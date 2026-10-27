"""Drawing kit for the animated info-graphic cards (skia).

Everything is laid out in a *logical* 1920x1080 space; `render()` scales the canvas by
height/1080 so a 960x540 preview and a 3840x2160 poster look identical. Layout (text
measuring and wrapping) happens in `prepare()` in the main process using the same skia
fonts, so what is measured is exactly what is drawn.
"""

from __future__ import annotations

import functools
import math
import threading
from pathlib import Path

import numpy as np
import skia
from PIL import Image

from . import style as S

LW, LH = 1920.0, 1080.0  # logical frame
BG_SIZE = (2112, 1188)  # pre-rendered background, 10% oversize so it can drift

# --------------------------------------------------------------------- fonts

_ROLES = {
    "serif": (S.SERIF_FAMILIES, skia.FontStyle.Normal()),
    "serif_italic": (S.SERIF_FAMILIES, skia.FontStyle.Italic()),
    "serif_bold": (S.SERIF_FAMILIES, skia.FontStyle.Bold()),
    "sans": (S.SANS_FAMILIES, skia.FontStyle.Normal()),
    "sans_medium": (S.SANS_FAMILIES, skia.FontStyle(500, 5, skia.FontStyle.kUpright_Slant)),
}


@functools.cache
def typeface(role: str) -> skia.Typeface:
    families, fs = _ROLES[role]
    mgr = skia.FontMgr()
    for fam in families:
        tf = mgr.matchFamilyStyle(fam, fs)
        if tf is not None:
            return tf
    return skia.Typeface.MakeDefault()


@functools.lru_cache(maxsize=1024)
def font(role: str, size: float) -> skia.Font:
    f = skia.Font(typeface(role), float(size))
    f.setSubpixel(True)
    f.setEdging(skia.Font.Edging.kAntiAlias)
    f.setHinting(skia.FontHinting.kNone)
    f.setLinearMetrics(True)
    return f


def measure(text: str, role: str, size: float, tracking: float = 0.0) -> float:
    w = font(role, size).measureText(text)
    if tracking and len(text) > 1:
        w += tracking * size * (len(text) - 1)
    return w


def metrics(role: str, size: float) -> tuple[float, float]:
    """(ascent, descent) as positive numbers."""
    m = font(role, size).getMetrics()
    return -m.fAscent, m.fDescent


def cap_height(role: str, size: float) -> float:
    m = font(role, size).getMetrics()
    return m.fCapHeight or 0.7 * size


def wrap(text: str, role: str, size: float, max_w: float, tracking: float = 0.0) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if cur and measure(trial, role, size, tracking) > max_w:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        lines.append(cur)
    return [ln for ln in lines if ln] or [""]


def fit(text: str, role: str, sizes, max_w: float, max_lines: int,
        tracking: float = 0.0) -> tuple[float, list[str]]:
    """Largest size at which `text` wraps into <= max_lines lines no wider than max_w.
    If even the smallest size fails, long words are allowed to shrink further."""
    size, lines = None, [text]
    for size in sizes:
        lines = wrap(text, role, size, max_w, tracking)
        if len(lines) <= max_lines and all(measure(ln, role, size, tracking) <= max_w for ln in lines):
            return float(size), lines
    assert size is not None
    # still too long: shrink below the size range until it fits (never drops text)
    for _ in range(40):
        size *= 0.94
        lines = wrap(text, role, size, max_w, tracking)
        if len(lines) <= max_lines and all(measure(ln, role, size, tracking) <= max_w for ln in lines):
            break
    return float(size), lines


def word_layout(lines: list[str], role: str, size: float, cx: float, y0: float, lh: float,
                align: str = "center", x0: float = 0.0) -> list[dict]:
    """Per-word positions for word-by-word reveals: [{"t": word, "x", "y", "line"}] (baselines)."""
    out = []
    space = measure(" ", role, size)
    for li, ln in enumerate(lines):
        w = measure(ln, role, size)
        x = cx - w / 2 if align == "center" else x0
        y = y0 + li * lh
        for word in ln.split():
            out.append({"t": word, "x": round(x, 2), "y": round(y, 2), "line": li})
            x += measure(word, role, size) + space
    return out


def text_box(text: str, role: str, size: float, x: float, y: float, align: str = "left",
             tracking: float = 0.0) -> list[float]:
    """[x0, y0, x1, y1] of a single line drawn with baseline y."""
    w = measure(text, role, size, tracking)
    a, d = metrics(role, size)
    xl = x - w / 2 if align == "center" else x - w if align == "right" else x
    return [round(xl, 1), round(y - a, 1), round(xl + w, 1), round(y + d, 1)]


# --------------------------------------------------------------------- paints

@functools.lru_cache(maxsize=256)
def _rgb(hex_color: str) -> tuple[float, float, float]:
    r, g, b, _ = S.rgba(hex_color)
    return r / 255, g / 255, b / 255


