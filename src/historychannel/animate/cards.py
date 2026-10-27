"""Animated info-graphic shots: stat, timeline, compare, person, title_card, quote.

`prepare()` lays out all text (skia measuring, logical 1920x1080 units), fetches and
grades the person portrait, pre-renders the drifting background texture and writes a
3840x2160 poster of the final state. `frame()` is a pure function of time that draws
the choreography with skia; see `_cardkit` for the shared primitives.

Choreography conventions: nothing moves before `style.SHOT_INTRO` (the crossfade);
seconds-based timings are compressed for short shots via `ctx.k`; everything has
settled by ~80% of the shot so the poster (t = duration) is the final state.
"""

from __future__ import annotations

import logging
import math
import re
from pathlib import Path

import numpy as np
import skia
from PIL import Image, ImageOps

from historychannel.models import Asset, Visual

from . import _cardkit as K
from . import style as S

log = logging.getLogger(__name__)

T0 = S.SHOT_INTRO
POSTER_DURATION = 10.0
POSTER_SIZE = (3840, 2160)
CX, CY = K.LW / 2, K.LH / 2

# ===================================================================== numbers

_NUM_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?")


def parse_value(value: str) -> dict:
    """Split a display value into prefix / number / suffix.

    "80,000" -> number 80000, sep ","; "c. 1 million" -> prefix "c. ", suffix " million";
    "1 in 3" -> number 1, suffix " in 3". `count` says whether counting up from zero
    reads well (not for years or single digits, which just scale in)."""
    value = (value or "").strip()
    m = _NUM_RE.search(value)
    if not m:
        return {"prefix": value, "number": None, "suffix": "", "decimals": 0, "sep": "", "count": False}
    int_part, dec_part = m.group(1), m.group(2) or ""
    number = float(int_part.replace(",", "") + (f".{dec_part}" if dec_part else ""))
    sep = "," if "," in int_part else ""
    prefix, suffix = value[: m.start()], value[m.end():]
    yearlike = not sep and not dec_part and len(int_part) == 4 and 1000 <= number <= 2100 and "%" not in suffix
    count = not yearlike and (number >= 10 or bool(dec_part))
    return {"prefix": prefix, "number": number, "suffix": suffix, "decimals": len(dec_part),
            "sep": sep, "count": count}


def format_value(spec: dict, x: float | None = None) -> str:
    """Render the value with the numeric part replaced by `x` (default: the final number)."""
    if spec.get("number") is None:
        return spec["prefix"]
    x = spec["number"] if x is None else x
    dec = spec["decimals"]
    if dec:
        body = f"{x:,.{dec}f}" if spec["sep"] else f"{x:.{dec}f}"
    else:
        n = math.floor(x + 1e-9)
        body = f"{n:,}" if spec["sep"] else str(n)
    return f"{spec['prefix']}{body}{spec['suffix']}"


def count_progress(t: float, start: float, length: float) -> float:
    return S.ease_out_cubic((t - start) / max(length, 1e-6))


# ===================================================================== prepare


def prepare(visual: Visual, out_dir: Path, *, index: int) -> Asset:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    kind = visual.kind
    builder = _PREPARE[kind]
    stem = out_dir / f"{index:03d}_{kind}"
    anim, attribution, source_url = builder(visual, stem, index)
    anim["kind"] = kind
    anim["seed"] = index
    if "bg" not in anim:
        anim["bg"] = str(K.make_background(stem.with_name(stem.name + "_bg.png"), seed=index,
                                           vignette=anim.pop("_vignette", 0.32)))
    anim.pop("_vignette", None)
    poster = stem.with_name(stem.name + "_poster.png")
    arr = frame(anim, POSTER_DURATION, POSTER_DURATION, *POSTER_SIZE)
    Image.fromarray(arr).save(poster, compress_level=3)
    return Asset(path=poster, kind=kind, animation=anim, attribution=attribution, source_url=source_url)


def frame(anim: dict, t: float, duration: float, width: int, height: int) -> np.ndarray:
    return K.render(anim, t, duration, width, height, _DRAW[anim["kind"]])


def _block_box(boxes: list[list[float]]) -> list[float]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _lines_boxes(lines, role, size, x, y0, lh, align="center"):
    return [K.text_box(ln, role, size, x, y0 + i * lh, align) for i, ln in enumerate(lines)]


# ------------------------------------------------------------------ title_card


def _prep_title(v: Visual, stem: Path, index: int):
    title = (v.text or "").strip() or "…"
    tsize, tlines = K.fit(title, "serif", range(122, 54, -4), K.LW * 0.78, 3)
    a, dsc = K.metrics("serif", tsize)
    tlh = (a + dsc) * 1.08
    ssize, slines = 0.0, []
    if v.subtext:
        ssize, slines = K.fit(v.subtext.strip(), "serif_italic", range(48, 26, -2), K.LW * 0.62, 2)
    slh = sum(K.metrics("serif_italic", ssize)) * 1.25 if slines else 0
    gap = 64
    block = len(tlines) * tlh + gap + (gap * 0.9 + len(slines) * slh if slines else 0)
    top = CY - block / 2 - 20
    y0 = top + a
    words = K.word_layout(tlines, "serif", tsize, CX, y0, tlh)
    title_bottom = y0 + (len(tlines) - 1) * tlh + dsc
    rule_y = title_bottom + gap * 0.62
    half = min(K.LW * 0.12, max(K.measure(ln, "serif", tsize) for ln in tlines) * 0.42)
    sub_y = rule_y + gap * 0.9 + K.metrics("serif_italic", ssize)[0] if slines else rule_y
    boxes = _lines_boxes(tlines, "serif", tsize, CX, y0, tlh)
    if slines:
        boxes += _lines_boxes(slines, "serif_italic", ssize, CX, sub_y, slh)
    return {
        "nominal": 5.0,
        "title": {"size": tsize, "words": words},
        "rule": {"y": rule_y, "half": half},
        "sub": {"size": ssize, "lines": slines, "y": sub_y, "lh": slh},
        "boxes": boxes,
    }, None, None


