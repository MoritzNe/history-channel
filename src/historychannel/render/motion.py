"""Ken Burns motion: a view rectangle per frame, sampled with sub-pixel precision.

Why not ffmpeg ``zoompan``: it rounds the crop origin to whole input pixels on every
frame, so slow zooms visibly "stair-step" (the well-known zoompan jitter). We instead
compute a float crop box per frame and let Pillow's convolution resampler
(``Image.resize(box=...)``) sample it. Pillow computes filter weights from the float box,
so a 0.3 px move per frame really is a 0.3 px move, and the downscale (source is ~2x
the output) is properly antialiased.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from historychannel.models import Motion

ZOOM_RANGE = (1.02, 1.14)  # zoom_in 1.02 -> 1.14 (starting at exactly 2x downscale trips 8-bit rounding in Pillow)
PAN_ZOOM = 1.10  # pans run at a fixed zoom so there is margin to travel through
PAN_TRAVEL = 0.85  # fraction of the available margin a pan uses
SUPERSAMPLE = 1.5  # source is resampled once to this multiple of the output size


def ease_in_out(u: float) -> float:
    """Cosine ease. Slightly blended with linear so motion never fully stops mid-shot."""
    u = min(1.0, max(0.0, u))
    return 0.8 * (0.5 - 0.5 * math.cos(math.pi * u)) + 0.2 * u


def view_box(motion: Motion, u: float, src_w: int, src_h: int) -> tuple[float, float, float, float]:
    """Crop box (x0, y0, x1, y1) in source pixels for progress ``u`` in [0, 1]."""
    e = ease_in_out(u)
    cx, cy = src_w / 2.0, src_h / 2.0
    if motion == "zoom_in":
        z = ZOOM_RANGE[0] + (ZOOM_RANGE[1] - ZOOM_RANGE[0]) * e
    elif motion == "zoom_out":
        z = ZOOM_RANGE[1] - (ZOOM_RANGE[1] - ZOOM_RANGE[0]) * e
    elif motion in ("pan_left", "pan_right", "pan_up", "pan_down"):
        z = PAN_ZOOM
        mx = (src_w - src_w / z) / 2.0 * PAN_TRAVEL
        my = (src_h - src_h / z) / 2.0 * PAN_TRAVEL
        s = 2.0 * e - 1.0  # -1 -> +1
        # "pan_left" = camera moves left, so the window travels right -> left.
        if motion == "pan_left":
            cx -= mx * s
        elif motion == "pan_right":
            cx += mx * s
        elif motion == "pan_up":
            cy -= my * s
        else:
            cy += my * s
    else:  # static
        z = 1.0
    w, h = src_w / z, src_h / z
    return (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)


@dataclass
class PreparedImage:
    """Source still cropped to the output aspect and scaled to SUPERSAMPLE x output."""

    image: Image.Image

    @classmethod
    def load(cls, path: Path, out_w: int, out_h: int) -> PreparedImage:
        im = Image.open(path)
        im = im.convert("RGB")
        # cover-crop to the output aspect ratio
        target = out_w / out_h
        w, h = im.size
        if abs(w / h - target) > 1e-3:
            if w / h > target:
                nw = round(h * target)
                im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
            else:
                nh = round(w / target)
                im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
        sw, sh = round(out_w * SUPERSAMPLE), round(out_h * SUPERSAMPLE)
        if im.size != (sw, sh):
            im = im.resize((sw, sh), Image.LANCZOS)
        return cls(im)

    def frame(self, motion: Motion, u: float, out_w: int, out_h: int) -> np.ndarray:
        box = view_box(motion, u, *self.image.size)
        return np.asarray(self.image.resize((out_w, out_h), Image.BICUBIC, box=box))
