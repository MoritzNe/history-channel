"""Visuals stage: turn each `Visual` of a script into a 3840x2160 still + attribution.

Public API:
    resolve_visual(visual, out_dir, *, index, script_title=None) -> Asset
    attribution_lines(assets) -> list[str]

`resolve_visual` never raises for a single bad image: archival -> search by query ->
tasteful fallback card (attribution None).
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

from historychannel.models import Asset, Visual

from .cards import render_fallback_card, render_quote_card, render_title_card
from .prep import OUT_H, OUT_SIZE, OUT_W, prepare_still

log = logging.getLogger(__name__)

__all__ = ["resolve_visual", "attribution_lines", "OUT_W", "OUT_H", "OUT_SIZE"]

_client = None


def _commons():
    global _client
    if _client is None:
        from .wikimedia import CommonsClient

        _client = CommonsClient()
    return _client


def _save_still(src: Path, out: Path, seed: int) -> Path:
    with Image.open(src) as im:
        im.load()
        prepare_still(im, seed=seed).save(out, quality=92, subsampling=0)
    return out


def _archival(visual: Visual, out: Path, seed: int) -> Asset | None:
    from .wikimedia import CommonsError

    client = _commons()
    attempts = []
    if visual.file:
        attempts.append(("file", visual.file))
    if visual.query:
        attempts.append(("query", visual.query))
    for how, value in attempts:
        try:
            img = client.fetch(value) if how == "file" else client.search_best(value)
            _save_still(img.path, out, seed)
            return Asset(path=out, kind="archival", attribution=img.attribution, source_url=img.page_url)
        except (CommonsError, OSError, ValueError) as e:
            log.warning("visual %s: archival %s %r failed: %s", out.name, how, value, e)
        except Exception as e:  # never let one image kill the run
            log.exception("visual %s: unexpected error for %s %r: %s", out.name, how, value, e)
    return None


def _generated(visual: Visual, out: Path, seed: int) -> Asset | None:
    from .imagegen import generate_cached, get_provider, styled

    provider = get_provider()
    if provider is None or not visual.prompt:
        return None
    try:
        src = generate_cached(provider, styled(visual.prompt))
        _save_still(src, out, seed)
        return Asset(path=out, kind="generated", attribution=f"AI-generated illustration ({provider.name})")
    except Exception as e:
        log.warning("visual %s: image generation failed: %s", out.name, e)
        return None


def _fallback(visual: Visual, out_dir: Path, index: int, script_title: str | None) -> Asset:
    text = visual.caption or visual.text or visual.query or visual.prompt or script_title or ""
    out = out_dir / f"{index:03d}_fallback.png"
    try:
        render_fallback_card(text, out, seed=index)
    except Exception:
        log.exception("fallback card failed; writing plain frame")
        Image.new("RGB", OUT_SIZE, (18, 15, 12)).save(out)
    return Asset(path=out, kind="fallback", attribution=None)


def resolve_visual(
    visual: Visual, out_dir: Path, *, index: int, script_title: str | None = None
) -> Asset:
    """Produce the still for one visual in `out_dir` (named `<index>_<kind>.<ext>`)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    kind = visual.kind
    stem = out_dir / f"{index:03d}_{kind}"
    try:
        if kind == "title_card":
            text = visual.text or script_title or ""
            p = render_title_card(text, visual.subtext, stem.with_suffix(".png"), seed=index)
            return Asset(path=p, kind=kind)
        if kind == "quote":
            if not visual.text:
                raise ValueError("quote visual without text")
            p = render_quote_card(visual.text, visual.subtext, stem.with_suffix(".png"), seed=index)
            return Asset(path=p, kind=kind)
        if kind == "map":
            if visual.map is None:
                raise ValueError("map visual without map spec")
            from .maps import render_map

            p = render_map(visual.map, stem.with_suffix(".png"), seed=index)
            return Asset(path=p, kind=kind, attribution="Map data: Natural Earth (public domain)")
        if kind == "generated":
            asset = _generated(visual, stem.with_suffix(".jpg"), index)
            if asset:
                return asset
            # no provider / failure: treat as archival search on `query`
            if visual.query or visual.file:
                asset = _archival(visual, out_dir / f"{index:03d}_archival.jpg", index)
                if asset:
                    return asset
        if kind == "archival":
            asset = _archival(visual, stem.with_suffix(".jpg"), index)
            if asset:
                return asset
    except Exception as e:
        log.exception("visual %03d (%s) failed: %s", index, kind, e)
    return _fallback(visual, out_dir, index, script_title)


def attribution_lines(assets: list[Asset]) -> list[str]:
    """Deduplicated credit lines (order of first appearance) for the video description."""
    seen: set[str] = set()
    lines: list[str] = []
    for a in assets:
        if not a.attribution:
            continue
        line = a.attribution + (f" ({a.source_url})" if a.source_url else "")
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return lines