def color(hex_color: str, alpha: float = 1.0) -> skia.Color4f:
    r, g, b = _rgb(hex_color)
    return skia.Color4f(r, g, b, max(0.0, min(1.0, alpha)))


def mix(a: str, b: str, k: float) -> str:
    ra, rb = S.rgba(a), S.rgba(b)
    k = S.clamp01(k)
    return "#" + "".join(f"{round(x + (y - x) * k):02x}" for x, y in zip(ra[:3], rb[:3]))


def paint(hex_color: str, alpha: float = 1.0, *, stroke: float | None = None, blur: float = 0.0,
          cap: str = "round") -> skia.Paint:
    p = skia.Paint(AntiAlias=True)
    p.setColor4f(color(hex_color, alpha))
    if stroke is not None:
        p.setStyle(skia.Paint.kStroke_Style)
        p.setStrokeWidth(stroke)
        p.setStrokeCap(skia.Paint.kRound_Cap if cap == "round" else skia.Paint.kButt_Cap)
    if blur > 0:
        p.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, blur))
    return p


def draw_text(c: skia.Canvas, text: str, x: float, y: float, role: str, size: float,
              hex_color: str = S.INK, alpha: float = 1.0, *, align: str = "left",
              tracking: float = 0.0, shadow: float = 0.0) -> None:
    """One line, baseline at y. `tracking` is extra letter spacing in ems."""
    if alpha <= 0.003 or not text:
        return
    f = font(role, size)
    w = measure(text, role, size, tracking)
    if align == "center":
        x -= w / 2
    elif align == "right":
        x -= w
    if shadow > 0:
        sp = paint(S.HALO, alpha * shadow, blur=max(1.0, size * 0.045))
        _draw_run(c, text, x, y + size * 0.035, f, sp, tracking * size)
    _draw_run(c, text, x, y, f, paint(hex_color, alpha), tracking * size)


def _draw_run(c, text, x, y, f, p, extra):
    if not extra:
        c.drawString(text, x, y, f, p)
        return
    for ch in text:
        c.drawString(ch, x, y, f, p)
        x += f.measureText(ch) + extra


def rule(c: skia.Canvas, cx: float, y: float, half_w: float, p: float, *, hex_color: str = S.GOLD,
         alpha: float = 1.0, width: float = 2.2, diamond: float = 7.0, dots: bool = True) -> None:
    """Gold rule that draws outward from a central diamond (house ornament)."""
    if p <= 0 or alpha <= 0:
        return
    pd = S.ease_out_back(S.clamp01(p / 0.45))
    pl = S.ease_out_cubic(S.clamp01((p - 0.1) / 0.9))
    gap = diamond * 2.4
    if diamond > 0 and pd > 0:
        d = diamond * pd
        path = skia.Path()
        path.moveTo(cx, y - d)
        path.lineTo(cx + d, y)
        path.lineTo(cx, y + d)
        path.lineTo(cx - d, y)
        path.close()
        c.drawPath(path, paint(hex_color, alpha))
    else:
        gap = 0
    length = (half_w - gap) * pl
    if length > 0.5:
        lp = paint(hex_color, alpha, stroke=width, cap="butt")
        c.drawLine(cx - gap, y, cx - gap - length, y, lp)
        c.drawLine(cx + gap, y, cx + gap + length, y, lp)
    if dots and pl > 0.9:
        a = alpha * (pl - 0.9) / 0.1
        r = width * 1.5
        c.drawCircle(cx - half_w - r * 3, y, r, paint(hex_color, a))
        c.drawCircle(cx + half_w + r * 3, y, r, paint(hex_color, a))


def hline(c, x0, y, length, p, *, hex_color=S.GOLD, alpha=1.0, width=2.2):
    """Rule that draws left -> right from x0."""
    pl = S.ease_out_cubic(p)
    if pl > 0 and alpha > 0:
        c.drawLine(x0, y, x0 + length * pl, y, paint(hex_color, alpha, stroke=width, cap="butt"))


def diamond(c, x, y, r, hex_color=S.GOLD, alpha=1.0):
    if r <= 0 or alpha <= 0:
        return
    path = skia.Path()
    path.moveTo(x, y - r)
    path.lineTo(x + r, y)
    path.lineTo(x, y + r)
    path.lineTo(x - r, y)
    path.close()
    c.drawPath(path, paint(hex_color, alpha))


# --------------------------------------------------------------------- images

@functools.lru_cache(maxsize=16)
def load_image(path: str) -> skia.Image:
    with Image.open(path) as im:
        arr = np.asarray(im.convert("RGBA"))
    return skia.Image.fromarray(
        np.ascontiguousarray(arr), colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kOpaque_AlphaType
    ).makeRasterImage()


SAMPLING = skia.SamplingOptions(skia.FilterMode.kLinear)
SAMPLING_HQ = skia.SamplingOptions(skia.CubicResampler.Mitchell())