def _draw_title(anim: dict, x: K.Ctx) -> None:
    c, k = x.c, x.k
    # whole block breathes in very slowly
    z = 1.0 + 0.018 * S.ease_in_out(x.t / x.d)
    c.save()
    c.translate(CX, CY)
    c.scale(z, z)
    c.translate(-CX, -CY)
    T = anim["title"]
    words = T["words"]
    step = min(0.13, 1.4 / max(1, len(words))) * k
    for i, w in enumerate(words):
        p = x.p(T0 + 0.1 + i * step, 0.9 * k)
        e = S.ease_out_cubic(p)
        K.draw_text(c, w["t"], w["x"], w["y"] + 26 * (1 - e), "serif", T["size"], S.INK, e, shadow=0.6)
    t_rule = T0 + 0.1 + len(words) * step + 0.15 * k
    R = anim["rule"]
    K.rule(c, CX, R["y"], R["half"], x.p(t_rule, 1.3 * k), diamond=8)
    sub = anim["sub"]
    if sub["lines"]:
        p = S.ease_out_cubic(x.p(t_rule + 0.45 * k, 1.0 * k))
        for i, ln in enumerate(sub["lines"]):
            K.draw_text(c, ln, CX, sub["y"] + i * sub["lh"] + 14 * (1 - p), "serif_italic", sub["size"],
                        S.GOLD, p, align="center", shadow=0.4)
    c.restore()


# ------------------------------------------------------------------ quote


def _prep_quote(v: Visual, stem: Path, index: int):
    quote = (v.text or "").strip().strip('"“”„').strip()
    qsize, qlines = K.fit(quote, "serif_italic", range(70, 30, -2), K.LW * 0.66, 7)
    a, dsc = K.metrics("serif_italic", qsize)
    lh = (a + dsc) * 1.24
    attrib = (v.subtext or "").strip().lstrip("-—– ").strip()
    asize, alines = 0.0, []
    if attrib:
        asize, alines = K.fit(attrib.upper(), "sans_medium", range(26, 16, -1), K.LW * 0.6, 2, tracking=0.14)
    mark_size = max(150.0, qsize * 3.0)
    mb = K.font("serif", mark_size).getBounds(K.font("serif", mark_size).textToGlyphs("“"))[0]
    mark_h, mark_gap = mb.height(), 34.0
    block = mark_h + mark_gap + len(qlines) * lh + (110 if alines else 0)
    y0 = CY - block / 2 + mark_h + mark_gap + a
    words = K.word_layout(qlines, "serif_italic", qsize, CX, y0, lh)
    last_base = y0 + (len(qlines) - 1) * lh
    rule_y = last_base + dsc + 46
    attr_y = rule_y + 32 + K.cap_height("sans_medium", asize) if alines else rule_y
    mark_y = y0 - a - mark_gap - mb.bottom()
    boxes = _lines_boxes(qlines, "serif_italic", qsize, CX, y0, lh)
    if alines:
        boxes += [K.text_box(ln, "sans_medium", asize, CX, attr_y + i * asize * 1.5, "center", 0.14)
                  for i, ln in enumerate(alines)]
    return {
        "nominal": 7.0,
        "_vignette": 0.45,
        "quote": {"size": qsize, "words": words},
        "mark": {"size": mark_size, "y": mark_y},
        "rule_y": rule_y,
        "attr": {"size": asize, "lines": alines, "y": attr_y},
        "boxes": boxes,
    }, None, None


def _draw_quote(anim: dict, x: K.Ctx) -> None:
    c = x.c
    M = anim["mark"]
    pm = S.ease_out_cubic(x.p(T0, 1.0))
    K.draw_text(c, "“", CX, M["y"] + 20 * (1 - pm), "serif", M["size"], S.GOLD, 0.55 * pm, align="center")
    Q = anim["quote"]
    words = Q["words"]
    n = max(1, len(words))
    start = T0 + 0.45
    end = min(max(0.55 * x.d, start + 0.6), 0.8 * x.d)
    step = (end - start) / n
    fade = max(0.35, min(0.7, step * 3))
    for i, w in enumerate(words):
        p = S.ease_out_cubic(x.p(start + i * step, fade))
        K.draw_text(c, w["t"], w["x"], w["y"] + 10 * (1 - p), "serif_italic", Q["size"], S.INK, p, shadow=0.5)
    A = anim["attr"]
    if A["lines"]:
        t_a = min(end + 0.25, 0.85 * x.d)
        K.rule(c, CX, anim["rule_y"], 70, x.p(t_a, 0.9), diamond=0, width=1.8, dots=False)
        p = S.ease_out_cubic(x.p(t_a + 0.2, 0.9))
        for i, ln in enumerate(A["lines"]):
            K.draw_text(c, ln, CX - 36 * (1 - p), A["y"] + i * A["size"] * 1.5, "sans_medium", A["size"],
                        S.GOLD, p, align="center", tracking=0.14)


# ------------------------------------------------------------------ stat


