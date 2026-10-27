"""Text overlays drawn with Pillow: lower-third captions and burned-in subtitles.

The Homebrew ffmpeg build has neither libass (``subtitles``/``ass`` filters) nor
freetype (``drawtext``), so all text is rasterised here and alpha-composited into the
frames in numpy. That also keeps typography identical across machines.

Layout (1080p reference, scaled with frame height):
  * subtitles: bottom-centre, white sans with soft shadow, <= 2 lines x 42 chars,
    bottom margin 60 px, so they occupy roughly y = 900..1020.
  * captions: lower third, left-aligned at x = 96, bar bottom at y = 0.80 H (864 px),
    i.e. always above the subtitle band - the two never collide.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from historychannel.models import SubtitleCue

MAX_CHARS = 42
MAX_LINES = 2

SERIF_CANDIDATES = [
    ("/System/Library/Fonts/Supplemental/Baskerville.ttc", 0),
    ("/System/Library/Fonts/Supplemental/Georgia.ttf", 0),
    ("/System/Library/Fonts/Times.ttc", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", 0),
]
SANS_CANDIDATES = [
    ("/System/Library/Fonts/Avenir Next.ttc", 5),  # Avenir Next Medium
    ("/System/Library/Fonts/HelveticaNeue.ttc", 0),
    ("/System/Library/Fonts/Helvetica.ttc", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
]


@lru_cache(maxsize=32)
def _font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for path, index in SERIF_CANDIDATES if kind == "serif" else SANS_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size, index=index)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


# --------------------------------------------------------------------------- overlay type


@dataclass
class Overlay:
    """Premultiplied RGBA sprite placed at (x, y) in the frame."""

    x: int
    y: int
    rgb: np.ndarray  # float32 HxWx3, premultiplied by alpha, 0..255
    alpha: np.ndarray  # float32 HxWx1, 0..1

    @classmethod
    def from_image(cls, im: Image.Image, x: int, y: int) -> Overlay:
        a = np.asarray(im, dtype=np.float32)
        alpha = a[..., 3:4] / 255.0
        return cls(x, y, a[..., :3] * alpha, alpha)

    def composite(self, frame: np.ndarray, opacity: float) -> None:
        """Blend into ``frame`` (uint8 HxWx3) in place."""
        if opacity <= 0.0:
            return
        fh, fw = frame.shape[:2]
        h, w = self.alpha.shape[:2]
        x0, y0 = max(self.x, 0), max(self.y, 0)
        x1, y1 = min(self.x + w, fw), min(self.y + h, fh)
        if x1 <= x0 or y1 <= y0:
            return
        sx, sy = x0 - self.x, y0 - self.y
        a = self.alpha[sy : sy + y1 - y0, sx : sx + x1 - x0] * opacity
        rgb = self.rgb[sy : sy + y1 - y0, sx : sx + x1 - x0] * opacity
        region = frame[y0:y1, x0:x1].astype(np.float32)
        region *= 1.0 - a
        region += rgb
        frame[y0:y1, x0:x1] = (region + 0.5).astype(np.uint8)


# --------------------------------------------------------------------------- captions


def render_caption(text: str, width: int, height: int) -> Overlay:
    s = height / 1080.0
    font = _font("serif", max(10, round(36 * s)))
    pad_x, pad_y = round(26 * s), round(14 * s)
    accent_w = max(2, round(4 * s))
    gap = round(18 * s)
    l, t, r, b = font.getbbox(text)
    tw, th = r - l, b - t
    bar_w = accent_w + gap + tw + pad_x
    bar_h = th + 2 * pad_y
    shadow = round(14 * s)
    im = Image.new("RGBA", (bar_w + 2 * shadow, bar_h + 2 * shadow), (0, 0, 0, 0))

    # soft drop shadow so the bar sits on bright images without a hard edge
    sh = Image.new("L", im.size, 0)
    ImageDraw.Draw(sh).rectangle((shadow, shadow, shadow + bar_w, shadow + bar_h), fill=90)
    sh = sh.filter(ImageFilter.GaussianBlur(shadow / 2))
    im.paste((0, 0, 0, 255), (0, 0), sh)

    d = ImageDraw.Draw(im)
    d.rectangle((shadow, shadow, shadow + bar_w, shadow + bar_h), fill=(14, 13, 12, 170))
    d.rectangle((shadow, shadow, shadow + accent_w, shadow + bar_h), fill=(198, 164, 98, 255))
    d.text(
        (shadow + accent_w + gap - l, shadow + pad_y - t),
        text,
        font=font,
        fill=(242, 236, 224, 255),
    )
    x = round(96 * s) - shadow
    y = round(height * 0.80) - bar_h - shadow
    return Overlay.from_image(im, x, y)


def caption_opacity(t: float, start: float, end: float) -> float:
    """Fade in from start+0.5 s over 0.5 s; fade out over the 0.5 s ending 0.6 s before end."""
    dur = end - start
    if dur < 2.0:
        return 0.0
    fade = 0.5
    t_in = start + min(0.5, dur * 0.15)
    t_out = end - min(0.6, dur * 0.15)
    if t < t_in or t > t_out:
        return 0.0
    v = min((t - t_in) / fade, (t_out - t) / fade, 1.0)
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, v))


# --------------------------------------------------------------------------- subtitles


def wrap_lines(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Greedy wrap into lines of <= max_chars (a single overlong word stays whole)."""
    lines: list[str] = []
    cur = ""
    for word in text.split():
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= max_chars:
            cur += " " + word
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _balance_two(words: list[str]) -> list[str]:
    """Split words into two lines minimising the longer line."""
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        score = max(len(a), len(b))
        if best is None or score < best[0]:
            best = (score, [a, b])
    return best[1] if best else [" ".join(words)]


