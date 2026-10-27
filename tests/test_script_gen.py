"""Script generation with a fake Anthropic client (no network)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from historychannel.models import VideoScript
from historychannel.script import generate as gen
from historychannel.script import llm as llm_mod
from historychannel.script.generate import PROMPT_NAMES, generate_script, load_prompt, render_prompt
from historychannel.script.llm import ScriptGenError

SCRIPT = {
    "slug": "ignored-by-validator",
    "title": "The Night Berlin Built Barricades",
    "description": "In March 1848 Berlin rose against its king.",
    "tags": ["1848", "Berlin", "revolution"],
    "language": "en",
    "scenes": [
        {
            "id": "s01",
            "narration": "On the night of the eighteenth of March, Berliners pried up the cobblestones.",
            "mood": "tense",
            "visuals": [
                {"kind": "archival", "query": "Barrikadenkampf Berlin 1848", "motion": "zoom_in"},
                {
                    "kind": "map",
                    "map": {
                        "title": "Berlin, 1848",
                        "extent": [13.2, 13.6, 52.4, 52.6],
                        "markers": [{"name": "Berliner Schloss", "lon": 13.402, "lat": 52.517}],
                    },
                },
            ],
            "sources": ["https://example.org/1848"],
        },
        {
            "id": "s02",
            "narration": "By morning the king had blinked.",
            "visuals": [
                {"kind": "title_card", "text": "18 March 1848"},
                {"kind": "quote", "text": "To my dear Berliners", "subtext": "Frederick William IV, 19 March 1848"},
            ],
        },
    ],
    "sources": ["https://example.org/1848"],
}


def _text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def _tool(name: str, data: Any, id_: str = "toolu_1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=data)


def _msg(content: list, stop: str = "end_turn") -> SimpleNamespace:
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=50,
        cache_read_input_tokens=10,
        cache_creation_input_tokens=5,
        server_tool_use=SimpleNamespace(web_search_requests=1),
    )
    return SimpleNamespace(content=content, stop_reason=stop, usage=usage, stop_details=None)


class _Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class FakeClient:
    """Mimics `client.beta.messages.stream(...)`; picks a response by the pass heading."""

    def __init__(self, scores=(60, 90), first_script_invalid=False, pause_research=False):
        self.calls: list[dict[str, Any]] = []
        self.scores = list(scores)
        self.first_script_invalid = first_script_invalid
        self.pause_research = pause_research
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kw):
        self.calls.append(copy.deepcopy(kw))
        return _Stream(self._respond(kw["messages"]))

    def _respond(self, messages):
        user = messages[0]["content"]
        prompt = user if isinstance(user, str) else user[-1]["text"]
        last = messages[-1]
        if "Pass 1: Research" in prompt:
            if self.pause_research and last["role"] == "user":
                return _msg([SimpleNamespace(type="server_tool_use", id="srv_1", name="web_search", input={})], "pause_turn")
            return _msg([_text("# Dossier\n\nFacts [1].\n")])
        if "Pass 2: Outline" in prompt:
            return _msg([_text("# Outline\n\n★ The Night Berlin Built Barricades\n")])
        if "Pass 4: Harsh editor" in prompt:
            score = self.scores.pop(0)
            return _msg([_tool("submit_critique", {"score": score, "issues": [{"problem": "slow middle"}], "must_fix": ["Cut s02 filler"]})], "tool_use")
        if "Pass 5: Fact-check" in prompt:
            fixed = copy.deepcopy(SCRIPT)
            fixed["scenes"][1]["narration"] = "By morning, the king had given way."
            return _msg([_text("## Summary\n\n3 supported.\n"), _tool("submit_script", fixed)], "tool_use")
        # draft / rewrite
        if self.first_script_invalid and last["role"] == "user" and isinstance(last["content"], list) and len(messages) == 1:
            self.first_script_invalid = False
            return _msg([_tool("submit_script", {"title": "x", "scenes": [{"id": "s01", "narration": "hi", "visuals": [{"kind": "map"}]}]}, "toolu_bad")], "tool_use")
        return _msg([_tool("submit_script", SCRIPT)], "tool_use")


@pytest.fixture
def out(tmp_path: Path) -> Path:
    return tmp_path / "berlin-1848"


def test_prompt_files_load_and_render():
    for name in PROMPT_NAMES:
        text = load_prompt(name)
        assert len(text) > 200, name
    r = render_prompt("draft", topic="Berlin 1848", minutes="8", language="en", words=1200, slug="berlin-1848")
    assert "Berlin 1848" in r and "1200" in r and "$topic" not in r
    assert "little did they know" in load_prompt("system")


def test_full_run_writes_every_pass(out: Path):
    client = FakeClient(scores=(60, 90))
    script = generate_script("Berlin 1848", minutes=1, out_dir=out, client=client, model="claude-opus-5-5")

    for f in ("research.md", "outline.md", "draft.json", "critique_1.json", "rewrite_1.json",
              "critique_2.json", "factcheck.md", "script.json", "usage.json"):
        assert (out / f).exists(), f
    assert not (out / "rewrite_2.json").exists()  # score 90 >= threshold stops the loop

    assert isinstance(script, VideoScript)
    assert script.slug == "berlin-1848"  # slug forced from out_dir, not the model's
    assert script.scenes[1].narration == "By morning, the king had given way."
    assert VideoScript.load(out / "script.json") == script
    assert json.loads((out / "critique_1.json").read_text())["score"] == 60
    assert "Summary" in (out / "factcheck.md").read_text()

    # research, outline, draft, critique1, rewrite1, critique2, factcheck
    assert len(client.calls) == 7
    usage = json.loads((out / "usage.json").read_text())
    assert usage["input_tokens"] == 700 and usage["web_searches"] == 7 and usage["estimated_usd"] > 0


def test_request_shape_caching_and_stable_tools(out: Path):
    client = FakeClient(scores=(95,))
    generate_script("Berlin 1848", minutes=1, out_dir=out, client=client, model="claude-opus-5-5")
    first = client.calls[0]
    assert first["model"] == "claude-opus-5-5"
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert first["thinking"] == {"type": "adaptive"}
    assert first["tool_choice"] == {"type": "auto"}  # forced tool_choice 400s on Opus 5.5
    assert first["fallbacks"] == "default" and first["betas"] == ["server-side-fallback-2026-07-01"]
    names = [t["name"] for t in first["tools"]]
    assert names == ["web_search", "submit_critique", "submit_script"]
    assert first["tools"][0]["type"] == "web_search_20260209"
    # Same tools + system in every pass -> shared cacheable prefix
    for c in client.calls[1:]:
        assert c["tools"] == first["tools"] and c["system"] == first["system"]
    # Later passes start with the cached research dossier block
    draft_call = client.calls[2]
    blocks = draft_call["messages"][0]["content"]
    assert blocks[0]["text"].startswith("<research_dossier>") and "cache_control" in blocks[0]
    # submit_script schema is the VideoScript schema
    sub = next(t for t in first["tools"] if t["name"] == "submit_script")
    assert sub["input_schema"] == VideoScript.model_json_schema()


def test_invalid_script_gets_repaired_via_tool_result(out: Path):
    client = FakeClient(scores=(95,), first_script_invalid=True)
    generate_script("Berlin 1848", minutes=1, out_dir=out, client=client, model="claude-opus-5-5")
    repair = [c for c in client.calls if len(c["messages"]) == 3]
    assert repair, "expected a repair round-trip"
    tr = repair[0]["messages"][2]["content"][0]
    assert tr["type"] == "tool_result" and tr["is_error"] and tr["tool_use_id"] == "toolu_bad"
    assert "Validation failed" in tr["content"]


def test_resume_skips_completed_passes(out: Path, monkeypatch):
    generate_script("Berlin 1848", minutes=1, out_dir=out, client=FakeClient(scores=(95,)), model="claude-opus-5-5")

    class Boom:
        @property
        def beta(self):
            raise AssertionError("no API call expected on a fully cached run")

    script = generate_script("Berlin 1848", minutes=1, out_dir=out, client=Boom(), model="claude-opus-5-5")
    assert script.slug == "berlin-1848"

    # Deleting the final files re-runs only the fact-check pass.
    (out / "script.json").unlink()
    client = FakeClient()
    generate_script("Berlin 1848", minutes=1, out_dir=out, client=client, model="claude-opus-5-5")
    assert len(client.calls) == 1 and "Pass 5" in client.calls[0]["messages"][0]["content"][-1]["text"]


def test_pause_turn_is_resumed_without_extra_user_message(out: Path):
    client = FakeClient(scores=(95,), pause_research=True)
    generate_script("Berlin 1848", minutes=1, out_dir=out, client=client, model="claude-opus-5-5")
    resumed = client.calls[1]
    assert [m["role"] for m in resumed["messages"]] == ["user", "assistant"]
    assert (out / "research.md").read_text().startswith("# Dossier")


def test_refusal_raises(out: Path):
    class Refuser(FakeClient):
        def _respond(self, messages):
            return _msg([], "refusal")

    with pytest.raises(ScriptGenError, match="refusal"):
        generate_script("x", minutes=1, out_dir=out, client=Refuser(), model="claude-opus-5-5")


def test_missing_api_key_gives_clear_error(monkeypatch, out: Path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    with pytest.raises(ScriptGenError, match="ANTHROPIC_API_KEY"):
        generate_script("x", minutes=1, out_dir=out)


def test_model_from_env(monkeypatch):
    monkeypatch.setenv("HC_MODEL", "claude-sonnet-5-5")
    assert llm_mod.model_id() == "claude-sonnet-5-5"
    monkeypatch.setenv("HC_MODEL", "")
    assert llm_mod.model_id() == "claude-opus-5-5"


def test_validate_script_rejects_bad_visuals():
    bad = copy.deepcopy(SCRIPT)
    bad["scenes"][0]["visuals"][1] = {"kind": "map"}
    with pytest.raises(ValueError, match="kind=map requires"):
        gen.validate_script(bad, slug="s", language="en")
    bad = copy.deepcopy(SCRIPT)
    bad["scenes"][1]["visuals"][1]["subtext"] = None
    with pytest.raises(ValueError, match="attribution"):
        gen.validate_script(bad, slug="s", language="en")


def test_cli_args_parse():
    import argparse

    args = gen.add_cli_arguments(argparse.ArgumentParser()).parse_args(["Berlin 1848", "--minutes", "10"])
    assert args.topic == "Berlin 1848" and args.minutes == 10 and args.rounds == 2
    assert gen.slugify("Die Märzrevolution 1848!") == "die-marzrevolution-1848"