def _prep_stat(v: Visual, stem: Path, index: int):
    raw = (v.value or v.text or "").strip() or "—"
    spec = parse_value(raw)
    final = format_value(spec)
    vsize, _ = K.fit(final, "serif", range(240, 80, -6), K.LW * 0.8, 1)
    if K.measure(final, "serif", vsize) > K.LW * 0.8:
        vsize *= K.LW * 0.8 / K.measure(final, "serif", vsize)
    vcap = K.cap_height("serif", vsize)
    label = (v.text or "").strip() if v.value else ""
    lsize, llines = (0.0, [])
    if label:
        lsize, llines = K.fit(label, "serif", range(56, 30, -2), K.LW * 0.68, 2)
    llh = sum(K.metrics("serif", lsize)) * 1.12 if llines else 0
    ssize, slines = (0.0, [])
    if v.subtext:
        ssize, slines = K.fit(v.subtext.strip(), "serif_italic", range(34, 22, -1), K.LW * 0.6, 2)
    slh = sum(K.metrics("serif_italic", ssize)) * 1.2 if slines else 0
    g_rule, g_label, g_sub = 56, 62, 34
    block = vcap + g_rule + (g_label + len(llines) * llh if llines else 0) + (g_sub + len(slines) * slh if slines else 0)
    top = CY - block / 2 - 10
    vy = top + vcap
    rule_y = vy + g_rule
    ly = rule_y + g_label + K.metrics("serif", lsize)[0] * 0.8 if llines else rule_y
    sy = (ly + (len(llines) - 1) * llh + g_sub + K.metrics("serif_italic", ssize)[0]) if slines else ly
    if not llines and slines:
        sy = rule_y + g_sub + K.metrics("serif_italic", ssize)[0]
    vw = K.measure(final, "serif", vsize)
    boxes = [K.text_box(final, "serif", vsize, CX, vy, "center")]
    boxes += _lines_boxes(llines, "serif", lsize, CX, ly, llh) if llines else []
    boxes += _lines_boxes(slines, "serif_italic", ssize, CX, sy, slh) if slines else []
    return {
        "nominal": 5.0,
        "value": {"spec": spec, "size": vsize, "y": vy, "cap": vcap, "w": vw},
        "rule": {"y": rule_y, "half": max(120.0, min(vw * 0.5, 340.0))},
        "label": {"size": lsize, "lines": llines, "y": ly, "lh": llh},
        "sub": {"size": ssize, "lines": slines, "y": sy, "lh": slh},
        "boxes": boxes,
    }, None, None


def _draw_stat(anim: dict, x: K.Ctx) -> None:
    c, k = x.c, x.k
    V = anim["value"]
    spec = V["spec"]
    count_len = 1.6 * k
    pc = count_progress(x.t, T0, count_len)
    # warm glow behind the number, swelling as the count lands
    glow = S.ease_out_cubic(x.p(T0, count_len)) * (0.65 + 0.35 * math.sin(x.t * 1.1) ** 2)
    if glow > 0:
        gp = skia.Paint(AntiAlias=True)
        r = max(V["w"] * 0.62, 260)
        gp.setShader(skia.GradientShader.MakeRadial(
            (CX, V["y"] - V["cap"] / 2), r,
            [K.color(S.GOLD, 0.10 * glow), K.color(S.GOLD, 0.0)], None))
        c.drawCircle(CX, V["y"] - V["cap"] / 2, r, gp)
    if spec["count"]:
        text = format_value(spec, spec["number"] * pc)
        alpha = S.ease_out_cubic(x.p(T0, 0.35))
        z = 0.94 + 0.06 * S.ease_out_cubic(x.p(T0, 1.2 * k))
    else:
        text = format_value(spec)
        alpha = S.ease_out_cubic(x.p(T0, 0.9 * k))
        z = 0.9 + 0.1 * S.ease_out_cubic(x.p(T0, 1.4 * k))
    landed = S.clamp01((x.t - (T0 + count_len * 0.85)) / 0.6)
    col = K.mix(S.INK, "#fff3d6", 0.5 * math.sin(math.pi * landed))
    c.save()
    c.translate(CX, V["y"] - V["cap"] / 2)
    c.scale(z, z)
    c.translate(-CX, -(V["y"] - V["cap"] / 2))
    K.draw_text(c, text, CX, V["y"], "serif", V["size"], col, alpha, align="center", shadow=0.7)
    c.restore()
    R = anim["rule"]
    K.rule(c, CX, R["y"], R["half"], x.p(T0 + 0.55 * k, 1.2 * k), diamond=7)
    L = anim["label"]
    p = S.ease_out_cubic(x.p(T0 + 0.95 * k, 0.9 * k))
    for i, ln in enumerate(L["lines"]):
        K.draw_text(c, ln, CX, L["y"] + i * L["lh"] + 16 * (1 - p), "serif", L["size"], S.INK, p,
                    align="center", shadow=0.5)
    Sb = anim["sub"]
    p = S.ease_out_cubic(x.p(T0 + 1.45 * k, 0.9 * k))
    for i, ln in enumerate(Sb["lines"]):
        K.draw_text(c, ln, CX, Sb["y"] + i * Sb["lh"] + 10 * (1 - p), "serif_italic", Sb["size"], S.INK_DIM,
                    p, align="center")


# ------------------------------------------------------------------ timeline

TL_Y = 590.0
TL_X0, TL_X1 = 250.0, 1670.0