def make_background(out: Path, *, seed: int, vignette: float = 0.3, tint: Image.Image | None = None,
                    tint_amount: float = 0.0) -> Path:
    """Warm dark card background (same look as the static cards), oversize for drift."""
    from historychannel.visuals.cards import background
    from historychannel.visuals.prep import blurred_backdrop, grade

    img = background(BG_SIZE, seed=seed)
    if tint is not None and tint_amount > 0:
        back = blurred_backdrop(tint, BG_SIZE)
        img = Image.blend(img, back, tint_amount)
    img = grade(img, warmth=0.0, desaturate=0.0, vignette=vignette, grain=2.0, seed=seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, compress_level=1)
    return out


def draw_background(c: skia.Canvas, path: str | None, t: float, d: float, lw: float, lh: float,
                    seed: int = 0) -> None:
    """Very slow push-in + lateral drift so the card never looks frozen."""
    if not path or not Path(path).exists():
        c.clear(color(S.BG))
        return
    img = load_image(path)
    u = S.clamp01(t / max(d, 1e-6))
    amount = min(0.05, 0.006 * max(d, 1.0))  # total zoom over the shot, ~speed-limited
    z = 1.0 + amount * S.ease_in_out(u)
    direction = 1 if seed % 2 == 0 else -1
    cover = max(lw / img.width(), lh / img.height()) * 1.03 * z
    w, h = img.width() * cover, img.height() * cover
    slack_x, slack_y = w - lw, h - lh
    dx = slack_x * (0.5 + direction * 0.35 * (u - 0.5))
    dy = slack_y * (0.5 + 0.2 * (u - 0.5))
    c.drawImageRect(img, skia.Rect.MakeXYWH(-dx, -dy, w, h), SAMPLING)


@functools.lru_cache(maxsize=64)
def _motes(seed: int, n: int):
    rng = np.random.default_rng(seed + 9001)
    return [
        (
            float(rng.uniform(0, LW)), float(rng.uniform(0, LH)),
            float(rng.uniform(-6, 6)), float(rng.uniform(-9, -2)),  # px/s, drifting up
            float(rng.uniform(1.2, 3.4)), float(rng.uniform(0.04, 0.13)),
            float(rng.uniform(0, 2 * math.pi)),
        )
        for _ in range(n)
    ]


def draw_motes(c: skia.Canvas, seed: int, t: float, lw: float, lh: float, alpha: float = 1.0,
               n: int = 22) -> None:
    """Sparse floating dust in front of the background: the foreground parallax layer."""
    if alpha <= 0:
        return
    for x0, y0, vx, vy, r, a, ph in _motes(seed, n):
        x = (x0 + vx * t) % (lw + 40) - 20
        y = (y0 + vy * t) % (lh + 40) - 20
        flicker = 0.75 + 0.25 * math.sin(ph + t * 0.9)
        c.drawCircle(x, y, r, paint(S.INK, a * alpha * flicker, blur=r * 0.9))


# --------------------------------------------------------------------- render

_tls = threading.local()


def _surface(width: int, height: int):
    cache = getattr(_tls, "surfaces", None)
    if cache is None:
        cache = _tls.surfaces = {}
    hit = cache.get((width, height))
    if hit is None:
        if len(cache) > 3:
            cache.clear()
        arr = np.zeros((height, width, 4), np.uint8)
        surf = skia.Surface(arr, colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kPremul_AlphaType)
        hit = cache[(width, height)] = (arr, surf)
    return hit


class Ctx:
    """Per-frame drawing context in logical units."""

    __slots__ = ("c", "t", "d", "lw", "lh", "ox", "k")

    def __init__(self, c, t, d, lw, lh, k):
        self.c, self.t, self.d, self.lw, self.lh, self.k = c, t, d, lw, lh, k
        self.ox = (lw - LW) / 2  # horizontal offset when the frame is not 16:9

    def p(self, start: float, length: float) -> float:
        """Linear 0..1 progress of an element starting at `start` seconds."""
        return S.clamp01((self.t - start) / max(length, 1e-6))


def time_scale(d: float, nominal: float) -> float:
    """Compress choreography for shots shorter than `nominal` seconds."""
    return max(0.45, min(1.0, d / nominal))


def render(anim: dict, t: float, duration: float, width: int, height: int, draw) -> np.ndarray:
    d = max(float(duration), 1e-3)
    t = max(0.0, min(float(t), d))
    s = height / LH
    lw = width / s
    arr, surf = _surface(width, height)
    c = surf.getCanvas()
    c.save()
    c.scale(s, s)
    seed = int(anim.get("seed", 0))
    draw_background(c, anim.get("bg"), t, d, lw, LH, seed)
    draw_motes(c, seed, t, lw, LH, anim.get("motes", 1.0))
    ctx = Ctx(c, t, d, lw, LH, time_scale(d, anim.get("nominal", 6.0)))
    c.translate(ctx.ox, 0)
    draw(anim, ctx)
    c.restore()
    surf.flushAndSubmit()
    return np.ascontiguousarray(arr[..., :3])
