"""House style shared by every animated shot: palette, fonts, easing, timing helpers.

Maps and cards both import from here so the channel reads as one visual system.
Colors are hex strings; use `rgba()` to get 0-255 tuples for skia/PIL.
"""

from __future__ import annotations

import math

# palette ------------------------------------------------------------------------
BG = "#16130f"  # card background (warm near-black)
BG_2 = "#221d16"  # raised panel
SEA = "#141b1f"
LAND = "#3b3427"
LAND_EDGE = "#8a7650"
BORDER = "#6f6046"
WATER_LINE = "#3c5560"
INK = "#efe3c6"  # primary text (parchment)
INK_DIM = "#b9ab8b"  # secondary text
GOLD = "#e0b860"  # accents, markers, rules
RED = "#c0492f"  # revolution / advance / emphasis
BLUE = "#5b7fa6"  # reaction / counter-revolution / Austria
GREEN = "#6f8f5f"
HALO = "#0d0b08"  # text halos and shadows

# categorical palette for regions, in order of use
REGION_COLORS = [RED, GOLD, BLUE, GREEN, "#8d6ab0", "#a0663a"]

SERIF_FAMILIES = ("Baskerville", "Hoefler Text", "Georgia", "Palatino", "Times New Roman")
SANS_FAMILIES = ("Avenir Next", "Avenir", "Helvetica Neue", "Helvetica")


def rgba(hex_color: str, alpha: float = 1.0) -> tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), round(255 * max(0.0, min(1.0, alpha)))


# easing -------------------------------------------------------------------------
def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def ease_in_out(x: float) -> float:
    x = clamp01(x)
    return 0.5 - 0.5 * math.cos(math.pi * x)


def ease_out_cubic(x: float) -> float:
    x = clamp01(x)
    return 1 - (1 - x) ** 3


def ease_out_back(x: float, overshoot: float = 1.70158) -> float:
    """Pop-in with a slight overshoot; good for markers and badges."""
    x = clamp01(x)
    c3 = overshoot + 1
    return 1 + c3 * (x - 1) ** 3 + overshoot * (x - 1) ** 2


def progress(t: float, duration: float, at: float | None, length: float) -> float:
    """0..1 progress of an element that starts at fraction `at` and animates for
    `length` seconds. Elements with at=None are fully shown from the start."""
    if at is None:
        return 1.0
    start = at * duration
    return clamp01((t - start) / max(length, 1e-6))


# standard durations (seconds) so motion feels consistent across shots
FADE = 0.6
POP = 0.45
DRAW = 1.2
SHOT_INTRO = 0.5  # nothing should start animating before this; the crossfade is still running