def _prep_timeline(v: Visual, stem: Path, index: int):
    events = v.events or []
    n = len(events)
    if n == 0:
        raise ValueError("timeline visual without events")
    xs = [CX] if n == 1 else [TL_X0 + (TL_X1 - TL_X0) * i / (n - 1) for i in range(n)]
    sp = (TL_X1 - TL_X0) / max(1, n - 1)
    block_w = min(420.0, 2 * sp - 44) if n > 2 else min(520.0, sp - 40) if n == 2 else 600.0
    hl = next((i for i, e in enumerate(events) if e.highlight), None)
    # one label size for every event, shrunk until all fit in 3 lines -- but an outlier
    # label never drags the rest below 26 px; it gets its own smaller size instead
    lsize = 26.0
    for lsize in (35.0, 33.0, 31.0, 29.0, 27.0, 26.0):
        if all(len(K.wrap(e.label, "serif", lsize + (5 if e.highlight else 0), block_w)) <= 3 for e in events):
            break
    dsize = 21.0
    out, boxes = [], []
    for i, e in enumerate(events):
        up = i % 2 == 0
        hi = e.highlight
        size = lsize + (5 if hi else 0)
        lines = K.wrap(e.label, "serif", size, block_w)
        if len(lines) > 3:
            size, lines = K.fit(e.label, "serif", np.arange(size, 17.0, -1.0), block_w, 3)
        lh = sum(K.metrics("serif", size)) * 1.1
        date = e.date.upper()
        ds = dsize + (2 if hi else 0)
        bw = max([K.measure(ln, "serif", size) for ln in lines] + [K.measure(date, "sans_medium", ds, 0.12)])
        tx = min(max(xs[i], 40 + bw / 2), K.LW - 40 - bw / 2)  # keep edge labels inside the frame
        stem_len = 46.0
        if up:
            date_y = TL_Y - stem_len - 14
            first = date_y - 18 - sum(K.metrics("serif", size)) * 0.25 - (len(lines) - 1) * lh
            line_ys = [first + j * lh for j in range(len(lines))]
        else:
            date_y = TL_Y + stem_len + 14 + K.cap_height("sans_medium", ds)
            first = date_y + 22 + K.metrics("serif", size)[0]
            line_ys = [first + j * lh for j in range(len(lines))]
        ebox = _block_box([K.text_box(date, "sans_medium", ds, tx, date_y, "center", 0.12)]
                          + [K.text_box(ln, "serif", size, tx, y, "center") for ln, y in zip(lines, line_ys)])
        out.append({"box": [ebox[0], ebox[2]], "x": xs[i], "tx": tx, "up": up, "hi": hi, "date": date, "dsize": ds, "date_y": date_y,
                    "lines": lines, "line_ys": line_ys, "size": size})
        boxes.append(K.text_box(date, "sans_medium", ds, tx, date_y, "center", 0.12))
        boxes += [K.text_box(ln, "serif", size, tx, y, "center") for ln, y in zip(lines, line_ys)]
    # final camera: zoom a touch and slide so the highlighted event ends near centre
    cam = {"z": 1.0, "tx": 0.0}
    if hl is not None and n > 1:
        ext = _block_box(boxes)
        z = 1.07 if n <= 4 else 1.1
        z = min(z, (K.LH - 230) / max(1.0, ext[3] - ext[1]))
        z = max(1.0, z)
        target = CX + 0.2 * (xs[hl] - CX)  # 80% of the way to centre
        cam = {"z": z, "tx": target - (CX + (xs[hl] - CX) * z)}
    title = None
    if v.text:
        tsize, tl = K.fit(v.text.strip().upper(), "sans_medium", range(26, 16, -1), K.LW * 0.8, 1, tracking=0.2)
        title = {"text": tl[0], "size": tsize, "y": 150.0}
        boxes.append(K.text_box(tl[0], "sans_medium", tsize, CX, 150.0, "center", 0.2))
    return {"nominal": 8.0, "events": out, "hl": hl, "cam": cam, "title": title, "boxes": boxes,
            "line": [TL_X0 - 110, TL_X1 + 110] if n > 1 else [CX - 400, CX + 400]}, None, None


def _tl_times(anim: dict, d: float, k: float):
    x0, x1 = anim["line"]
    draw_len = max(1.2, min(0.38 * d, 3.6)) if d > 2 else 0.6 * d
    t_ev = [T0 + 0.1 + draw_len * (e["x"] - x0) / (x1 - x0) for e in anim["events"]]
    return draw_len, t_ev


def _edge_alpha(sx: float, lw: float) -> float:
    m = 150.0
    return S.clamp01(min(sx + 60, lw - sx + 60) / m)


