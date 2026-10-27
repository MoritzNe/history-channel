"""Multi-pass script generation: research -> outline -> draft -> critique loop -> fact-check.

Every pass writes its result into `out_dir`; re-running skips passes whose output already
exists (delete a file, or pass `force=True`, to redo it):

    research.md      pass 1, Claude + server-side web search
    outline.md       pass 2, titles / thumbnail / central question / acts
    draft.json       pass 3, first VideoScript
    critique_<n>.json, rewrite_<n>.json   pass 4, harsh-editor loop (max `critique_rounds`)
    factcheck.md     pass 5 report
    script.json      final VideoScript (after fact-check fixes)
    usage.json       token / search usage and an estimated cost
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import unicodedata
from pathlib import Path
from string import Template
from typing import Any

from pydantic import BaseModel, Field

from historychannel.models import VideoScript

from .llm import LLM, ScriptGenError, web_search_tool

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).with_name("prompts")
PROMPT_NAMES = ("system", "research", "outline", "draft", "critique", "rewrite", "factcheck")

WORDS_PER_MINUTE = 150
DEFAULT_THRESHOLD = 80
DEFAULT_ROUNDS = 2

# USD per million tokens / per search, for the cost estimate in usage.json.
PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
CACHE_READ_FACTOR = 0.05  # Opus 5.5: $0.20 read vs $4 input
CACHE_WRITE_FACTOR = 1.25  # 5-minute TTL write
WEB_SEARCH_USD = 10.0 / 1000


# --------------------------------------------------------------------------- prompts


def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise ScriptGenError(f"Prompt file missing: {path}")
    return path.read_text(encoding="utf-8")


def render_prompt(name: str, **values: Any) -> str:
    return Template(load_prompt(name)).safe_substitute({k: str(v) for k, v in values.items()})


# --------------------------------------------------------------------------- critique model


class CritiqueIssue(BaseModel):
    scene_id: str | None = None
    # hook | clarity | structure | characters | retention | economy | language | accuracy | visuals | length
    category: str = "clarity"
    problem: str
    suggestion: str = ""


class RetentionRisk(BaseModel):
    minute: float
    risk: str


class Critique(BaseModel):
    score: int = Field(ge=0, le=100)
    issues: list[CritiqueIssue] = Field(default_factory=list)
    must_fix: list[str] = Field(default_factory=list)
    retention_risks: list[RetentionRisk] = Field(default_factory=list)
    cut: list[str] = Field(default_factory=list)
    cliches: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)


def build_tools(model: str) -> list[dict[str, Any]]:
    """The constant tool list used by every pass (stable prefix for prompt caching)."""
    return [
        web_search_tool(model),
        {
            "name": "submit_critique",
            "description": "Submit the editor's critique of the current script. Call exactly once in a critique pass.",
            "input_schema": Critique.model_json_schema(),
            "eager_input_streaming": True,
        },
        {
            "name": "submit_script",
            "description": (
                "Submit the complete narrated video script (all scenes with narration and visual plan). "
                "Call exactly once in a draft, rewrite or fact-check pass."
            ),
            "input_schema": VideoScript.model_json_schema(),
            "eager_input_streaming": True,
        },
    ]


# --------------------------------------------------------------------------- helpers


def slugify(text: str, max_len: int = 60) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:max_len].rstrip("-") or "video"


def word_count(script: VideoScript) -> int:
    return sum(len(s.narration.split()) for s in script.scenes)


def script_warnings(script: VideoScript, minutes: float) -> list[str]:
    """Soft checks that are fed to the critic rather than rejected outright."""
    warn: list[str] = []
    target = minutes * WORDS_PER_MINUTE
    wc = word_count(script)
    if abs(wc - target) > 0.2 * target:
        warn.append(f"Length: {wc} narration words vs target ~{int(target)} ({wc / WORDS_PER_MINUTE:.1f} min).")
    kinds: list[str] = []
    for s in script.scenes:
        if not 1 <= len(s.visuals) <= 5:
            warn.append(f"Scene {s.id}: {len(s.visuals)} visuals (want 1-5).")
        n = len(s.narration.split())
        if n > 170:
            warn.append(f"Scene {s.id}: {n} words, longer than ~60 s; consider splitting.")
        secs = n / WORDS_PER_MINUTE * 60
        total_w = sum(max(v.weight, 0.05) for v in s.visuals) or 1.0
        for i, v in enumerate(s.visuals):
            kinds.append(v.kind)
            shot = secs * max(v.weight, 0.05) / total_w
            limit = {"map": 18, "compare": 15, "timeline": 15}.get(v.kind, 12)
            if shot > limit:
                warn.append(f"Scene {s.id} visual {i} ({v.kind}): ~{shot:.0f} s on screen; split it or add a shot.")
            if v.caption and v.kind != "archival":
                warn.append(f"Scene {s.id} visual {i}: caption on a {v.kind} shot (captions are for archival stills).")
            if v.map is not None:
                ats = [x.at for xs in (v.map.markers, v.map.arrows, v.map.regions, v.map.texts) for x in xs if x.at is not None]
                if ats and min(ats) < 0.05:
                    warn.append(f"Scene {s.id} visual {i}: first map animation at {min(ats):.2f}; start at 0.05 or later.")
    if kinds:
        share = {k: kinds.count(k) / len(kinds) for k in set(kinds)}
        if share.get("map", 0) < 0.55:
            warn.append(f"Shot mix: {share.get('map', 0):.0%} maps (want at least 55%).")
        if share.get("archival", 0) > 0.20:
            warn.append(f"Shot mix: {share.get('archival', 0):.0%} archival (want at most 20%).")
        if "timeline" not in share:
            warn.append("No recurring timeline shot at the act transitions.")
    return warn


def validate_script(data: dict[str, Any], *, slug: str, language: str) -> VideoScript:
    """Strict validation: pydantic schema plus the invariants downstream stages rely on."""
    data = dict(data)
    data["slug"] = slug  # never let the model rename the output folder
    data.setdefault("language", language)
    script = VideoScript.model_validate(data)
    errors: list[str] = []
    if not script.scenes:
        errors.append("script has no scenes")
    if len(script.title) > 100:
        errors.append(f"title is {len(script.title)} characters; YouTube allows 100")
    ids = [s.id for s in script.scenes]
    if len(set(ids)) != len(ids):
        errors.append("scene ids must be unique")
    for s in script.scenes:
        if not s.narration.strip():
            errors.append(f"scene {s.id}: empty narration")
        if not s.visuals:
            errors.append(f"scene {s.id}: no visuals")
        for i, v in enumerate(s.visuals):
            where = f"scene {s.id} visual {i}"
            if v.kind == "map" and v.map is None:
                errors.append(f"{where}: kind=map requires `map`")
            if v.kind in ("title_card", "quote") and not (v.text or "").strip():
                errors.append(f"{where}: kind={v.kind} requires `text`")
            if v.kind == "quote" and not (v.subtext or "").strip():
                errors.append(f"{where}: quote requires attribution in `subtext`")
            if v.kind == "archival" and not (v.file or v.query):
                errors.append(f"{where}: archival requires `file` or `query`")
            if v.kind == "generated" and not (v.prompt or v.query):
                errors.append(f"{where}: generated requires `prompt`")
            if v.kind == "stat" and not (v.value or "").strip():
                errors.append(f"{where}: stat requires `value`")
            if v.kind == "timeline" and not v.events:
                errors.append(f"{where}: timeline requires `events`")
            if v.kind == "compare" and len(v.columns) != 2:
                errors.append(f"{where}: compare requires exactly two `columns`")
            if v.kind == "person" and not ((v.file or v.query) and (v.text or "").strip()):
                errors.append(f"{where}: person requires a portrait `file` or `query` and the name in `text`")
            if v.map is not None:
                extents = [v.map.extent] + ([v.map.camera_to] if v.map.camera_to else [])
                for lon0, lon1, lat0, lat1 in extents:
                    if not (-180 <= lon0 < lon1 <= 180 and -90 <= lat0 < lat1 <= 90):
                        errors.append(f"{where}: map extent/camera_to must be (lon_min, lon_max, lat_min, lat_max)")
                for m in v.map.markers:
                    if not (-180 <= m.lon <= 180 and -90 <= m.lat <= 90):
                        errors.append(f"{where}: marker {m.name!r} has invalid coordinates")
                timed = [*v.map.markers, *v.map.arrows, *v.map.regions, *v.map.texts, *v.map.stamps]
                if any(x.at is not None and not 0 <= x.at <= 1 for x in timed):
                    errors.append(f"{where}: map `at` values are fractions of the shot and must be within 0-1")
    if errors:
        raise ValueError("; ".join(errors))
    return script


def _text_block(text: str, cache: bool = False) -> dict[str, Any]:
    b: dict[str, Any] = {"type": "text", "text": text}
    if cache:
        b["cache_control"] = {"type": "ephemeral"}
    return b


def _context_blocks(research: str, outline: str | None = None) -> list[dict[str, Any]]:
    """Research (cached) then outline: the shared message prefix for passes 2-5."""
    blocks = [_text_block(f"<research_dossier>\n{research}\n</research_dossier>", cache=True)]
    if outline is not None:
        blocks.append(_text_block(f"<outline>\n{outline}\n</outline>", cache=True))
    return blocks


def _script_json(script: VideoScript) -> str:
    return script.model_dump_json(indent=1, exclude_none=True)


# --------------------------------------------------------------------------- orchestration


class ScriptGenerator:
    def __init__(
        self,
        topic: str,
        *,
        minutes: float = 8,
        language: str = "en",
        out_dir: Path,
        slug: str | None = None,
        client: Any = None,
        model: str | None = None,
        critique_rounds: int = DEFAULT_ROUNDS,
        threshold: int = DEFAULT_THRESHOLD,
        force: bool = False,
    ) -> None:
        self.topic = topic
        self.minutes = minutes
        self.language = language
        self.out_dir = Path(out_dir)
        self.slug = slug or slugify(self.out_dir.name if self.out_dir.name else topic)
        self.words = int(round(minutes * WORDS_PER_MINUTE))
        self.rounds = critique_rounds
        self.threshold = threshold
        self.force = force
        self._client = client
        self._model = model
        self._llm: LLM | None = None

    # The LLM (and therefore the API key check) is only created when a pass actually runs,
    # so a fully cached run works offline.
    @property
    def llm(self) -> LLM:
        if self._llm is None:
            kw: dict[str, Any] = {}
            if self._model:
                kw["model"] = self._model
            # Build with a placeholder tool list, then set tools for the resolved model.
            self._llm = LLM(system_prompt=load_prompt("system"), tools=[], client=self._client, **kw)
            self._llm.tools = build_tools(self._llm.model)
        return self._llm

    def _path(self, name: str) -> Path:
        return self.out_dir / name

    def _cached(self, name: str) -> Path | None:
        p = self._path(name)
        return p if p.exists() and not self.force else None

    def _common(self) -> dict[str, Any]:
        return dict(topic=self.topic, minutes=f"{self.minutes:g}", language=self.language, words=self.words, slug=self.slug)

    def _validate(self, data: dict[str, Any]) -> VideoScript:
        return validate_script(data, slug=self.slug, language=self.language)

    # ---------------------------------------------------------------- passes

    def research(self) -> str:
        if p := self._cached("research.md"):
            return p.read_text(encoding="utf-8")
        log.info("pass 1/5: research")
        text = self.llm.text(render_prompt("research", **self._common()))
        self._path("research.md").write_text(text, encoding="utf-8")
        return text

    def outline(self, research: str) -> str:
        if p := self._cached("outline.md"):
            return p.read_text(encoding="utf-8")
        log.info("pass 2/5: outline")
        content = _context_blocks(research) + [_text_block(render_prompt("outline", **self._common()))]
        text = self.llm.text(content)
        self._path("outline.md").write_text(text, encoding="utf-8")
        return text

    def draft(self, research: str, outline: str) -> VideoScript:
        if p := self._cached("draft.json"):
            return VideoScript.load(p)
        log.info("pass 3/5: draft")
        content = _context_blocks(research, outline) + [_text_block(render_prompt("draft", **self._common()))]
        script, _ = self.llm.structured(content, "submit_script", self._validate)
        script.save(self._path("draft.json"))
        return script

    def critique(self, research: str, outline: str, script: VideoScript, round_: int) -> Critique:
        name = f"critique_{round_}.json"
        if p := self._cached(name):
            return Critique.model_validate_json(p.read_text(encoding="utf-8"))
        log.info("pass 4/5: critique round %d", round_)
        warnings = script_warnings(script, self.minutes)
        checks = ""
        if warnings:
            checks = "\nAutomatic checks flagged:\n" + "\n".join(f"- {w}" for w in warnings) + "\n"
        prompt = render_prompt(
            "critique",
            **self._common(),
            round=round_,
            actual_words=word_count(script),
            scene_count=len(script.scenes),
            automatic_checks=checks,
            script=_script_json(script),
        )
        content = _context_blocks(research, outline) + [_text_block(prompt)]
        crit, _ = self.llm.structured(content, "submit_critique", Critique.model_validate)
        self._path(name).write_text(crit.model_dump_json(indent=2), encoding="utf-8")
        return crit

    def rewrite(self, research: str, outline: str, script: VideoScript, crit: Critique, round_: int) -> VideoScript:
        name = f"rewrite_{round_}.json"
        if p := self._cached(name):
            return VideoScript.load(p)
        log.info("pass 4/5: rewrite round %d (score %d)", round_, crit.score)
        prompt = render_prompt(
            "rewrite",
            **self._common(),
            round=round_,
            score=crit.score,
            actual_words=word_count(script),
            critique=crit.model_dump_json(indent=1),
            script=_script_json(script),
        )
        content = _context_blocks(research, outline) + [_text_block(prompt)]
        new, _ = self.llm.structured(content, "submit_script", self._validate)
        new.save(self._path(name))
        return new

    def factcheck(self, research: str, script: VideoScript) -> VideoScript:
        if (p := self._cached("script.json")) and self._path("factcheck.md").exists():
            return VideoScript.load(p)
        log.info("pass 5/5: fact-check")
        prompt = render_prompt("factcheck", **self._common(), script=_script_json(script))
        content = _context_blocks(research) + [_text_block(prompt)]
        fixed, report = self.llm.structured(content, "submit_script", self._validate)
        self._path("factcheck.md").write_text(report.strip() + "\n", encoding="utf-8")
        fixed.save(self._path("script.json"))
        return fixed

    # ---------------------------------------------------------------- driver

    def run(self) -> VideoScript:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        try:
            research = self.research()
            outline = self.outline(research)
            script = self.draft(research, outline)
            for n in range(1, self.rounds + 1):
                crit = self.critique(research, outline, script, n)
                if crit.score >= self.threshold:
                    log.info("critique round %d: score %d >= %d, stopping", n, crit.score, self.threshold)
                    break
                script = self.rewrite(research, outline, script, crit, n)
            return self.factcheck(research, script)
        finally:
            self._write_usage()

    def _write_usage(self) -> None:
        if self._llm is None:
            return
        u = self._llm.usage
        path = self._path("usage.json")
        prev: dict[str, Any] = {}
        if path.exists():
            try:
                prev = json.loads(path.read_text())
            except json.JSONDecodeError:
                prev = {}
        totals = {k: int(prev.get(k, 0)) + v for k, v in u.as_dict().items()}
        totals["model"] = self._llm.model
        totals["estimated_usd"] = round(estimate_cost(self._llm.model, totals), 4)
        path.write_text(json.dumps(totals, indent=2))


def estimate_cost(model: str, usage: dict[str, Any]) -> float:
    inp, out = PRICING.get(model, PRICING["claude-opus-5-5"])
    return (
        usage.get("input_tokens", 0) * inp
        + usage.get("cache_read_input_tokens", 0) * inp * CACHE_READ_FACTOR
        + usage.get("cache_creation_input_tokens", 0) * inp * CACHE_WRITE_FACTOR
        + usage.get("output_tokens", 0) * out
    ) / 1e6 + usage.get("web_searches", 0) * WEB_SEARCH_USD


# --------------------------------------------------------------------------- public API


def generate_script(
    topic: str,
    *,
    minutes: float = 8,
    language: str = "en",
    out_dir: Path,
    client: Any = None,
    model: str | None = None,
    critique_rounds: int = DEFAULT_ROUNDS,
    threshold: int = DEFAULT_THRESHOLD,
    force: bool = False,
) -> VideoScript:
    """Research and write a `VideoScript` for `topic`; all intermediate passes go to `out_dir`.

    Resumable: passes whose output file already exists are skipped unless `force=True`.
    `client` lets tests inject a fake Anthropic client; `model` overrides `HC_MODEL`.
    """
    return ScriptGenerator(
        topic,
        minutes=minutes,
        language=language,
        out_dir=Path(out_dir),
        client=client,
        model=model,
        critique_rounds=critique_rounds,
        threshold=threshold,
        force=force,
    ).run()


def default_out_dir(topic: str) -> Path:
    from historychannel import REPO_ROOT

    return REPO_ROOT / "content" / slugify(topic)


def add_cli_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Arguments for `hc script "<topic>"` (the coordinator can attach these to a subparser)."""
    parser.add_argument("topic", help="What the video is about, e.g. 'The Berlin March Revolution of 1848'")
    parser.add_argument("--minutes", type=float, default=8, help="Target video length (default 8)")
    parser.add_argument("--language", default="en", help="Narration language code (default en)")
    parser.add_argument("--out", type=Path, default=None, help="Output dir (default content/<slug>)")
    parser.add_argument("--model", default=None, help="Claude model id (default $HC_MODEL or claude-opus-5-5)")
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS, help="Max critique/rewrite rounds")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD, help="Critique score that ends the loop")
    parser.add_argument("--force", action="store_true", help="Redo passes even if their files exist")
    return parser


def run_from_args(args: argparse.Namespace) -> Path:
    """Run generation from parsed CLI args; returns the path of the final script.json."""
    out_dir = args.out or default_out_dir(args.topic)
    script = generate_script(
        args.topic,
        minutes=args.minutes,
        language=args.language,
        out_dir=out_dir,
        model=args.model,
        critique_rounds=args.rounds,
        threshold=args.threshold,
        force=args.force,
    )
    path = Path(out_dir) / "script.json"
    print(f"{script.title}\n{len(script.scenes)} scenes, {word_count(script)} words -> {path}")
    return path


def cli(argv: list[str] | None = None) -> int:
    """Standalone entry point: `python -m historychannel.script "<topic>"`."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = add_cli_arguments(argparse.ArgumentParser(prog="hc script", description=__doc__.splitlines()[0])).parse_args(argv)
    try:
        run_from_args(args)
    except ScriptGenError as e:
        print(f"error: {e}")
        return 1
    return 0
