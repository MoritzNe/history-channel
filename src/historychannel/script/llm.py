"""Thin wrapper around the Anthropic Messages API used by every script pass.

Design choices (see docs/tools-script.md):

* One **constant tool list** (web search + `submit_script` + `submit_critique`) and one
  **constant system prompt** for every pass, so `tools -> system -> research` forms a
  stable prefix that prompt caching can reuse across passes.
* Structured results come back through client tools whose `input_schema` is the pydantic
  JSON schema; we validate with pydantic and send validation errors back as an
  `is_error` tool_result so the model can repair its output. (Claude Opus 5.5 rejects
  forced `tool_choice`, so we use `auto` and instruct the model to call the tool.)
* Streaming + `get_final_message()` so long drafts don't hit HTTP timeouts.
* `pause_turn` (server-side web search loop hit its iteration cap) is resumed by
  re-sending the conversation unchanged.
* The `anthropic` package and `ANTHROPIC_API_KEY` are resolved lazily, so importing this
  module never fails and tests can inject a fake client.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"
WEB_SEARCH_TOOL_TYPE = "web_search_20260209"  # dynamic-filtering variant (Opus 4.6+/Sonnet 4.6+)
WEB_SEARCH_MAX_USES = 20

# Models that accept the server-side `fallbacks: "default"` refusal fallback.
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models older than the 4.6 generation only have the basic web search variant.
_BASIC_SEARCH_PREFIXES = ("claude-haiku-4", "claude-sonnet-4-5", "claude-opus-4-5", "claude-opus-4-1")

MAX_CONTINUATIONS = 6  # pause_turn resumes per call
MAX_REPAIRS = 2  # schema-validation repair rounds per structured call


class ScriptGenError(RuntimeError):
    """Raised for configuration problems or unrecoverable model output."""


def model_id() -> str:
    return os.environ.get("HC_MODEL", "").strip() or DEFAULT_MODEL


def effort() -> str:
    return os.environ.get("HC_EFFORT", "").strip() or DEFAULT_EFFORT


def make_client() -> Any:
    """Create an Anthropic client, with a clear message if it can't be done."""
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover - dependency is declared in pyproject
        raise ScriptGenError(
            "The 'anthropic' package is not installed. Run: uv pip install anthropic"
        ) from e
    try:
        from dotenv import load_dotenv

        from historychannel import REPO_ROOT

        load_dotenv(REPO_ROOT / ".env")
    except ImportError:  # pragma: no cover
        pass
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise ScriptGenError(
            "ANTHROPIC_API_KEY is not set. Add it to .env (see .env.example) or export it."
        )
    # Generous timeout: research turns with many web searches can run several minutes.
    return anthropic.Anthropic(timeout=900.0, max_retries=4)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    web_searches: int = 0

    def add(self, u: Any) -> None:
        if u is None:
            return
        for name in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            setattr(self, name, getattr(self, name) + int(getattr(u, name, 0) or 0))
        stu = getattr(u, "server_tool_use", None)
        if stu is not None:
            self.web_searches += int(getattr(stu, "web_search_requests", 0) or 0)

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass
class TurnResult:
    text: str
    tool_input: dict[str, Any] | None
    content: list[Any]
    stop_reason: str | None


def _block_type(b: Any) -> str | None:
    return b.get("type") if isinstance(b, dict) else getattr(b, "type", None)


def _get(b: Any, key: str) -> Any:
    return b.get(key) if isinstance(b, dict) else getattr(b, key, None)