def _draw_timeline(anim: dict, x: K.Ctx) -> None:
    c, k, t = x.c, x.k, x.t
    ev = anim["events"]
    hl = anim["hl"]
    draw_len, t_ev = _tl_times(anim, x.d, k)
    if anim["title"]:
        T = anim["title"]
        p = S.ease_out_cubic(x.p(T0, 0.8))
        K.draw_text(c, T["text"], CX, T["y"] - 10 * (1 - p), "sans_medium", T["size"], S.GOLD, 0.9 * p,
                    align="center", tracking=0.2)
        K.hline(c, CX - 40, T["y"] + 26, 80, x.p(T0 + 0.3, 0.8), alpha=0.6, width=1.5)
    # camera
    t_hl = t_ev[hl] if hl is not None else 0.0
    cam_start = min(t_hl + 0.35, 0.6 * x.d)
    cam_len = max(0.8, min(2.6, 0.85 * x.d - cam_start))
    pc = S.ease_in_out(x.p(cam_start, cam_len)) if hl is not None else 0.0
    z = 1.0 + (anim["cam"]["z"] - 1.0) * pc
    tx = anim["cam"]["tx"] * pc
    dim = S.ease_in_out(x.p(t_hl + 0.4, 0.9)) if hl is not None else 0.0

    def sx(wx: float) -> float:  # world -> screen x (before ox translate)
        return CX + (wx - CX) * z + tx

    c.save()
    c.translate(CX + tx, TL_Y)
    c.scale(z, z)
    c.translate(-CX, -TL_Y)
    # the line: draws left -> right with a bright tip; fades at the frame edges
    x0, x1 = anim["line"]
    pl = S.ease_in_out(x.p(T0 + 0.1, draw_len))
    if pl > 0:
        xe = x0 + (x1 - x0) * pl
        lp = K.paint(S.GOLD, 0.8, stroke=3.0, cap="butt")
        lp.setShader(skia.GradientShader.MakeLinear(
            [(x0, TL_Y), (x1, TL_Y)],
            [K.color(S.GOLD, 0.0), K.color(S.GOLD, 0.75), K.color(S.GOLD, 0.75), K.color(S.GOLD, 0.0)],
            [0.0, 0.08, 0.92, 1.0]))
        c.drawLine(x0, TL_Y, xe, TL_Y, lp)
        if pl < 1:
            c.drawCircle(xe, TL_Y, 7, K.paint("#fff0c8", 0.8, blur=5))
        # minor ticks
        if len(ev) > 1:
            spx = (ev[1]["x"] - ev[0]["x"]) / 4
            tick = K.paint(S.GOLD, 0.35, stroke=1.4, cap="butt")
            xx = ev[0]["x"] - spx * 2
            while xx < min(xe, ev[-1]["x"] + spx * 2.5):
                c.drawLine(xx, TL_Y - 6, xx, TL_Y + 6, tick)
                xx += spx
    for i, e in enumerate(ev):
        te = t_ev[i]
        if t < te:
            continue
        base_a = (1.0 - 0.5 * dim) if (hl is not None and not e["hi"]) else 1.0
        a = _edge_alpha(sx(e["x"]) + x.ox, x.lw) * base_a  # dot + stem
        # labels pushed past the frame edge by the camera fade out instead of being cut
        over = max(0.0, 24 - (sx(e["box"][0]) + x.ox), (sx(e["box"][1]) + x.ox) - (x.lw - 24))
        ta = base_a * S.clamp01(1 - over / 70)
        pop = S.ease_out_back(x.p(te, S.POP))
        pfade = S.ease_out_cubic(x.p(te, 0.5))
        sgn = -1 if e["up"] else 1
        # stem
        ps = S.ease_out_cubic(x.p(te + 0.1, 0.4))
        if ps > 0:
            c.drawLine(e["x"], TL_Y + sgn * 12, e["x"], TL_Y + sgn * (12 + 34 * ps),
                       K.paint(S.GOLD, 0.55 * a, stroke=1.5, cap="butt"))
        if e["hi"]:
            rr = 13 * pop
            pulse = 0.5 + 0.5 * math.sin((t - te) * 2.6)
            c.drawCircle(e["x"], TL_Y, rr + 10 + 3 * pulse, K.paint(S.RED, 0.35 * a * pfade, blur=9))
            # expanding ripple every 2 s
            ph = ((t - te) % 2.0) / 2.0
            c.drawCircle(e["x"], TL_Y, rr + 6 + 30 * S.ease_out_cubic(ph),
                         K.paint(S.RED, 0.6 * (1 - ph) * a * pfade, stroke=2.0))
            c.drawCircle(e["x"], TL_Y, rr + 6, K.paint(S.GOLD, 0.9 * a * pfade, stroke=1.6))
            c.drawCircle(e["x"], TL_Y, rr, K.paint(S.RED, a))
        else:
            rr = 8 * pop
            c.drawCircle(e["x"], TL_Y, rr + 3, K.paint(S.BG, a))
            c.drawCircle(e["x"], TL_Y, rr, K.paint(S.GOLD, a))
            c.drawCircle(e["x"], TL_Y, rr * 0.42, K.paint(S.BG, a))
        dcol = S.RED if e["hi"] else S.GOLD
        dp = S.ease_out_cubic(x.p(te + 0.15, 0.6))
        K.draw_text(c, e["date"], e["tx"], e["date_y"] + sgn * 10 * (1 - dp), "sans_medium", e["dsize"],
                    mix_hi(dcol, e["hi"]), ta * dp, align="center", tracking=0.12)
        lp_ = S.ease_out_cubic(x.p(te + 0.3, 0.7))
        for ln, ly in zip(e["lines"], e["line_ys"]):
            K.draw_text(c, ln, e["tx"], ly + sgn * 12 * (1 - lp_), "serif", e["size"], S.INK,
                        ta * lp_ * (1.0 if e["hi"] else 0.92), align="center", shadow=0.5)
    c.restore()


def mix_hi(col: str, hi: bool) -> str:
    return K.mix(col, "#ff8a6a", 0.25) if hi else col


# ------------------------------------------------------------------ compare

PANEL_W = 740.0
PANEL_GAP = 120.0
PANEL_PAD = 54.0