def layout_cue(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Lines for one on-screen cue (already known to fit into MAX_LINES)."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return [text]
    return _balance_two(text.split())


def split_cues(cues: list[SubtitleCue], max_chars: int = MAX_CHARS) -> list[SubtitleCue]:
    """Re-wrap cues; cues longer than 2 x max_chars are split in time by character share.

    Returned cue text uses ``\\n`` between lines.
    """
    out: list[SubtitleCue] = []
    for cue in cues:
        text = " ".join(cue.text.split())
        if not text or cue.end <= cue.start:
            continue
        lines = wrap_lines(text, max_chars)
        chunks = [" ".join(lines[i : i + MAX_LINES]) for i in range(0, len(lines), MAX_LINES)]
        total = sum(len(c) for c in chunks)
        t = cue.start
        for c in chunks:
            d = (cue.end - cue.start) * len(c) / total
            out.append(SubtitleCue(start=t, end=t + d, text="\n".join(layout_cue(c, max_chars))))
            t += d
    return out


def _srt_time(t: float) -> str:
    ms = max(0, round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues: list[SubtitleCue], path: Path) -> Path:
    parts = []
    for i, c in enumerate(cues, 1):
        parts.append(f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n{c.text}\n")
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def render_subtitle(text: str, width: int, height: int) -> Overlay:
    s = height / 1080.0
    font = _font("sans", max(10, round(44 * s)))
    lines = text.split("\n")
    line_h = round(58 * s)
    stroke = max(1, round(2 * s))
    blur = max(1.0, 4 * s)
    margin = round(24 * s)
    widths = [font.getbbox(ln, stroke_width=stroke)[2] for ln in lines]
    w = max(widths) + 2 * margin
    h = line_h * len(lines) + 2 * margin
    ascent = font.getmetrics()[0]

    shadow = Image.new("L", (w, h), 0)
    sd = ImageDraw.Draw(shadow)
    text_mask = Image.new("L", (w, h), 0)
    td = ImageDraw.Draw(text_mask)
    for i, (ln, lw) in enumerate(zip(lines, widths)):
        x = (w - lw) // 2
        y = margin + i * line_h + (line_h - ascent) // 2
        sd.text((x, y + round(2 * s)), ln, font=font, fill=255, stroke_width=stroke + 1, stroke_fill=255)
        td.text((x, y), ln, font=font, fill=255)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    # darken the shadow mask so it reads as a soft halo, not a box
    shadow = shadow.point(lambda v: int(v * 0.85))

    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    im.paste((0, 0, 0, 255), (0, 0), shadow)
    im.paste((255, 255, 255, 255), (0, 0), text_mask)
    x = (width - w) // 2
    y = height - round(60 * s) - h + margin
    return Overlay.from_image(im, x, y)


def subtitle_opacity(t: float, start: float, end: float) -> float:
    if t < start or t >= end:
        return 0.0
    fade = min(0.12, (end - start) / 4)
    return min(1.0, (t - start) / fade, (end - t) / fade) if fade > 0 else 1.0
