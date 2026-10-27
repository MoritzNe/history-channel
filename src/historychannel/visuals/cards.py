"""Typographic cards (title, quote, fallback) rendered with PIL at 3840x2160."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .fonts import find_font
from .prep import OUT_SIZE, grade, texture

GOLD = (201, 164, 92)
GOLD_DIM = (150, 120, 66)
PARCHMENT = (232, 220, 192)
PARCHMENT_DIM = (196, 182, 152)
BG_CENTER = (44, 35, 26)
BG_EDGE = (12, 10, 8)

Font = ImageFont.FreeTypeFont | ImageFont.ImageFont


def background(size: tuple[int, int] = OUT_SIZE, *, seed: int = 0) -> Image.Image:
    """Dark warm radial gradient with subtle paper mottling."""
    w, h = size
    yy = np.linspace(-1, 1, h, dtype=np.float32)[:, None]
    xx = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
    r = np.clip(np.sqrt((xx * 0.85) ** 2 + (yy * 1.05) ** 2) / 1.3, 0, 1) ** 1.4
    c0, c1 = np.array(BG_CENTER, np.float32), np.array(BG_EDGE, np.float32)
    arr = c0 * (1 - r[..., None]) + c1 * r[..., None]
    arr += texture(size, seed=seed)[..., None] * 7.0
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8))


def _wrap(text: str, font: Font, max_w: float) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if cur and font.getlength(trial) > max_w:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        lines.append(cur)
    return lines


def _fit_text(
    text: str, max_w: float, max_lines: int, sizes: range, style: str
) -> tuple[Font, list[str]]:
    """Largest font size at which `text` wraps into <= max_lines within max_w."""
    font, lines = None, [text]
    for size in sizes:
        font = find_font(size, style)
        lines = _wrap(text, font, max_w)
        if len(lines) <= max_lines and all(font.getlength(ln) <= max_w for ln in lines):
            return font, lines
    assert font is not None
    return font, lines


def _line_h(font: Font) -> int:
    asc, desc = font.getmetrics() if hasattr(font, "getmetrics") else (font.size, 0)
    return asc + desc


def _draw_lines(draw, lines, font, cx, y, fill, spacing=1.18, shadow=True) -> int:
    lh = int(_line_h(font) * spacing)
    for ln in lines:
        w = font.getlength(ln)
        if shadow:
            draw.text((cx - w / 2 + 4, y + 6), ln, font=font, fill=(0, 0, 0, 140))
        draw.text((cx - w / 2, y), ln, font=font, fill=fill)
        y += lh
    return y


def _tracked(draw, text, font, cx, y, fill, tracking=0.12):
    """Draw letter-spaced text centred on cx."""
    extra = font.size * tracking if hasattr(font, "size") else 4
    widths = [font.getlength(ch) for ch in text]
    total = sum(widths) + extra * (len(text) - 1)
    x = cx - total / 2
    for ch, cw in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill)
        x += cw + extra


def _rule(draw, cx, y, half_w, color=GOLD, width=5, diamond=18):
    gap = diamond * 2.2
    draw.line([(cx - half_w, y), (cx - gap, y)], fill=color, width=width)
    draw.line([(cx + gap, y), (cx + half_w, y)], fill=color, width=width)
    d = diamond
    draw.polygon([(cx, y - d), (cx + d, y), (cx, y + d), (cx - d, y)], fill=color)
    for sx in (-1, 1):  # little end dots
        ex = cx + sx * (half_w + 16)
        draw.ellipse([ex - 6, y - 6, ex + 6, y + 6], fill=color)


def _finish(img: Image.Image, out: Path, seed: int) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    grade(img.convert("RGB"), warmth=0.0, desaturate=0.0, vignette=0.22, grain=2.5, seed=seed).save(out)
    return out


def _text_layer(size):
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    return layer, ImageDraw.Draw(layer)


def _composite(bg: Image.Image, layer: Image.Image) -> Image.Image:
    # soften the shadow pass slightly by blurring a copy underneath
    glow = layer.filter(ImageFilter.GaussianBlur(2))
    out = Image.alpha_composite(bg.convert("RGBA"), glow)
    return Image.alpha_composite(out, layer)


def render_title_card(title: str, subtitle: str | None, out: Path, *, seed: int = 0) -> Path:
    W, H = OUT_SIZE
    bg = background(OUT_SIZE, seed=seed)
    layer, d = _text_layer(OUT_SIZE)
    cx = W / 2

    tfont, tlines = _fit_text(title, W * 0.78, 2, range(250, 110, -10), "Regular")
    sfont, slines = (None, [])
    if subtitle:
        sfont, slines = _fit_text(subtitle, W * 0.66, 2, range(100, 56, -4), "Italic")

    t_h = len(tlines) * int(_line_h(tfont) * 1.12)
    s_h = len(slines) * int(_line_h(sfont) * 1.25) if sfont else 0
    gap = 130
    total = t_h + (gap * 2 + s_h if sfont else gap)
    y = (H - total) / 2 - 40

    y = _draw_lines(d, tlines, tfont, cx, y, PARCHMENT, spacing=1.12)
    rule_y = y + gap * 0.55
    _rule(d, cx, rule_y, min(W * 0.22, max(tfont.getlength(ln) for ln in tlines) * 0.45))
    if sfont:
        _draw_lines(d, slines, sfont, cx, rule_y + gap * 0.75, GOLD, spacing=1.25)
    return _finish(_composite(bg, layer), out, seed)


def render_quote_card(quote: str, attribution: str | None, out: Path, *, seed: int = 0) -> Path:
    W, H = OUT_SIZE
    bg = background(OUT_SIZE, seed=seed)
    layer, d = _text_layer(OUT_SIZE)
    cx = W / 2

    quote = quote.strip().strip('"“”')
    qfont, qlines = _fit_text(quote, W * 0.68, 7, range(140, 70, -6), "Italic")
    afont = find_font(60, "Regular")
    mark = find_font(420, "Regular")

    lh = int(_line_h(qfont) * 1.28)
    block = len(qlines) * lh + (200 if attribution else 0)
    y = (H - block) / 2 + 60

    # big opening quote mark, gold, above the text
    mw = mark.getlength("“")
    d.text((cx - mw / 2, y - 330), "“", font=mark, fill=GOLD_DIM)

    y = _draw_lines(d, qlines, qfont, cx, y, PARCHMENT, spacing=1.28)
    if attribution:
        a = attribution.strip().lstrip("-—– ")
        y += 70
        d.line([(cx - 90, y), (cx + 90, y)], fill=GOLD, width=4)
        _tracked(d, a.upper(), afont, cx, y + 50, GOLD, tracking=0.14)
    return _finish(_composite(bg, layer), out, seed)


def render_fallback_card(text: str, out: Path, *, seed: int = 0) -> Path:
    """Quiet placeholder used when no image could be obtained: the text on a dark card."""
    W, H = OUT_SIZE
    bg = background(OUT_SIZE, seed=seed)
    layer, d = _text_layer(OUT_SIZE)
    text = (text or "").strip() or "…"
    font, lines = _fit_text(text, W * 0.64, 4, range(120, 60, -6), "Italic")
    lh = int(_line_h(font) * 1.25)
    y = (H - len(lines) * lh) / 2
    _rule(d, W / 2, y - 110, W * 0.08, color=GOLD_DIM, width=3, diamond=12)
    y = _draw_lines(d, lines, font, W / 2, y, PARCHMENT_DIM, spacing=1.25)
    _rule(d, W / 2, y + 70, W * 0.08, color=GOLD_DIM, width=3, diamond=12)
    return _finish(_composite(bg, layer), out, seed)