def _prep_compare(v: Visual, stem: Path, index: int):
    cols = list(v.columns or [])
    if len(cols) < 2:
        raise ValueError("compare visual needs two columns")
    cols = cols[:2]
    header = None
    top_reserved = 0.0
    if v.text:
        hsize, hl = K.fit(v.text.strip().upper(), "sans_medium", range(26, 16, -1), K.LW * 0.8, 1, tracking=0.2)
        header = {"text": hl[0], "size": hsize}
        top_reserved = 90.0
    inner = PANEL_W - 2 * PANEL_PAD
    max_h = K.LH - 200 - top_reserved
    accents = [cols[0].color or S.RED, cols[1].color or S.BLUE]
    chosen = None
    for tsize, bsize in ((54, 36), (50, 34), (46, 32), (44, 30), (40, 28), (38, 26), (34, 24), (30, 22)):
        layout = []
        for col in cols:
            _, tlines = K.fit(col.title, "serif", [tsize], inner, 2)
            ta, td = K.metrics("serif", tsize)
            tlh = (ta + td) * 1.05
            y = PANEL_PAD + ta
            title_ys = [y + j * tlh for j in range(len(tlines))]
            y = title_ys[-1] + td + 26  # accent underline
            under_y = y
            y += 34
            ba, bd = K.metrics("serif", bsize)
            blh = (ba + bd) * 1.12
            bullets = []
            for pt in col.points[:5]:
                bl = K.wrap(pt, "serif", bsize, inner - 34)
                ys = [y + ba + j * blh for j in range(len(bl))]
                bullets.append({"lines": bl, "ys": ys})
                y = ys[-1] + bd + 24
            layout.append({"title": tlines, "title_ys": title_ys, "tsize": tsize, "under_y": under_y,
                           "bullets": bullets, "bsize": float(bsize), "h": y - 24 + PANEL_PAD})
        h = max(L["h"] for L in layout)
        widest = max(K.measure(ln, "serif", L["tsize"]) for L in layout for ln in L["title"])
        if h <= max_h and widest <= inner:
            chosen = (layout, h)
            break
    if chosen is None:
        chosen = (layout, h)
    layout, h = chosen
    h = max(h, 380.0)
    top = (K.LH - h) / 2 + top_reserved / 2 + 10
    lefts = [CX - PANEL_GAP / 2 - PANEL_W, CX + PANEL_GAP / 2]
    boxes = []
    panels = []
    for L, left, acc in zip(layout, lefts, accents):
        tx = left + PANEL_PAD
        for ln, y in zip(L["title"], L["title_ys"]):
            boxes.append(K.text_box(ln, "serif", L["tsize"], tx, top + y))
        for b in L["bullets"]:
            for ln, y in zip(b["lines"], b["ys"]):
                boxes.append(K.text_box(ln, "serif", L["bsize"], tx + 34, top + y))
        panels.append({**L, "left": left, "accent": accents[len(panels)]})
    if header:
        header["y"] = top - 52
        boxes.append(K.text_box(header["text"], "sans_medium", header["size"], CX, header["y"], "center", 0.2))
    return {"nominal": 8.0, "panels": panels, "top": top, "h": h, "header": header, "boxes": boxes}, None, None


def _draw_compare(anim: dict, x: K.Ctx) -> None:
    c, k = x.c, x.k
    top, h = anim["top"], anim["h"]
    H = anim["header"]
    if H:
        p = S.ease_out_cubic(x.p(T0, 0.8))
        K.draw_text(c, H["text"], CX, H["y"] - 10 * (1 - p), "sans_medium", H["size"], S.GOLD, 0.9 * p,
                    align="center", tracking=0.2)
    # divider + "vs"
    pdv = S.ease_in_out(x.p(T0 + 0.5 * k, 0.9 * k))
    if pdv > 0:
        half = (h / 2 - 30) * pdv
        lp = K.paint(S.GOLD, 0.5, stroke=1.5, cap="butt")
        cy = top + h / 2
        c.drawLine(CX, cy - 40, CX, cy - 40 - half + 40 * pdv, lp)
        c.drawLine(CX, cy + 40, CX, cy + 40 + half - 40 * pdv, lp)
        pv = S.ease_out_back(x.p(T0 + 0.8 * k, S.POP))
        if pv > 0:
            c.drawCircle(CX, cy, 30 * pv, K.paint(S.BG, 0.95))
            c.drawCircle(CX, cy, 30 * pv, K.paint(S.GOLD, 0.8, stroke=1.5))
            K.draw_text(c, "vs", CX, cy + 9 * pv, "serif_italic", 30 * pv, S.GOLD, min(1.0, pv), align="center")
    panels = anim["panels"]
    order = []  # interleave bullets L0, R0, L1, R1 ...
    for j in range(max(len(P["bullets"]) for P in panels)):
        for pi, P in enumerate(panels):
            if j < len(P["bullets"]):
                order.append((pi, j))
    b0 = T0 + 1.35 * k
    b_last = max(b0, 0.6 * x.d)
    step = (b_last - b0) / max(1, len(order) - 1)
    step = max(0.3 * k, min(1.6, step))
    t_bullet = {key: b0 + i * step for i, key in enumerate(order)}
    for pi, P in enumerate(panels):
        sgn = -1 if pi == 0 else 1
        pp = S.ease_out_cubic(x.p(T0 + 0.12 * pi * k, 1.0 * k))
        if pp <= 0:
            continue
        dx = sgn * 90 * (1 - pp)
        left = P["left"] + dx
        acc = P["accent"]
        rect = skia.Rect.MakeXYWH(left, top, PANEL_W, h)
        c.drawRRect(skia.RRect.MakeRectXY(rect, 6, 6), K.paint(S.HALO, 0.35 * pp, blur=18))
        c.drawRRect(skia.RRect.MakeRectXY(rect, 6, 6), K.paint(S.BG_2, 0.88 * pp))
        c.drawRRect(skia.RRect.MakeRectXY(rect, 6, 6), K.paint(acc, 0.35 * pp, stroke=1.2))
        c.save()
        c.clipRRect(skia.RRect.MakeRectXY(rect, 6, 6), True)
        c.drawRect(skia.Rect.MakeXYWH(left, top, PANEL_W, 6), K.paint(acc, 0.95 * pp))
        c.restore()
        tx = left + PANEL_PAD
        pt = S.ease_out_cubic(x.p(T0 + (0.25 + 0.12 * pi) * k, 0.8 * k))
        for ln, y in zip(P["title"], P["title_ys"]):
            K.draw_text(c, ln, tx, top + y + 10 * (1 - pt), "serif", P["tsize"], S.INK, pp * pt, shadow=0.5)
        K.hline(c, tx, top + P["under_y"], 64, x.p(T0 + (0.5 + 0.12 * pi) * k, 0.8 * k), hex_color=acc, width=3)
        for j, b in enumerate(P["bullets"]):
            tb = t_bullet[(pi, j)]
            pb = S.ease_out_cubic(x.p(tb, 0.6))
            if pb <= 0:
                continue
            pdm = S.ease_out_back(x.p(tb, S.POP))
            K.diamond(c, tx + 8, top + b["ys"][0] - P["bsize"] * 0.33, 6.5 * pdm, acc, pp)
            for ln, y in zip(b["lines"], b["ys"]):
                K.draw_text(c, ln, tx + 34 - 14 * (1 - pb), top + y, "serif", P["bsize"], S.INK, pb * pp)


