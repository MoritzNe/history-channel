"""Image preparation: fit any still to a 16:9 frame and apply the channel's house grade."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from historychannel.models import FRAME_H, FRAME_W

# Stills are produced at 2x the video frame so Ken Burns zooms stay sharp.
OUT_W, OUT_H = FRAME_W * 2, FRAME_H * 2
OUT_SIZE = (OUT_W, OUT_H)
TARGET_ASPECT = OUT_W / OUT_H

# Images whose aspect is within this band of 16:9 get a gentle cover-crop; everything
# else (portraits, panoramas) is composed over a blurred copy of itself.
COVER_MIN, COVER_MAX = 1.5, 2.05


def to_rgb(img: Image.Image) -> Image.Image:
    """Flatten any mode (P, LA, RGBA, I;16, CMYK, ...) onto RGB; respect EXIF rotation."""
    img = ImageOps.exif_transpose(img)
    if img.mode in ("I;16", "I;16B", "I;16L", "I"):
        arr = np.asarray(img, dtype=np.float32)
        arr = 255 * (arr - arr.min()) / max(1.0, float(arr.max() - arr.min()))
        img = Image.fromarray(arr.astype(np.uint8), "L")
    if img.mode in ("RGBA", "LA", "P", "PA"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (24, 20, 16, 255))
        img = Image.alpha_composite(bg, img)
    return img.convert("RGB")


def _cover(img: Image.Image, size: tuple[int, int], centering=(0.5, 0.4)) -> Image.Image:
    # Slightly above-centre crop: faces and skylines tend to sit in the upper half.
    return ImageOps.fit(img, size, Image.Resampling.LANCZOS, centering=centering)


def _contain(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.contain(img, size, Image.Resampling.LANCZOS)


def blurred_backdrop(img: Image.Image, size: tuple[int, int] = OUT_SIZE) -> Image.Image:
    """Enlarged, heavily blurred, darkened copy of `img` filling `size`."""
    small = _cover(img, (size[0] // 8, size[1] // 8))
    small = small.filter(ImageFilter.GaussianBlur(9))
    arr = np.asarray(small, dtype=np.float32)
    lum = arr.mean(axis=2, keepdims=True)
    arr = (0.5 * arr + 0.5 * lum) * 0.42  # desaturate + darken so the subject pops
    small = Image.fromarray(arr.clip(0, 255).astype(np.uint8))
    return small.resize(size, Image.Resampling.BICUBIC).filter(ImageFilter.GaussianBlur(6))


def fit_frame(img: Image.Image, size: tuple[int, int] = OUT_SIZE) -> Image.Image:
    """Fit an image to the 16:9 frame without cutting the subject off.

    Near-16:9 images are cover-cropped; others are centred (with a soft shadow) over a
    blurred, darkened enlargement of themselves -- the standard documentary look.
    """
    img = to_rgb(img)
    aspect = img.width / img.height
    if COVER_MIN <= aspect <= COVER_MAX:
        return _cover(img, size)

    canvas = blurred_backdrop(img, size)
    margin = int(size[1] * 0.045)
    fg = _contain(img, (size[0] - 2 * margin, size[1] - 2 * margin))
    x, y = (size[0] - fg.width) // 2, (size[1] - fg.height) // 2

    # soft drop shadow
    shadow = Image.new("L", size, 0)
    pad = int(size[1] * 0.012)
    shadow.paste(150, (x - pad, y + pad // 2, x + fg.width + pad, y + fg.height + pad * 2))
    shadow = shadow.filter(ImageFilter.GaussianBlur(size[1] * 0.02))
    canvas.paste((0, 0, 0), (0, 0), shadow)
    canvas.paste(fg, (x, y))
    return canvas


def grade(
    img: Image.Image,
    *,
    warmth: float = 0.16,
    desaturate: float = 0.12,
    vignette: float = 0.28,
    grain: float = 3.0,
    seed: int = 0,
) -> Image.Image:
    """Subtle house grade: warm/sepia tint, slight desaturation, vignette, fine grain."""
    arr = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
    h, w, _ = arr.shape

    lum = arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    arr = arr * (1 - desaturate) + lum[..., None] * desaturate
    sepia = np.stack([lum * 1.07, lum * 0.95, lum * 0.78], axis=-1)
    arr = arr * (1 - warmth) + sepia * warmth
    # lift blacks a touch, roll off highlights (filmic, never pure black/white)
    arr = 0.025 + arr * 0.955

    if vignette > 0:
        yy = np.linspace(-1, 1, h, dtype=np.float32)[:, None]
        xx = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
        r = np.sqrt((xx * 0.9) ** 2 + yy**2) / np.sqrt(0.81 + 1)
        mask = 1 - vignette * np.clip((r - 0.35) / 0.65, 0, 1) ** 2
        arr *= mask[..., None]

    arr = arr * 255.0
    if grain > 0:
        rng = np.random.default_rng(seed)
        arr += rng.normal(0, grain, (h, w, 1)).astype(np.float32)
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8))


def prepare_still(img: Image.Image, *, seed: int = 0) -> Image.Image:
    """Full prep for an archival/generated still: fit to 3840x2160 + grade."""
    return grade(fit_frame(img), seed=seed)


def texture(size: tuple[int, int] = OUT_SIZE, *, seed: int = 0, strength: float = 1.0) -> np.ndarray:
    """Low-frequency 'paper' mottling in [-1, 1] (h, w) -- used for card/map backgrounds."""
    rng = np.random.default_rng(seed)
    w, h = size
    out = np.zeros((h, w), dtype=np.float32)
    for scale, amp in ((64, 0.55), (16, 0.3), (4, 0.15)):
        small = rng.normal(0, 1, (max(2, h // (scale * 2)), max(2, w // (scale * 2)))).astype(np.float32)
        layer = Image.fromarray(small, mode="F").resize((w, h), Image.Resampling.BICUBIC)
        out += amp * np.asarray(layer)
    out /= max(1e-6, float(np.abs(out).max()))
    return out * strength
