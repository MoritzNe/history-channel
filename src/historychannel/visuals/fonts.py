"""Tiny system-font finder for PIL (macOS first, degrades to PIL's default font)."""

from __future__ import annotations

import functools
from pathlib import Path

from PIL import ImageFont

FONT_DIRS = [
    Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
    Path("/usr/share/fonts"),  # linux, just in case
]

# Preference order for the channel's serif look.
SERIF_FAMILIES = ("Baskerville", "Hoefler Text", "Georgia", "Palatino", "Times New Roman", "Times")


@functools.cache
def _font_index() -> dict[tuple[str, str], tuple[str, int]]:
    """Map (family, style) -> (path, ttc index), lower-cased keys."""
    index: dict[tuple[str, str], tuple[str, int]] = {}
    for d in FONT_DIRS:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
                continue
            for i in range(16 if p.suffix.lower() == ".ttc" else 1):
                try:
                    fam, style = ImageFont.truetype(str(p), 12, index=i).getname()
                except Exception:
                    break
                index.setdefault(((fam or "").lower(), (style or "").lower()), (str(p), i))
    return index


def find_font(
    size: int,
    style: str = "Regular",
    families: tuple[str, ...] = SERIF_FAMILIES,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Return the first available font of the given families/style at `size` px.

    `style` is e.g. "Regular", "Italic", "Bold", "SemiBold". Falls back to Regular of the
    same family, then to the next family, then to PIL's bundled default font.
    """
    idx = _font_index()
    for fam in families:
        for st in (style, "Regular", "Roman", ""):
            hit = idx.get((fam.lower(), st.lower()))
            if hit:
                return ImageFont.truetype(hit[0], size, index=hit[1])
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()