# ------------------------------------------------------------------ person

PORTRAIT_ASPECT = 0.78  # w / h
PORTRAIT_PX = (1100, 1410)
OVAL = {"cx": 560.0, "cy": 540.0, "rx": 262.0, "ry": 338.0}
_PARTICLES = {"von", "van", "de", "der", "den", "zu", "di", "da", "du", "la", "le", "of", "the"}


def _fetch_portrait(v: Visual):
    """(PIL image, CommonsImage) or None. Exact file first, then query."""
    from historychannel.visuals import _commons
    from historychannel.visuals.prep import to_rgb

    client = _commons()
    attempts = ([("file", v.file)] if v.file else []) + ([("query", v.query)] if v.query else [])
    for how, value in attempts:
        try:
            ci = client.fetch(value) if how == "file" else client.search_best(value)
            with Image.open(ci.path) as im:
                im.load()
                return to_rgb(im), ci
        except Exception as e:  # missing file, network, bad image: try next / fall back
            log.warning("person portrait %s %r failed: %s", how, value, e)
    return None


def _initials(name: str) -> str:
    words = [w for w in re.split(r"[\s\-]+", name) if w and w.lower() not in _PARTICLES and w[0].isalpha()]
    if not words:
        return name[:1].upper()
    return (words[0][0] + (words[-1][0] if len(words) > 1 else "")).upper()


def _prep_person(v: Visual, stem: Path, index: int):
    from historychannel.visuals.prep import grade

    name = (v.text or "").strip() or "—"
    sub = (v.subtext or "").strip()
    got = _fetch_portrait(v) if (v.file or v.query) else None
    anim: dict = {"nominal": 6.0}
    attribution = source_url = None
    if got:
        img, ci = got
        crop = ImageOps.fit(img, PORTRAIT_PX, Image.Resampling.LANCZOS, centering=(0.5, 0.3))
        crop = grade(crop, warmth=0.22, desaturate=0.25, vignette=0.15, grain=2.0, seed=index)
        ppath = stem.with_name(stem.name + "_portrait.jpg")
        crop.save(ppath, quality=92)
        anim["portrait"] = str(ppath)
        anim["bg"] = str(K.make_background(stem.with_name(stem.name + "_bg.png"), seed=index, vignette=0.38,
                                           tint=img, tint_amount=0.3))
        attribution, source_url = ci.attribution, ci.page_url
        x0 = 930.0
        max_w = K.LW - x0 - 110
        nsize, nlines = K.fit(name, "serif", range(104, 50, -4), max_w, 2)
        ssize, slines = (0.0, [])
        if sub:
            ssize, slines = K.fit(sub, "serif_italic", range(40, 24, -2), max_w, 3)
        align, tx = "left", x0
    else:
        max_w = K.LW * 0.72
        nsize, nlines = K.fit(name, "serif", range(120, 56, -4), max_w, 2)
        ssize, slines = (0.0, [])
        if sub:
            ssize, slines = K.fit(sub, "serif_italic", range(42, 24, -2), K.LW * 0.6, 2)
        align, tx = "center", CX
        anim["monogram"] = _initials(name)
    na, nd = K.metrics("serif", nsize)
    nlh = (na + nd) * 1.05
    sa, sd = K.metrics("serif_italic", ssize) if slines else (0, 0)
    slh = (sa + sd) * 1.22
    g_rule, g_sub = 40, 46
    mono_h = 0 if got else 230
    block = mono_h + len(nlines) * nlh + g_rule + (g_sub + len(slines) * slh if slines else 0)
    top = CY - block / 2
    ny = top + mono_h + na
    rule_y = ny + (len(nlines) - 1) * nlh + nd + g_rule * 0.6
    sy = rule_y + g_sub + sa
    nw = max(K.measure(ln, "serif", nsize) for ln in nlines)
    boxes = _lines_boxes(nlines, "serif", nsize, tx, ny, nlh, align)
    if slines:
        boxes += _lines_boxes(slines, "serif_italic", ssize, tx, sy, slh, align)
    anim.update({
        "align": align, "tx": tx,
        "name": {"lines": nlines, "y": ny, "lh": nlh, "size": nsize},
        "rule": {"y": rule_y, "len": max(160.0, min(nw * 0.6, 420.0))},
        "sub": {"lines": slines, "y": sy, "lh": slh, "size": ssize},
        "mono": {"cy": top + 95, "rx": 72.0, "ry": 92.0},
        "boxes": boxes,
    })
    return anim, attribution, source_url


