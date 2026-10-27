"""Frame-by-frame animated shots: maps, stats, timelines, comparisons, name cards, titles.

Each animated kind lives in its own module exposing two functions:

    prepare(visual: Visual, out_dir: Path, *, index: int) -> Asset
        One-time heavy work in the main process (render base map, fetch a portrait,
        lay out text). Returns an Asset whose `path` is the final-state poster frame
        and whose `animation` dict is a JSON-serializable spec containing "kind".

    frame(anim: dict, t: float, duration: float, width: int, height: int) -> np.ndarray
        Pure function of time, called from render worker processes. Returns an
        HxWx3 uint8 RGB array. `t` is seconds into the shot and may fall slightly
        outside [0, duration] during crossfades; clamp it. Cache expensive loads
        (base images, fonts) per process with functools.lru_cache keyed on paths.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import numpy as np

from historychannel.models import Asset, Visual

# visual.kind -> module under historychannel.animate
_MODULES = {
    "map": "maps",
    "stat": "cards",
    "timeline": "cards",
    "compare": "cards",
    "person": "cards",
    "title_card": "cards",
    "quote": "cards",
}

ANIMATED_KINDS = frozenset(_MODULES)


def _module(kind: str):
    return importlib.import_module(f"historychannel.animate.{_MODULES[kind]}")


def prepare(visual: Visual, out_dir: Path, *, index: int) -> Asset:
    return _module(visual.kind).prepare(visual, out_dir, index=index)


def render_frame(anim: dict, t: float, duration: float, width: int, height: int) -> np.ndarray:
    return _module(anim["kind"]).frame(anim, t, duration, width, height)