@dataclass
class LLM:
    """Stateless per-call helper bound to one client, model, system prompt and tool set."""

    system_prompt: str
    tools: list[dict[str, Any]]
    client: Any = None
    model: str = field(default_factory=model_id)
    effort: str = field(default_factory=effort)
    max_tokens: int = 64000
    usage: Usage = field(default_factory=Usage)

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = make_client()

    # ------------------------------------------------------------------ request

    def _request_kwargs(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        kw: dict[str, Any] = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": self.system_prompt, "cache_control": {"type": "ephemeral"}}],
            tools=self.tools,
            tool_choice={"type": "auto"},
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            messages=messages,
        )
        if self.model in _FALLBACK_MODELS:
            kw["betas"] = [_FALLBACK_BETA]
            kw["fallbacks"] = "default"
        return kw

    def _send(self, messages: list[dict[str, Any]]) -> Any:
        # Retryable errors (429/5xx/connection) are retried by the SDK (max_retries).
        # Auth / bad-request / not-found errors propagate with the SDK's typed exceptions.
        with self.client.beta.messages.stream(**self._request_kwargs(messages)) as stream:
            msg = stream.get_final_message()
        self.usage.add(getattr(msg, "usage", None))
        return msg

    def run(self, messages: list[dict[str, Any]]) -> TurnResult:
        """Send `messages`, resuming `pause_turn` until the model finishes its turn.

        Mutates `messages` by appending the assistant turn(s), so callers can continue
        the conversation (e.g. with a tool_result).
        """
        msg = None
        turn: list[Any] = []  # all assistant blocks of this (possibly resumed) turn
        for _ in range(MAX_CONTINUATIONS + 1):
            msg = self._send(messages)
            turn.extend(msg.content)
            if messages and messages[-1]["role"] == "assistant":
                messages[-1] = {"role": "assistant", "content": list(turn)}
            else:
                messages.append({"role": "assistant", "content": list(turn)})
            if msg.stop_reason != "pause_turn":
                break
            # pause_turn: the server-side search loop hit its cap. Re-send as-is (no extra
            # "continue" message); the API resumes from the trailing server_tool_use block.
        assert msg is not None
        if msg.stop_reason == "refusal":
            details = getattr(msg, "stop_details", None)
            raise ScriptGenError(f"Model declined the request (refusal): {details}")
        if msg.stop_reason == "max_tokens":
            log.warning("Response hit max_tokens=%s; output may be truncated", self.max_tokens)
        text = "".join(_get(b, "text") or "" for b in turn if _block_type(b) == "text")
        tool_input = None
        for b in turn:
            if _block_type(b) == "tool_use":
                tool_input = _get(b, "input")
        return TurnResult(text=text, tool_input=tool_input, content=turn, stop_reason=msg.stop_reason)

    # ------------------------------------------------------------------ helpers

    def text(self, user_content: list[dict[str, Any]] | str) -> str:
        """Free-text pass (research, outline). Tools stay declared for cache stability."""
        messages = [{"role": "user", "content": user_content}]
        res = self.run(messages)
        if not res.text.strip():
            raise ScriptGenError("Model returned no text")
        return res.text

    def structured(
        self,
        user_content: list[dict[str, Any]] | str,
        tool_name: str,
        validate: Callable[[dict[str, Any]], Any],
    ) -> tuple[Any, str]:
        """Run a pass that must end with a call to client tool `tool_name`.

        Returns `(validated_object, accompanying_text)`. On validation failure the error is
        returned to the model as an `is_error` tool_result and it gets `MAX_REPAIRS` tries.
        """
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
        texts: list[str] = []
        for attempt in range(MAX_REPAIRS + 1):
            res = self.run(messages)
            texts.append(res.text)
            call = next(
                (b for b in res.content if _block_type(b) == "tool_use" and _get(b, "name") == tool_name),
                None,
            )
            if call is None:
                if attempt == MAX_REPAIRS:
                    break
                messages.append({"role": "user", "content": f"Now call the `{tool_name}` tool with the result."})
                continue
            raw = _get(call, "input")
            err: str | None = None
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError as e:
                    err = f"Tool input was not valid JSON: {e}"
            if err is None and not isinstance(raw, dict):
                err = "Tool input must be a JSON object."
            if err is None:
                try:
                    obj = validate(raw)
                except Exception as e:  # pydantic.ValidationError or a custom check
                    err = str(e)
                else:
                    return obj, "\n".join(t for t in texts if t)
            if attempt == MAX_REPAIRS:
                raise ScriptGenError(f"`{tool_name}` output failed validation after retries:\n{err}")
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": _get(call, "id"),
                            "is_error": True,
                            "content": f"Validation failed, fix and call `{tool_name}` again:\n{err[:4000]}",
                        }
                    ],
                }
            )
        raise ScriptGenError(f"Model never called `{tool_name}`")


def web_search_tool(model: str) -> dict[str, Any]:
    kind = "web_search_20250305" if model.startswith(_BASIC_SEARCH_PREFIXES) else WEB_SEARCH_TOOL_TYPE
    return {"type": kind, "name": "web_search", "max_uses": WEB_SEARCH_MAX_USES}