def _draw_person(anim: dict, x: K.Ctx) -> None:
    c, k = x.c, x.k
    if anim.get("portrait"):
        _draw_portrait(anim, x)
    elif anim.get("monogram"):
        M = anim["mono"]
        p = x.p(T0, 1.2 * k)
        rect = skia.Rect.MakeXYWH(CX - M["rx"], M["cy"] - M["ry"], 2 * M["rx"], 2 * M["ry"])
        sweep = 180 * S.ease_in_out(p)
        lp = K.paint(S.GOLD, 0.85, stroke=1.6, cap="butt")
        c.drawArc(rect, 90, sweep, False, lp)
        c.drawArc(rect, 90, -sweep, False, lp)
        pm = S.ease_out_cubic(x.p(T0 + 0.5 * k, 0.9 * k))
        K.draw_text(c, anim["monogram"], CX, M["cy"] + 26, "serif_italic", 76, S.GOLD, pm, align="center")
        K.diamond(c, CX, M["cy"] + M["ry"], 6 * S.ease_out_back(x.p(T0 + 0.9 * k, S.POP)))
    # name: letters type/fade in
    N = anim["name"]
    align, tx = anim["align"], anim["tx"]
    t_name = T0 + (0.75 if anim.get("portrait") else 0.6) * k
    total = sum(len(ln) for ln in N["lines"])
    step = min(0.05, 1.1 / max(1, total)) * k
    f = K.font("serif", N["size"])
    ci = 0
    for li, ln in enumerate(N["lines"]):
        y = N["y"] + li * N["lh"]
        w = K.measure(ln, "serif", N["size"])
        xx = tx - w / 2 if align == "center" else tx
        shadow = K.paint(S.HALO, 0.0, blur=N["size"] * 0.045)
        for ch in ln:
            p = S.ease_out_cubic(x.p(t_name + ci * step, 0.35))
            if p > 0 and ch != " ":
                shadow.setColor4f(K.color(S.HALO, 0.6 * p))
                c.drawString(ch, xx, y + N["size"] * 0.035, f, shadow)
                c.drawString(ch, xx + 6 * (1 - p), y, f, K.paint(S.INK, p))
            xx += f.measureText(ch)
            ci += 1
    t_rule = t_name + total * step * 0.6
    R = anim["rule"]
    if align == "center":
        K.rule(c, CX, R["y"], R["len"] / 2 + 40, x.p(t_rule, 1.1 * k), diamond=6)
    else:
        K.diamond(c, tx + 5, R["y"], 5.5 * S.ease_out_back(x.p(t_rule, S.POP)))
        K.hline(c, tx + 22, R["y"], R["len"], x.p(t_rule + 0.1, 1.0 * k), width=2.0)
    Sb = anim["sub"]
    p = S.ease_out_cubic(x.p(t_rule + 0.4 * k, 0.9 * k))
    for i, ln in enumerate(Sb["lines"]):
        K.draw_text(c, ln, tx, Sb["y"] + i * Sb["lh"] + 12 * (1 - p), "serif_italic", Sb["size"], S.INK_DIM, p,
                    align=align, shadow=0.4)


def _draw_portrait(anim: dict, x: K.Ctx) -> None:
    c, k = x.c, x.k
    O = OVAL
    img = K.load_image(anim["portrait"])
    rect = skia.Rect.MakeXYWH(O["cx"] - O["rx"], O["cy"] - O["ry"], 2 * O["rx"], 2 * O["ry"])
    pf = S.ease_out_cubic(x.p(T0 + 0.15 * k, 1.2 * k))
    # soft shadow behind the cameo
    c.drawOval(rect.makeOffset(0, 10), K.paint(S.HALO, 0.55 * pf, blur=26))
    if pf > 0:
        u = S.ease_in_out(x.t / x.d)
        z = 1.02 + 0.09 * u  # slow push-in
        w, h = rect.width() * z, rect.width() * z / PORTRAIT_ASPECT
        h = max(h, rect.height() * z)
        w = h * PORTRAIT_ASPECT
        dy = -12 * u
        dst = skia.Rect.MakeXYWH(O["cx"] - w / 2, O["cy"] - h / 2 + dy - (h - rect.height()) * 0.15, w, h)
        c.save()
        path = skia.Path()
        path.addOval(rect)
        c.clipPath(path, skia.ClipOp.kIntersect, True)
        ip = skia.Paint(AntiAlias=True)
        ip.setAlphaf(pf)
        c.drawImageRect(img, dst, K.SAMPLING, ip)
        # inner vignette so the photo sinks into the frame
        gp = skia.Paint(AntiAlias=True)
        gp.setShader(skia.GradientShader.MakeRadial(
            (O["cx"], O["cy"]), O["ry"] * 1.02,
            [K.color(S.HALO, 0.0), K.color(S.HALO, 0.0), K.color(S.HALO, 0.55 * pf)], [0.0, 0.62, 1.0]))
        c.drawOval(rect, gp)
        c.restore()
    # gold hairlines trace around from the bottom
    p = S.ease_in_out(x.p(T0, 1.4 * k))
    if p > 0:
        sweep = 180 * p
        lp = K.paint(S.GOLD, 0.95, stroke=2.0, cap="butt")
        c.drawArc(rect, 90, sweep, False, lp)
        c.drawArc(rect, 90, -sweep, False, lp)
        outer = rect.makeOutset(14, 14)
        lp2 = K.paint(S.GOLD, 0.45, stroke=1.0, cap="butt")
        c.drawArc(outer, 270, sweep, False, lp2)
        c.drawArc(outer, 270, -sweep, False, lp2)
    pd = S.ease_out_back(x.p(T0 + 1.1 * k, S.POP))
    K.diamond(c, O["cx"], O["cy"] + O["ry"] + 14, 7 * pd)
    K.diamond(c, O["cx"], O["cy"] - O["ry"] - 14, 5 * pd)


# ===================================================================== tables

_PREPARE = {
    "title_card": _prep_title,
    "quote": _prep_quote,
    "stat": _prep_stat,
    "timeline": _prep_timeline,
    "compare": _prep_compare,
    "person": _prep_person,
}
_DRAW = {
    "title_card": _draw_title,
    "quote": _draw_quote,
    "stat": _draw_stat,
    "timeline": _draw_timeline,
    "compare": _draw_compare,
    "person": _draw_person,
}
