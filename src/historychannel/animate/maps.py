"""Animated maps: a static high-resolution base layer + skia overlays drawn per frame.

prepare()  (main process, once per shot)
    * plans a base image covering every camera view (``_mapbase.plan_layout``) and
      renders it with matplotlib: sea, land, historical polity tints/borders, rivers
    * resolves regions / arrows / markers to base-image pixels, decides every label
      placement once (greedy collision avoidance over the start, middle and end camera
      views) so labels never jump between frames
    * returns an Asset whose ``animation`` dict is all ``frame()`` needs (picklable JSON)

frame()  (render workers, per frame; no matplotlib/geopandas)
    * camera: ease-in-out from ``extent`` to ``camera_to`` (log-interpolated zoom), or a
      very slow drift when there is no camera move
    * base layer: skia ``drawImageRect`` with trilinear mipmaps (cached per process)
    * overlays in output pixels, every size given in 1080p px and scaled by height/1080:
      regions (fill fade + outline draw-on + hatch + spaced-caps label), arrows
      (progressive draw-on with a riding arrowhead), markers (pop + ring + pulse),
      area texts, date stamps (slide/fade ticker), title.

Timing: every ``at`` is a fraction of the shot duration; nothing starts before
``style.SHOT_INTRO`` seconds (the incoming crossfade is still running).
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import math
from pathlib import Path

import numpy as np
import skia

from historychannel.animate import style as S
from historychannel.models import Asset, MapSpec, Visual

log = logging.getLogger(__name__)

VERSION = 1
REF_W, REF_H = 1920, 1080
POSTER_W, POSTER_H = 3840, 2160
POSTER_DURATION = 10.0  # nominal duration used to draw the poster's final state

# 1080p pixel sizes ------------------------------------------------------------------
TITLE_SIZE = 50
STAMP_SIZE = 30
MARKER_LABEL = 26
CAPITAL_LABEL = 28
ARROW_LABEL = 23
REGION_LABEL = 22
TEXT_SIZES = {"small": 20, "medium": 28, "large": 40}
MARKER_R = {"city": 7.0, "capital": 10.0, "battle": 13.0, "uprising": 14.0, "dot": 4.5}
REGION_FILL = 0.2
SAFE_BOTTOM = 0.80 * REF_H  # captions / subtitles live below this line
TITLE_BOX = (REF_W / 2, 74)  # centre of the title text
STAMP_RIGHT, STAMP_TOP = REF_W - 72, 48

ARROW_STYLE = {
    #          width, casing, alpha, color,       dash,        head
    "advance": (6.5, 4.5, 1.0, S.RED, None, (22, 20)),
    "retreat": (5.0, 4.0, 1.0, S.BLUE, (15, 9), (20, 18)),
    "flow": (4.0, 0.0, 0.7, S.GOLD, None, (16, 14)),
    "route": (3.2, 2.5, 0.95, S.INK, (0.01, 10), None),
}

_REVEAL = skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kLinear)


# --------------------------------------------------------------------------- helpers


def _start(at: float | None, duration: float) -> float | None:
    """Start time in seconds of an element (None = visible from the first frame)."""
    if at is None:
        return None
    return max(float(at) * duration, S.SHOT_INTRO)


def _prog(t: float, start: float | None, length: float) -> float:
    if start is None:
        return 1.0
    return S.clamp01((t - start) / max(length, 1e-6))


def _color(hex_color: str, alpha: float = 1.0) -> int:
    r, g, b, a = S.rgba(hex_color, alpha)
    return skia.ColorSetARGB(a, r, g, b)


def _mix(c1: str, c2: str, f: float) -> str:
    a, b = S.rgba(c1), S.rgba(c2)
    return "#" + "".join(f"{round(a[i] + (b[i] - a[i]) * f):02x}" for i in range(3))


@functools.lru_cache(maxsize=None)
def _typeface(kind: str) -> skia.Typeface:
    fm = skia.FontMgr()
    fams = S.SANS_FAMILIES if kind.startswith("sans") else S.SERIF_FAMILIES
    style = {
        "serif": skia.FontStyle.Normal(),
        "serif_italic": skia.FontStyle.Italic(),
        "serif_bold": skia.FontStyle(600, 5, skia.FontStyle.kUpright_Slant),
        "sans": skia.FontStyle.Normal(),
        "sans_bold": skia.FontStyle.Bold(),
    }[kind]
    for fam in fams:
        tf = fm.matchFamilyStyle(fam, style)
        if tf is not None:
            return tf
    return skia.Typeface.MakeEmpty()


@functools.lru_cache(maxsize=512)
def _blob(text: str, kind: str, size: float, spacing: float):
    """(TextBlob anchored at x=0 baseline, advance width, ascent<0, descent>0)."""
    font = skia.Font(_typeface(kind), size)
    font.setSubpixel(True)
    font.setEdging(skia.Font.Edging.kAntiAlias)
    glyphs = font.textToGlyphs(text)
    widths = font.getWidths(glyphs)
    xs, x = [], 0.0
    for w in widths:
        xs.append(x)
        x += w + spacing * size
    width = x - (spacing * size if widths else 0.0)
    blob = skia.TextBlob.MakeFromPosTextH(text, xs, 0.0, font) if text else None
    m = font.getMetrics()
    return blob, width, m.fAscent, m.fDescent


def text_size(text: str, kind: str, size: float, spacing: float = 0.0) -> tuple[float, float]:
    """(width, cap-ish height) in px."""
    _, w, asc, desc = _blob(text, kind, round(size, 2), spacing)
    return w, (-asc + desc) * 0.82


def _draw_text(canvas, text, x, y, *, size, kind="serif", color=S.INK, alpha=1.0, align=0.5,
               spacing=0.0, halo=0.16, halo_alpha=0.85, middle=True):
    """Draw ``text`` with its box centred (``middle``) or baseline at y; align 0=left .5=centre 1=right."""
    if alpha <= 0.003 or not text:
        return
    blob, w, asc, desc = _blob(text, kind, round(size, 2), spacing)
    if blob is None:
        return
    bx = x - align * w
    by = y - (asc + desc) / 2 if middle else y
    if halo > 0:
        hp = skia.Paint(AntiAlias=True, Color=_color(S.HALO, halo_alpha * alpha), Style=skia.Paint.kStroke_Style,
                        StrokeWidth=size * halo, StrokeJoin=skia.Paint.kRound_Join)
        canvas.drawTextBlob(blob, bx, by, hp)
    canvas.drawTextBlob(blob, bx, by, skia.Paint(AntiAlias=True, Color=_color(color, alpha)))


def _edge_fade(x: float, y: float, sc: float, width: int, height: int) -> float:
    """Labels fade out as the camera carries them towards the frame edge (and the
    bottom subtitle band) instead of being cut off."""
    m = 36 * sc
    f = min(S.clamp01((x - 20 * sc) / m), S.clamp01((width - 20 * sc - x) / m), S.clamp01((y - 20 * sc) / m))
    return f * S.clamp01((height - 60 * sc - y) / (50 * sc))


# --------------------------------------------------------------------------- label placement


class _Boxes:
    """Axis-aligned obstacle boxes in 1080p reference px, one list per camera view."""

    def __init__(self, n_views: int):
        self.views: list[list[tuple[float, float, float, float]]] = [[] for _ in range(n_views)]

    def add(self, v: int, box):
        self.views[v].append(box)

    @staticmethod
    def overlap(a, b) -> float:
        w = min(a[2], b[2]) - max(a[0], b[0])
        h = min(a[3], b[3]) - max(a[1], b[1])
        return w * h if w > 0 and h > 0 else 0.0

    def cost(self, v: int, box) -> float:
        area = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
        hit = sum(self.overlap(box, b) for b in self.views[v]) / area
        # keep out of the frame edge and the caption/subtitle band
        out = 0.0
        if box[0] < 40 or box[2] > REF_W - 40 or box[1] < 30:
            out += 1.0
        if box[3] > SAFE_BOTTOM:
            out += 0.6 * min(1.0, (box[3] - SAFE_BOTTOM) / 40)
        return 10 * hit + 4 * out


def _box(cx, cy, w, h, pad=4.0):
    return (cx - w / 2 - pad, cy - h / 2 - pad, cx + w / 2 + pad, cy + h / 2 + pad)


# --------------------------------------------------------------------------- geometry


def _curve(pts: list[tuple[float, float]], n: int = 96, bend: float = 0.18) -> np.ndarray:
    """Smooth curve through ``pts`` (base px): a quadratic arc for 2 points, else a
    centripetal Catmull-Rom spline through every waypoint. Returns (n, 2)."""
    P = np.asarray(pts, dtype=float)
    if len(P) == 2:
        a, b = P
        d = b - a
        normal = np.array([d[1], -d[0]])  # bends to the left of travel (screen coords)
        c = (a + b) / 2 + bend * normal
        u = np.linspace(0, 1, n)[:, None]
        return (1 - u) ** 2 * a + 2 * (1 - u) * u * c + u**2 * b
    ext = np.vstack([2 * P[0] - P[1], P, 2 * P[-1] - P[-2]])
    out = []
    segs = len(P) - 1
    per = max(8, n // segs)
    for i in range(segs):
        p0, p1, p2, p3 = ext[i : i + 4]

        def tj(ti, pi, pj):
            return ti + max(1e-6, np.linalg.norm(pj - pi)) ** 0.5

        t0 = 0.0
        t1 = tj(t0, p0, p1)
        t2 = tj(t1, p1, p2)
        t3 = tj(t2, p2, p3)
        ts = np.linspace(t1, t2, per, endpoint=(i == segs - 1))[:, None]
        a1 = (t1 - ts) / (t1 - t0) * p0 + (ts - t0) / (t1 - t0) * p1
        a2 = (t2 - ts) / (t2 - t1) * p1 + (ts - t1) / (t2 - t1) * p2
        a3 = (t3 - ts) / (t3 - t2) * p2 + (ts - t2) / (t3 - t2) * p3
        b1 = (t2 - ts) / (t2 - t0) * a1 + (ts - t0) / (t2 - t0) * a2
        b2 = (t3 - ts) / (t3 - t1) * a2 + (ts - t1) / (t3 - t1) * a3
        out.append((t2 - ts) / (t2 - t1) * b1 + (ts - t1) / (t2 - t1) * b2)
    return np.vstack(out)


def _arclen(P: np.ndarray) -> np.ndarray:
    return np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))])


def _subcurve(P: np.ndarray, L: np.ndarray, s0: float, s1: float) -> np.ndarray:
    """Points of polyline P between arc lengths s0 < s1 (interpolated ends)."""
    if s1 <= s0:
        return P[:0]
    xs = np.interp([s0, s1], L, P[:, 0])
    ys = np.interp([s0, s1], L, P[:, 1])
    inner = (L > s0) & (L < s1)
    return np.vstack([[xs[0], ys[0]], P[inner], [xs[1], ys[1]]])


def _point_at(P: np.ndarray, L: np.ndarray, s: float) -> tuple[np.ndarray, np.ndarray]:
    """Point and unit tangent at arc length s."""
    s = min(max(s, 0.0), L[-1])
    p = np.array([np.interp(s, L, P[:, 0]), np.interp(s, L, P[:, 1])])
    e = 0.02 * L[-1] + 1e-6
    a = np.array([np.interp(max(0, s - e), L, P[:, 0]), np.interp(max(0, s - e), L, P[:, 1])])
    b = np.array([np.interp(min(L[-1], s + e), L, P[:, 0]), np.interp(min(L[-1], s + e), L, P[:, 1])])
    d = b - a
    n = np.linalg.norm(d)
    return p, (d / n if n > 0 else np.array([1.0, 0.0]))


# --------------------------------------------------------------------------- camera


def camera(anim: dict, t: float, duration: float) -> tuple[float, float, float, float]:
    """Camera rect (x0, y0, x1, y1) in base px at time t."""
    c0 = anim["cam0"]
    c1 = anim.get("cam1")
    u = S.clamp01(t / max(duration, 1e-6))
    if c1:
        e = S.ease_in_out(u)
        w0, w1 = c0[2] - c0[0], c1[2] - c1[0]
        w = math.exp(math.log(w0) + (math.log(w1) - math.log(w0)) * e)
        # centre follows the zoom so the target point approaches smoothly
        f = (w0 - w) / (w0 - w1) if abs(w0 - w1) > 1e-6 else e
        cx = (c0[0] + c0[2]) / 2 + ((c1[0] + c1[2]) / 2 - (c0[0] + c0[2]) / 2) * f
        cy = (c0[1] + c0[3]) / 2 + ((c1[1] + c1[3]) / 2 - (c0[1] + c0[3]) / 2) * f
    else:
        w = (c0[2] - c0[0]) / (1.0 + 0.04 * u)  # slow drift so a map never feels frozen
        cx = (c0[0] + c0[2]) / 2 + 0.006 * (c0[2] - c0[0]) * u
        cy = (c0[1] + c0[3]) / 2
    h = w * 9 / 16
    bw, bh = anim["base"]["w"], anim["base"]["h"]
    w, h = min(w, bw), min(h, bh)
    cx = min(max(cx, w / 2), bw - w / 2)
    cy = min(max(cy, h / 2), bh - h / 2)
    return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2


def _to_ref(cam, x, y):
    s = REF_W / (cam[2] - cam[0])
    return (np.asarray(x) - cam[0]) * s, (np.asarray(y) - cam[1]) * s


# --------------------------------------------------------------------------- prepare


def _stamp_box(stamps) -> tuple[float, float, float, float] | None:
    if not stamps:
        return None
    w = max(text_size(s["text"], "serif", STAMP_SIZE, 0.04)[0] for s in stamps) + 56
    return (STAMP_RIGHT - w, STAMP_TOP, STAMP_RIGHT, STAMP_TOP + 62)


def build_anim(spec: MapSpec, base_path: Path, layout) -> dict:
    """Everything frame() needs, in base pixels / 1080p reference px."""
    from historychannel.animate import _mapbase as MB

    proj = layout.proj
    cams = layout.cams
    cam0 = cams[0]
    cam1 = cams[1] if len(cams) > 1 else None
    anim: dict = {
        "kind": "map",
        "version": VERSION,
        "base": {"path": str(base_path), "w": proj.w, "h": proj.h},
        "proj": proj.to_dict(),
        "cam0": list(cam0),
        "cam1": list(cam1) if cam1 else None,
        "title": spec.title,
        "regions": [],
        "arrows": [],
        "markers": [],
        "texts": [],
        "stamps": [{"at": float(s.at), "text": s.text} for s in sorted(spec.stamps, key=lambda s: s.at)],
    }
    # placement is checked in three views: start, middle and end of the camera move
    probe = {"cam0": anim["cam0"], "cam1": anim["cam1"], "base": anim["base"]}
    views = [camera(probe, u, 1.0) for u in (0.0, 0.5, 1.0)] if cam1 else [camera(probe, u, 1.0) for u in (0.0, 1.0)]
    weights = [0.6, 0.6, 1.0] if cam1 else [0.8, 1.0]
    boxes = _Boxes(len(views))

    def reserve_all(fn):
        for v, cam in enumerate(views):
            b = fn(cam)
            if b is not None:
                boxes.add(v, b)

    if spec.title:
        tw, th = text_size(spec.title, "serif", TITLE_SIZE)
        reserve_all(lambda cam: _box(TITLE_BOX[0], TITLE_BOX[1] + 8, tw + 40, th + 34))
    sb = _stamp_box(anim["stamps"])
    if sb:
        reserve_all(lambda cam: sb)

    # markers: glyphs are obstacles for everything
    mk = []
    for m in spec.markers:
        x, y = proj(m.lon, m.lat)
        mk.append((m, float(x), float(y)))
        r = MARKER_R[m.style] + 4

        def mbox(cam, x=float(x), y=float(y), r=r):
            rx, ry = _to_ref(cam, x, y)
            return (float(rx) - r, float(ry) - r, float(rx) + r, float(ry) + r)

        reserve_all(mbox)

    # arrows: resolve curves; their sampled points are obstacles for labels
    arrows_tmp = []
    for a in spec.arrows:
        pts = [a.from_lonlat, *a.via, a.to_lonlat]
        xs, ys = proj([p[0] for p in pts], [p[1] for p in pts])
        P = _curve(list(zip(xs.tolist(), ys.tolist())), bend=0.0 if a.via else 0.18)
        arrows_tmp.append((a, P))
        width = ARROW_STYLE[a.style][0] + ARROW_STYLE[a.style][1]
        for v, cam in enumerate(views):
            rx, ry = _to_ref(cam, P[::4, 0], P[::4, 1])
            for px, py in zip(rx.tolist(), ry.tolist()):
                boxes.add(v, _box(px, py, width + 4, width + 4, pad=0))

    # map texts are fixed in place
    for tx in spec.texts:
        x, y = proj(tx.lon, tx.lat)
        size = TEXT_SIZES[tx.size]
        sp = 0.28 if tx.size != "small" else 0.18
        w, h = text_size(tx.text.upper(), "serif_italic", size, sp)
        anim["texts"].append({"x": float(x), "y": float(y), "text": tx.text.upper(), "size": size,
                              "spacing": sp, "at": tx.at})

        def tbox(cam, x=float(x), y=float(y), w=w, h=h):
            rx, ry = _to_ref(cam, x, y)
            return _box(float(rx), float(ry), w, h)

        reserve_all(tbox)

    def best(cands, extra=None):
        """cands: list of (payload, fn(cam)->box or None); ``extra(cam, box)`` adds a
        per-view cost. Returns the cheapest payload and reserves its boxes."""
        best_c, best_cost = None, math.inf
        for i, (payload, fn) in enumerate(cands):
            cost = 0.002 * i
            for v, cam in enumerate(views):
                b = fn(cam)
                if b is not None:
                    cost += weights[v] * (boxes.cost(v, b) + (extra(cam, b) if extra else 0.0))
            if cost < best_cost:
                best_c, best_cost = (payload, fn), cost
            if cost < 0.01:
                break
        if best_c is None:
            return None
        reserve_all(best_c[1])
        return best_c[0]

    # marker labels (capitals first: they matter most)
    order = sorted(range(len(mk)), key=lambda i: {"capital": 0, "uprising": 1, "battle": 1}.get(mk[i][0].style, 2))
    placed = {}
    for i in order:
        m, x, y = mk[i]
        text = m.label or m.name
        size = CAPITAL_LABEL if m.style == "capital" else MARKER_LABEL
        kind = "serif_bold" if m.style == "capital" else "serif"
        w, h = text_size(text, kind, size)
        gap = MARKER_R[m.style] + 9
        cands = []
        for sx, sy in ((1, 0), (-1, 0), (0, -1), (0, 1), (1, -1), (1, 1), (-1, -1), (-1, 1)):
            k = 0.75 if sx and sy else 1.0
            dx = sx * (gap * k + w / 2)
            dy = sy * (gap * k + h / 2)

            def fn(cam, x=x, y=y, dx=dx, dy=dy, w=w, h=h):
                rx, ry = _to_ref(cam, x, y)
                if not (0 <= rx <= REF_W and 0 <= ry <= REF_H):
                    return None
                return _box(float(rx) + dx, float(ry) + dy, w, h)

            cands.append(((dx, dy, sx), fn))
        placed[i] = best(cands) or (gap + w / 2, 0.0, 1)
    for i, (m, x, y) in enumerate(mk):
        dx, dy, sx = placed[i]
        anim["markers"].append({
            "x": x, "y": y, "style": m.style, "at": m.at, "pulse": bool(m.pulse),
            "label": m.label or m.name, "dx": dx, "dy": dy, "side": sx,
        })

    # arrows + their labels
    for a, P in arrows_tmp:
        width, casing, alpha, color, dash, head = ARROW_STYLE[a.style]
        color = a.color or color
        near_marker = any(
            math.hypot(P[0, 0] - mx, P[0, 1] - my) * REF_W / (views[-1][2] - views[-1][0]) < 14 for _, mx, my in mk
        )
        end_marker = max(
            [MARKER_R[m.style] for m, mx, my in mk
             if math.hypot(P[-1, 0] - mx, P[-1, 1] - my) * REF_W / (views[-1][2] - views[-1][0]) < 14] or [0.0]
        )
        entry = {
            "pts": [[round(float(px), 2), round(float(py), 2)] for px, py in P],
            "style": a.style, "color": color, "at": a.at, "draw": float(a.draw),
            "tail_dot": not near_marker, "end_gap": end_marker + 5.0,
            "label": a.label, "label_s": 0.5, "label_dx": 0.0, "label_dy": 0.0,
        }
        if a.label:
            L = _arclen(P)
            w, h = text_size(a.label, "serif_italic", ARROW_LABEL)
            cands = []
            for frac in (0.5, 0.42, 0.58, 0.34, 0.66):
                p, tan = _point_at(P, L, frac * L[-1])
                nrm = np.array([tan[1], -tan[0]])
                for side in (1, -1):
                    # distance from the curve so the box clears the line on that side
                    ext = abs(nrm[0]) * w / 2 + abs(nrm[1]) * h / 2
                    off = side * nrm * (ext + width / 2 + 10)

                    def fn(cam, p=p, off=off, w=w, h=h):
                        rx, ry = _to_ref(cam, p[0], p[1])
                        return _box(float(rx) + off[0], float(ry) + off[1], w, h)

                    cands.append(((frac, float(off[0]), float(off[1])), fn))
            res = best(cands)
            if res:
                entry["label_s"], entry["label_dx"], entry["label_dy"] = res
        anim["arrows"].append(entry)

    # regions
    for i, reg in enumerate(spec.regions):
        geom = MB.region_geom(reg, spec.basemap)
        if geom is None or geom.is_empty:
            log.warning("map region %r resolved to nothing; skipped", reg.label or i)
            continue
        rings = MB.geom_to_rings(geom, proj)
        if not rings:
            continue
        entry = {
            "rings": rings, "color": reg.color or S.REGION_COLORS[i % len(S.REGION_COLORS)],
            "at": reg.at, "hatch": bool(reg.hatch), "label": reg.label.upper() if reg.label else None,
            "lx": None, "ly": None,
        }
        if reg.label:
            w, h = text_size(entry["label"], "serif", REGION_LABEL, 0.22)
            cands = []
            seen = set()
            for cam_c in (views[-1], *views[:-1]):
                for px, py in MB.label_candidates(geom, proj, cam_c):
                    if (round(px), round(py)) in seen:
                        continue
                    seen.add((round(px), round(py)))

                    def fn(cam, px=px, py=py, w=w, h=h):
                        rx, ry = _to_ref(cam, px, py)
                        return _box(float(rx), float(ry), w, h, pad=8)

                    cands.append(((px, py), fn))
            inside = MB.inside_fn(geom, proj)

            def outside(cam, b, inside=inside):
                # labels should sit well inside their region, not straddle its border
                xs = np.linspace(b[0], b[2], 5)
                ys = np.linspace(b[1], b[3], 3)
                gx, gy = np.meshgrid(xs, ys)
                s_ = (cam[2] - cam[0]) / REF_W
                return 3.0 * (1.0 - float(np.mean(inside(cam[0] + gx.ravel() * s_, cam[1] + gy.ravel() * s_))))

            res = best(cands, outside)
            if res:
                entry["lx"], entry["ly"] = res
        anim["regions"].append(entry)

    anim["id"] = hashlib.sha1(json.dumps(anim, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return anim


def prepare(visual: Visual, out_dir: Path, *, index: int) -> Asset:
    from historychannel.animate import _mapbase as MB

    spec = visual.map
    if spec is None:
        raise ValueError("map visual without map spec")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    layout = MB.plan_layout(spec.extent, spec.camera_to)
    # the base PNG is re-used across builds; its key covers the layer code itself
    code = hashlib.sha1(Path(MB.__file__).read_bytes()).hexdigest()
    key = hashlib.sha1(json.dumps([VERSION, code, spec.basemap, layout.proj.to_dict()]).encode()).hexdigest()[:10]
    base = out_dir / f"{index:03d}_mapbase-{key}.png"
    if not base.exists():
        MB.render_base(layout, spec.basemap, base, seed=index)
    anim = build_anim(spec, base, layout)
    poster = out_dir / f"{index:03d}_map.png"
    from PIL import Image

    Image.fromarray(frame(anim, POSTER_DURATION, POSTER_DURATION, POSTER_W, POSTER_H)).save(poster)
    attribution = MB.NE_ATTRIBUTION
    if spec.basemap in ("1815", "1880"):
        attribution += "; " + MB.HB_ATTRIBUTION
    return Asset(path=poster, kind="map", animation=anim, attribution=attribution)


# --------------------------------------------------------------------------- per-process scene cache


class _Scene:
    """Per-process heavy state for one anim: base image + skia paths."""

    def __init__(self, anim: dict):
        from PIL import Image

        im = Image.open(anim["base"]["path"]).convert("RGBA")
        arr = np.ascontiguousarray(np.asarray(im))
        self.image = skia.Image.fromarray(arr, colorType=skia.ColorType.kRGBA_8888_ColorType).withDefaultMipmaps()
        self.fills: list[skia.Path] = []
        self.rings: list[list[skia.Path]] = []
        self.perims: list[float] = []
        for reg in anim["regions"]:
            fill = skia.Path()
            fill.setFillType(skia.PathFillType.kEvenOdd)
            rps = []
            for ring in reg["rings"]:
                p = skia.Path()
                p.moveTo(ring[0], ring[1])
                for j in range(2, len(ring), 2):
                    p.lineTo(ring[j], ring[j + 1])
                p.close()
                fill.addPath(p)
                rps.append(p)
            self.fills.append(fill)
            self.rings.append(rps)
        self.arrows = []
        for a in anim["arrows"]:
            P = np.asarray(a["pts"], dtype=float)
            self.arrows.append((P, _arclen(P)))


_SCENES: dict[str, _Scene] = {}


def _scene(anim: dict) -> _Scene:
    key = anim.get("id") or anim["base"]["path"]
    sc = _SCENES.get(key)
    if sc is None:
        while len(_SCENES) >= 2:  # 8K RGBA base + mipmaps is ~200 MB per worker
            _SCENES.pop(next(iter(_SCENES)))
        sc = _SCENES[key] = _Scene(anim)
    return sc


@functools.lru_cache(maxsize=8)
def _buffers(width: int, height: int):
    arr = np.zeros((height, width, 4), dtype=np.uint8)
    surf = skia.Surface(arr)
    return arr, surf


@functools.lru_cache(maxsize=8)
def _vignette(width: int, height: int) -> skia.Paint:
    r = math.hypot(width, height) / 2
    shader = skia.GradientShader.MakeRadial(
        skia.Point(width / 2, height * 0.46), r,
        [_color(S.HALO, 0.0), _color(S.HALO, 0.0), _color(S.HALO, 0.42)], [0.0, 0.55, 1.0],
    )
    return skia.Paint(Shader=shader)


@functools.lru_cache(maxsize=8)
def _top_shade(width: int, height: int) -> skia.Paint:
    shader = skia.GradientShader.MakeLinear(
        [skia.Point(0, 0), skia.Point(0, height * 0.2)], [_color(S.HALO, 0.55), _color(S.HALO, 0.0)]
    )
    return skia.Paint(Shader=shader)


# --------------------------------------------------------------------------- frame


def frame(anim: dict, t: float, duration: float, width: int, height: int) -> np.ndarray:
    duration = max(float(duration), 1e-3)
    t = min(max(float(t), 0.0), duration)
    scn = _scene(anim)
    arr, surf = _buffers(width, height)
    canvas = surf.getCanvas()
    sc = height / REF_H

    cam = camera(anim, t, duration)
    k = width / (cam[2] - cam[0])  # base px -> output px
    mtx = skia.Matrix()
    mtx.setScaleTranslate(k, k, -cam[0] * k, -cam[1] * k)

    canvas.drawImageRect(scn.image, skia.Rect(*cam), skia.Rect(0, 0, width, height), _REVEAL)

    def out(x, y):
        return (x - cam[0]) * k, (y - cam[1]) * k

    _draw_regions(canvas, anim, scn, t, duration, mtx, out, sc, width, height)
    _draw_texts(canvas, anim, t, duration, out, sc)
    _draw_arrows(canvas, anim, scn, t, duration, k, cam, sc, width, height)
    _draw_markers(canvas, anim, t, duration, out, sc, width, height)
    canvas.drawPaint(_vignette(width, height))
    _draw_title(canvas, anim, t, sc, width, height)
    _draw_stamps(canvas, anim, t, duration, sc, width)

    return np.ascontiguousarray(arr[..., :3])


def _draw_regions(canvas, anim, scn, t, duration, mtx, out, sc, width, height):
    for reg, fill, rings in zip(anim["regions"], scn.fills, scn.rings):
        st = _start(reg["at"], duration)
        pf = S.ease_in_out(_prog(t, st, S.FADE))
        po = S.ease_in_out(_prog(t, st, S.DRAW))
        if pf <= 0 and po <= 0:
            continue
        path = skia.Path()
        fill.transform(mtx, path)
        canvas.drawPath(path, skia.Paint(AntiAlias=True, Color=_color(reg["color"], REGION_FILL * pf)))
        if reg["hatch"] and pf > 0:
            canvas.save()
            canvas.clipPath(path, doAntiAlias=True)
            b = path.computeTightBounds()
            step = 13 * sc
            hp = skia.Paint(AntiAlias=True, Color=_color(reg["color"], 0.5 * pf), StrokeWidth=1.7 * sc,
                            Style=skia.Paint.kStroke_Style)
            x = b.left() - b.height()
            while x < b.right():
                canvas.drawLine(x, b.bottom(), x + b.height(), b.top(), hp)
                x += step
            canvas.restore()
        if po > 0:
            trim = skia.TrimPathEffect.Make(0.0, po) if po < 1 else None
            casing = skia.Paint(AntiAlias=True, Color=_color(S.HALO, 0.45), Style=skia.Paint.kStroke_Style,
                                StrokeWidth=4.6 * sc, StrokeJoin=skia.Paint.kRound_Join, StrokeCap=skia.Paint.kRound_Cap)
            line = skia.Paint(AntiAlias=True, Color=_color(reg["color"], 0.95), Style=skia.Paint.kStroke_Style,
                              StrokeWidth=2.3 * sc, StrokeJoin=skia.Paint.kRound_Join, StrokeCap=skia.Paint.kRound_Cap)
            if trim:
                casing.setPathEffect(trim)
                line.setPathEffect(trim)
            for rp in rings:
                p = skia.Path()
                rp.transform(mtx, p)
                canvas.drawPath(p, casing)
                canvas.drawPath(p, line)
        if reg["label"] and reg["lx"] is not None:
            pl = S.ease_out_cubic(_prog(t, None if st is None else st + 0.45 * S.DRAW, S.FADE))
            x, y = out(reg["lx"], reg["ly"])
            _draw_text(canvas, reg["label"], x, y + 6 * sc * (1 - pl), size=REGION_LABEL * sc, spacing=0.22,
                       color=_mix(S.INK, reg["color"], 0.18), alpha=0.95 * pl * _edge_fade(x, y, sc, width, height),
                       halo=0.2)


def _draw_texts(canvas, anim, t, duration, out, sc):
    for tx in anim["texts"]:
        p = S.ease_in_out(_prog(t, _start(tx["at"], duration), S.FADE * 1.5))
        x, y = out(tx["x"], tx["y"])
        _draw_text(canvas, tx["text"], x, y, size=tx["size"] * sc, kind="serif_italic", spacing=tx["spacing"],
                   color=S.INK_DIM, alpha=0.7 * p, halo=0.1, halo_alpha=0.6)


def _arrow_head(canvas, tip, tan, length, half_w, color, alpha, casing, sc):
    nrm = np.array([-tan[1], tan[0]])
    base = tip - tan * length
    path = skia.Path()
    path.moveTo(*tip)
    path.lineTo(*(base + nrm * half_w))
    path.lineTo(*(tip - tan * length * 0.72))
    path.lineTo(*(base - nrm * half_w))
    path.close()
    if casing > 0:
        canvas.drawPath(path, skia.Paint(AntiAlias=True, Color=_color(S.HALO, 0.6 * alpha), Style=skia.Paint.kStroke_Style,
                                         StrokeWidth=casing, StrokeJoin=skia.Paint.kRound_Join))
    canvas.drawPath(path, skia.Paint(AntiAlias=True, Color=_color(color, alpha)))


def _poly(points: np.ndarray) -> skia.Path:
    p = skia.Path()
    p.moveTo(float(points[0, 0]), float(points[0, 1]))
    for x, y in points[1:]:
        p.lineTo(float(x), float(y))
    return p


def _draw_arrows(canvas, anim, scn, t, duration, k, cam, sc, width, height):
    for a, (P0, L0) in zip(anim["arrows"], scn.arrows):
        st = _start(a["at"], duration)
        draw_len = max(0.3, a["draw"] * duration)
        pd = S.ease_in_out(_prog(t, st, draw_len))
        if pd <= 0:
            continue
        lw, casing, alpha, _, dash, head = ARROW_STYLE[a["style"]]
        color = a["color"]
        P = (P0 - np.array([cam[0], cam[1]])) * k
        L = L0 * k
        g0 = (MARKER_R["city"] + 6) * sc if not a["tail_dot"] else 4 * sc
        g1 = a["end_gap"] * sc
        total = L[-1] - g0 - g1
        if total <= 4 * sc:
            continue
        s_tip = g0 + pd * total
        hl = head[0] * sc if head else 0.0
        seg = _subcurve(P, L, g0, max(g0 + 0.1, s_tip - 0.6 * hl))
        tip, tan = _point_at(P, L, s_tip)
        appear = min(1.0, pd * 6)
        a_alpha = alpha * appear

        if a["tail_dot"]:
            r = 5 * sc * S.ease_out_back(min(1.0, pd * 5))
            p0 = P[0]
            canvas.drawCircle(p0[0], p0[1], r + 2 * sc, skia.Paint(AntiAlias=True, Color=_color(S.HALO, 0.8)))
            canvas.drawCircle(p0[0], p0[1], r, skia.Paint(AntiAlias=True, Color=_color(color, a_alpha)))

        if len(seg) >= 2:
            path = _poly(seg)
            if casing > 0:
                cp = skia.Paint(AntiAlias=True, Color=_color(S.HALO, 0.55 * a_alpha), Style=skia.Paint.kStroke_Style,
                                StrokeWidth=(lw + casing) * sc, StrokeCap=skia.Paint.kRound_Cap,
                                StrokeJoin=skia.Paint.kRound_Join)
                canvas.drawPath(path, cp)
            lp = skia.Paint(AntiAlias=True, Color=_color(color, a_alpha), Style=skia.Paint.kStroke_Style,
                            StrokeWidth=lw * sc, StrokeCap=skia.Paint.kRound_Cap if a["style"] != "retreat" else skia.Paint.kButt_Cap,
                            StrokeJoin=skia.Paint.kRound_Join)
            if dash:
                lp.setPathEffect(skia.DashPathEffect.Make([dash[0] * sc, dash[1] * sc], 0.0))
            canvas.drawPath(path, lp)

        if head:
            _arrow_head(canvas, tip, tan, hl, head[1] / 2 * sc, color, a_alpha, 3.5 * sc if casing else 0.0, sc)
        else:  # routes: a small dot rides at the tip
            canvas.drawCircle(tip[0], tip[1], 5.5 * sc, skia.Paint(AntiAlias=True, Color=_color(S.HALO, 0.8 * a_alpha)))
            canvas.drawCircle(tip[0], tip[1], 3.8 * sc, skia.Paint(AntiAlias=True, Color=_color(color, a_alpha)))

        if a["label"]:
            pl = S.ease_out_cubic(_prog(t, None if st is None else st + 0.6 * draw_len, S.FADE))
            if pl > 0:
                p, _ = _point_at(P, L, a["label_s"] * L[-1])
                lx, ly = p[0] + a["label_dx"] * sc, p[1] + a["label_dy"] * sc
                _draw_text(canvas, a["label"], lx, ly + 5 * sc * (1 - pl), size=ARROW_LABEL * sc, kind="serif_italic",
                           color=_mix(S.INK, color, 0.25), alpha=pl * _edge_fade(lx, ly, sc, width, height), halo=0.2)


def _star(cx, cy, r_out, r_in, n):
    p = skia.Path()
    for i in range(2 * n):
        r = r_out if i % 2 == 0 else r_in
        ang = -math.pi / 2 + i * math.pi / n
        x, y = cx + r * math.cos(ang), cy + r * math.sin(ang)
        p.moveTo(x, y) if i == 0 else p.lineTo(x, y)
    p.close()
    return p


def _draw_glyph(canvas, style, x, y, s, alpha, sc):
    halo = skia.Paint(AntiAlias=True, Color=_color(S.HALO, 0.9 * alpha), Style=skia.Paint.kStroke_Style)
    fill = skia.Paint(AntiAlias=True)
    if style == "city":
        r = MARKER_R["city"] * sc * s
        canvas.drawCircle(x, y, r + 2.2 * sc, skia.Paint(AntiAlias=True, Color=_color(S.HALO, 0.85 * alpha)))
        fill.setColor(_color(S.GOLD, alpha))
        canvas.drawCircle(x, y, r, fill)
    elif style == "capital":
        r = MARKER_R["capital"] * sc * s
        canvas.drawCircle(x, y, r + 2.2 * sc, skia.Paint(AntiAlias=True, Color=_color(S.HALO, 0.9 * alpha)))
        ring = skia.Paint(AntiAlias=True, Color=_color(S.GOLD, alpha), Style=skia.Paint.kStroke_Style, StrokeWidth=2.4 * sc * s)
        canvas.drawCircle(x, y, r - 1.2 * sc * s, ring)
        q = r * 0.42
        fill.setColor(_color(S.GOLD, alpha))
        canvas.drawRect(skia.Rect(x - q, y - q, x + q, y + q), fill)
    elif style == "dot":
        r = MARKER_R["dot"] * sc * s
        canvas.drawCircle(x, y, r + 1.8 * sc, skia.Paint(AntiAlias=True, Color=_color(S.HALO, 0.85 * alpha)))
        fill.setColor(_color(S.INK, alpha))
        canvas.drawCircle(x, y, r, fill)
    elif style == "uprising":
        r = MARKER_R["uprising"] * sc * s
        star = _star(x, y, r, r * 0.5, 8)
        halo.setStrokeWidth(3.2 * sc)
        halo.setStrokeJoin(skia.Paint.kRound_Join)
        canvas.drawPath(star, halo)
        fill.setColor(_color(S.RED, alpha))
        canvas.drawPath(star, fill)
        canvas.drawCircle(x, y, r * 0.3, skia.Paint(AntiAlias=True, Color=_color(S.GOLD, alpha)))
    elif style == "battle":
        r = MARKER_R["battle"] * sc * s
        canvas.drawCircle(x, y, r + 1.5 * sc, skia.Paint(AntiAlias=True, Color=_color(S.HALO, 0.72 * alpha)))
        canvas.drawCircle(x, y, r + 1.5 * sc, skia.Paint(AntiAlias=True, Color=_color(S.RED, 0.9 * alpha),
                                                          Style=skia.Paint.kStroke_Style, StrokeWidth=1.6 * sc))
        e = r * 0.72
        # crossguards drawn as separate short strokes for both blades
        stroke = skia.Paint(AntiAlias=True, Color=_color(S.INK, alpha), Style=skia.Paint.kStroke_Style,
                            StrokeWidth=2.3 * sc * s, StrokeCap=skia.Paint.kRound_Cap)
        guards = skia.Path()
        for sgn in (1, -1):
            gx, gy = x + sgn * e * 0.42, y + e * 0.42
            g = e * 0.36
            # perpendicular to blade direction (sgn, 1)/sqrt2 is (1, -sgn)/sqrt2
            guards.moveTo(gx - g * 0.7071, gy + sgn * g * 0.7071)
            guards.lineTo(gx + g * 0.7071, gy - sgn * g * 0.7071)
        blades = skia.Path()
        for sgn in (1, -1):
            blades.moveTo(x - sgn * e, y - e)
            blades.lineTo(x + sgn * e, y + e)
        canvas.drawPath(blades, stroke)
        canvas.drawPath(guards, stroke)


def _draw_markers(canvas, anim, t, duration, out, sc, width, height):
    for m in anim["markers"]:
        st = _start(m["at"], duration)
        p = _prog(t, st, S.POP)
        if p <= 0:
            continue
        x, y = out(m["x"], m["y"])
        r0 = MARKER_R[m["style"]] * sc
        ring_col = S.RED if m["style"] in ("uprising", "battle") else S.GOLD
        # one-shot ring as it lands
        if st is not None:
            q = _prog(t, st, 1.1)
            if 0 < q < 1:
                rp = skia.Paint(AntiAlias=True, Color=_color(ring_col, 0.85 * (1 - q) ** 1.5),
                                Style=skia.Paint.kStroke_Style, StrokeWidth=2.4 * sc)
                canvas.drawCircle(x, y, r0 + 34 * sc * S.ease_out_cubic(q), rp)
        if m["pulse"]:
            t0 = (st if st is not None else S.SHOT_INTRO) + S.POP
            if t > t0:
                period = 1.8
                ph = ((t - t0) % period) / period
                glow = skia.Paint(AntiAlias=True, Color=_color(ring_col, 0.16))
                canvas.drawCircle(x, y, r0 + 9 * sc, glow)
                rp = skia.Paint(AntiAlias=True, Color=_color(ring_col, 0.7 * (1 - ph) ** 1.6),
                                Style=skia.Paint.kStroke_Style, StrokeWidth=2.2 * sc)
                canvas.drawCircle(x, y, r0 + 3 * sc + 26 * sc * S.ease_out_cubic(ph), rp)
        s = S.ease_out_back(p)
        _draw_glyph(canvas, m["style"], x, y, s, min(1.0, p * 3), sc)
        if m["label"]:
            pl = S.ease_out_cubic(_prog(t, None if st is None else st + 0.15, S.FADE))
            size = (CAPITAL_LABEL if m["style"] == "capital" else MARKER_LABEL) * sc
            kind = "serif_bold" if m["style"] == "capital" else "serif"
            slide = 10 * sc * (1 - pl) * (m["side"] or 0)
            lx, ly = x + m["dx"] * sc, y + m["dy"] * sc
            _draw_text(canvas, m["label"], lx - slide, ly, size=size, kind=kind, color=S.INK,
                       alpha=pl * _edge_fade(lx, ly, sc, width, height), halo=0.2)


def _draw_title(canvas, anim, t, sc, width, height):
    if not anim.get("title"):
        return
    p = S.ease_in_out(S.clamp01((t - 0.1) / S.FADE))
    if p <= 0:
        return
    shade = _top_shade(width, height)
    shade.setAlphaf(p)
    canvas.drawRect(skia.Rect(0, 0, width, height * 0.2), shade)
    cx, cy = TITLE_BOX[0] * sc, TITLE_BOX[1] * sc
    _draw_text(canvas, anim["title"], cx, cy + 6 * sc * (1 - p), size=TITLE_SIZE * sc, color=S.INK, alpha=p,
               halo=0.12, spacing=0.02)
    w, _ = text_size(anim["title"], "serif", TITLE_SIZE * sc, 0.02)
    half = min(w * 0.32, 220 * sc) * p
    rule = skia.Paint(AntiAlias=True, Color=_color(S.GOLD, 0.9 * p), StrokeWidth=1.6 * sc)
    yy = cy + TITLE_SIZE * sc * 0.62
    canvas.drawLine(cx - half, yy, cx + half, yy, rule)


def _draw_stamps(canvas, anim, t, duration, sc, width):
    stamps = anim["stamps"]
    if not stamps:
        return
    box = _stamp_box(stamps)
    x0, y0, x1, y1 = (v * sc for v in box)
    # panel fades in with the first stamp
    first = _start(stamps[0]["at"], duration) if stamps[0]["at"] > 0 else 0.0
    pp = S.ease_in_out(_prog(t, first, S.FADE)) if first else S.ease_in_out(S.clamp01((t - 0.1) / S.FADE))
    if pp <= 0:
        return
    rr = skia.RRect.MakeRectXY(skia.Rect(x0, y0, x1, y1), 4 * sc, 4 * sc)
    canvas.drawRRect(rr, skia.Paint(AntiAlias=True, Color=_color(S.BG_2, 0.88 * pp)))
    canvas.drawRRect(rr, skia.Paint(AntiAlias=True, Color=_color(S.GOLD, 0.35 * pp), Style=skia.Paint.kStroke_Style,
                                    StrokeWidth=1.0 * sc))
    canvas.drawRect(skia.Rect(x0 + 14 * sc, y1 - 12 * sc, x1 - 14 * sc, y1 - 10.4 * sc),
                    skia.Paint(AntiAlias=True, Color=_color(S.GOLD, 0.9 * pp)))
    # current + previous stamp
    starts = [(_start(s["at"], duration) if s["at"] > 0 else 0.0) for s in stamps]
    cur = max([i for i, s0 in enumerate(starts) if t >= s0] or [0])
    cy = (y0 + y1 - 8 * sc) / 2
    cx = (x0 + x1) / 2
    canvas.save()
    canvas.clipRect(skia.Rect(x0, y0 + 2 * sc, x1, y1 - 13 * sc))
    trans = 0.55
    q = S.ease_in_out(S.clamp01((t - starts[cur]) / trans)) if cur > 0 or starts[0] > 0 else 1.0
    if cur > 0 and q < 1:
        _draw_text(canvas, stamps[cur - 1]["text"], cx, cy - 24 * sc * q, size=STAMP_SIZE * sc, spacing=0.04,
                   color=S.INK, alpha=pp * (1 - q), halo=0)
    _draw_text(canvas, stamps[cur]["text"], cx, cy + 24 * sc * (1 - q), size=STAMP_SIZE * sc, spacing=0.04,
               color=S.INK, alpha=pp * q, halo=0)
    canvas.restore()
