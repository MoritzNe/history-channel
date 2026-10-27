"""Antique-styled historical maps from a MapSpec (matplotlib + geopandas + Natural Earth 1:50m).

Modern admin-0 borders are only an approximation for historical periods, so borders are
drawn faintly; markers, arrows and highlighted regions carry the story.
"""

from __future__ import annotations

import functools
import logging
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patheffects as pe  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import requests  # noqa: E402
from matplotlib.patches import FancyArrowPatch, Rectangle  # noqa: E402
from PIL import Image  # noqa: E402

from historychannel import CACHE_DIR  # noqa: E402
from historychannel.models import MapSpec  # noqa: E402

from .prep import OUT_H, OUT_W, grade, texture  # noqa: E402
from .wikimedia import USER_AGENT  # noqa: E402

log = logging.getLogger(__name__)

NE_DIR = CACHE_DIR / "naturalearth"
NE_BASE = "https://naciscdn.org/naturalearth/50m"
LAYERS = {
    "countries": "cultural/ne_50m_admin_0_countries.zip",
    "rivers": "physical/ne_50m_rivers_lake_centerlines.zip",
    "lakes": "physical/ne_50m_lakes.zip",
}

# palette ---------------------------------------------------------------------
SEA = "#141b1f"
LAND = "#3b3427"
LAND_EDGE = "#8a7650"
BORDER = "#6f6046"
WATER_LINE = "#3c5560"
GRATICULE = "#c9a45c"
GOLD = "#e0b860"
RED = "#b8432f"
INK = "#efe3c6"
HALO = "#0d0b08"
REGION_PALETTE = ["#b8432f", "#c9a45c", "#5f7f6a", "#6d6a9c", "#a0663a"]
SERIF = ["Baskerville", "Hoefler Text", "Georgia", "Palatino", "Times New Roman", "DejaVu Serif"]

DPI = 200
FIG_W, FIG_H = OUT_W / DPI, OUT_H / DPI  # 19.2 x 10.8 in


def _ensure(layer: str) -> Path:
    NE_DIR.mkdir(parents=True, exist_ok=True)
    rel = LAYERS[layer]
    path = NE_DIR / Path(rel).name
    if not path.exists() or path.stat().st_size < 1000:
        url = f"{NE_BASE}/{rel}"
        log.info("downloading %s", url)
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=120)
        r.raise_for_status()
        tmp = path.with_suffix(".part")
        tmp.write_bytes(r.content)
        tmp.replace(path)
    return path


@functools.cache
def load_layer(layer: str):
    import geopandas as gpd

    return gpd.read_file(f"zip://{_ensure(layer)}")


def fit_extent_16x9(extent: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Pad (lon_min, lon_max, lat_min, lat_max) so that, drawn with a cos(lat) aspect, it is 16:9."""
    lon0, lon1, lat0, lat1 = extent
    k = math.cos(math.radians((lat0 + lat1) / 2))
    w, h = (lon1 - lon0) * k, lat1 - lat0
    target = OUT_W / OUT_H
    if w / h < target:  # too tall -> widen
        extra = (h * target / k - (lon1 - lon0)) / 2
        lon0, lon1 = lon0 - extra, lon1 + extra
    else:  # too wide -> heighten
        extra = (w / target - h) / 2
        lat0, lat1 = lat0 - extra, lat1 + extra
    return lon0, lon1, lat0, lat1


def _halo(width=6):
    return [pe.withStroke(linewidth=width, foreground=HALO, alpha=0.85)]


def _countries_geom(names: list[str]):
    gdf = load_layer("countries")
    wanted = {n.strip().lower() for n in names}
    cols = [c for c in ("NAME", "NAME_LONG", "ADMIN", "SOVEREIGNT", "NAME_EN", "ISO_A3") if c in gdf.columns]
    mask = np.zeros(len(gdf), dtype=bool)
    for c in cols:
        mask |= gdf[c].astype(str).str.lower().isin(wanted).to_numpy()
    if not mask.any():
        log.warning("map region: no Natural Earth country matched %s", names)
        return None
    return gdf[mask].union_all()


class _LabelPlacer:
    """Greedy non-overlapping label placement in display space."""

    OFFSETS = [(1, 0.15), (1, -1), (-1, 0.15), (0, 1.3), (0, -1.6), (-1, -1), (1, 1.1), (-1, 1.1)]

    def __init__(self, ax, fig):
        self.ax, self.fig = ax, fig
        self.renderer = fig.canvas.get_renderer()
        self.boxes = []

    def reserve(self, bbox):
        self.boxes.append(bbox.expanded(1.08, 1.15))

    def place(self, x, y, text, *, size, color=INK, style="normal", weight="normal", gap_pt=14):
        best = None
        for dx, dy in self.OFFSETS:
            ha = "left" if dx > 0 else ("right" if dx < 0 else "center")
            va = "center" if abs(dy) < 0.5 else ("bottom" if dy > 0 else "top")
            off = (dx * gap_pt, dy * gap_pt if abs(dy) >= 0.5 else dy * gap_pt)
            t = self.ax.annotate(
                text, (x, y), xytext=off, textcoords="offset points", ha=ha, va=va,
                fontsize=size, color=color, family=SERIF, style=style, weight=weight,
                path_effects=_halo(5), zorder=12,
            )
            bb = t.get_window_extent(self.renderer)
            axbb = self.ax.get_window_extent(self.renderer)
            inside = axbb.contains(bb.x0, bb.y0) and axbb.contains(bb.x1, bb.y1)
            hits = sum(bb.overlaps(b) for b in self.boxes)
            cost = hits * 10 + (0 if inside else 5)
            if best is None or cost < best[0]:
                if best:
                    best[1].remove()
                best = (cost, t, bb)
            else:
                t.remove()
            if cost == 0:
                break
        self.reserve(best[2])
        return best[1]


def _place_region_label(ax, placer: _LabelPlacer, geom, text: str) -> None:
    from shapely.geometry import Point

    rp = geom.representative_point()
    x0, y0, x1, y1 = geom.bounds
    grid = [(x0 + (x1 - x0) * fx, y0 + (y1 - y0) * fy) for fy in (0.5, 0.35, 0.65, 0.2, 0.8) for fx in (0.5, 0.3, 0.7, 0.15, 0.85)]
    candidates = [(rp.x, rp.y)] + [p for p in grid if geom.contains(Point(p))]
    best = None
    for x, y in candidates:
        # letter-spacing is not supported by matplotlib; small caps feel comes from upper + size
        t = ax.text(x, y, text, ha="center", va="center", fontsize=22, color=INK, alpha=0.9,
                    family=SERIF, zorder=11, path_effects=_halo(6))
        bb = t.get_window_extent(placer.renderer)
        hits = sum(bb.overlaps(b) for b in placer.boxes)
        if best is None or hits < best[0]:
            if best:
                best[1].remove()
            best = (hits, t, bb)
        else:
            t.remove()
        if hits == 0:
            break
    placer.reserve(best[2])


def render_map(spec: MapSpec, out: Path, *, seed: int = 0) -> Path:
    """Render `spec` to a 3840x2160 PNG at `out`."""
    lon0, lon1, lat0, lat1 = fit_extent_16x9(spec.extent)
    midlat = (lat0 + lat1) / 2

    plt.rcParams["font.family"] = SERIF
    fig = plt.figure(figsize=(FIG_W, FIG_H), dpi=DPI, facecolor=SEA)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(SEA)
    ax.set_xlim(lon0, lon1)
    ax.set_ylim(lat0, lat1)
    ax.set_aspect(1 / math.cos(math.radians(midlat)), adjustable="box")
    ax.set_axis_off()

    from shapely.geometry import Polygon, box

    view = box(lon0 - 2, lat0 - 2, lon1 + 2, lat1 + 2)
    countries = load_layer("countries").clip(view)

    # soft coastal glow, land, borders
    countries.plot(ax=ax, aspect=None, color="none", edgecolor=LAND_EDGE, linewidth=5.0, alpha=0.10, zorder=1)
    countries.plot(ax=ax, aspect=None, color=LAND, edgecolor="none", zorder=2)
    countries.boundary.plot(ax=ax, aspect=None, color=BORDER, linewidth=0.7, alpha=0.55, linestyle=(0, (4, 2)), zorder=3)
    countries.dissolve().boundary.plot(ax=ax, aspect=None, color=LAND_EDGE, linewidth=1.1, alpha=0.8, zorder=3)
    for layer, kw in (("lakes", dict(color=SEA, edgecolor=WATER_LINE, linewidth=0.6)),
                      ("rivers", dict(color=WATER_LINE, linewidth=0.9, alpha=0.8))):
        try:
            load_layer(layer).clip(view).plot(ax=ax, aspect=None, zorder=4, **kw)
        except Exception as e:  # decorative layers are optional
            log.warning("map: skipping %s layer (%s)", layer, e)

    # faint graticule every 5 degrees
    for lo in range(int(lon0 // 5 * 5), int(lon1) + 5, 5):
        ax.axvline(lo, color=GRATICULE, lw=0.5, alpha=0.10, zorder=5)
    for la in range(int(lat0 // 5 * 5), int(lat1) + 5, 5):
        ax.axhline(la, color=GRATICULE, lw=0.5, alpha=0.10, zorder=5)

    fig.canvas.draw()
    placer = _LabelPlacer(ax, fig)

    # regions -------------------------------------------------------------------
    region_labels = []
    for i, reg in enumerate(spec.regions):
        color = reg.color or REGION_PALETTE[i % len(REGION_PALETTE)]
        geom = None
        if reg.polygon and len(reg.polygon) >= 3:
            geom = Polygon(reg.polygon)
        elif reg.countries:
            geom = _countries_geom(reg.countries)
        if geom is None or geom.is_empty:
            continue
        geom = geom.intersection(view)
        import geopandas as gpd

        g = gpd.GeoSeries([geom])
        g.plot(ax=ax, aspect=None, color=color, alpha=0.22, edgecolor="none", zorder=6)
        g.boundary.plot(ax=ax, aspect=None, color=color, linewidth=2.2, alpha=0.9, zorder=7)
        if reg.label:
            vis = geom.intersection(box(lon0, lat0, lon1, lat1))
            region_labels.append((vis if not vis.is_empty else geom, reg.label))

    arrow_labels: list[tuple[float, float, str]] = []
    # arrows --------------------------------------------------------------------
    for arr in spec.arrows:
        (x0, y0), (x1, y1) = arr.from_lonlat, arr.to_lonlat
        rad = 0.25
        for lw, col, z in ((11, HALO, 8), (6, RED, 9)):
            ax.add_patch(FancyArrowPatch(
                (x0, y0), (x1, y1), connectionstyle=f"arc3,rad={rad}",
                arrowstyle=f"-|>,head_length={0.9 if lw < 10 else 1.0},head_width={0.5 if lw < 10 else 0.6}",
                mutation_scale=30, lw=lw, color=col, alpha=0.95 if col == RED else 0.6,
                shrinkA=10, shrinkB=18, zorder=z, joinstyle="round", capstyle="round",
            ))
        if arr.label:
            # control point of the arc3 curve (matplotlib uses rad * |d| perpendicular offset)
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            dx, dy = x1 - x0, y1 - y0
            cx, cy = mx + rad * dy, my - rad * dx
            lx, ly = (mx + cx) / 2, (my + cy) / 2  # point on the curve (t=0.5)
            arrow_labels.append((lx, ly, arr.label))
        ax.scatter([x0], [y0], s=90, facecolor=HALO, edgecolor=RED, linewidths=2.5, zorder=10)

    # markers -------------------------------------------------------------------
    pts = []
    for m in spec.markers:
        ax.scatter([m.lon], [m.lat], s=520, color=GOLD, alpha=0.18, zorder=13, linewidths=0)
        ax.scatter([m.lon], [m.lat], s=150, color=GOLD, edgecolor=HALO, linewidths=2.2, zorder=14)
        ax.scatter([m.lon], [m.lat], s=22, color=HALO, zorder=15, linewidths=0)
        pts.append(m)
    fig.canvas.draw()
    for m in pts:  # reserve marker dots before placing labels
        px, py = ax.transData.transform((m.lon, m.lat))
        from matplotlib.transforms import Bbox

        placer.reserve(Bbox.from_extents(px - 18, py - 18, px + 18, py + 18))
    for m in pts:
        placer.place(m.lon, m.lat, m.label or m.name, size=27, gap_pt=13)
    for x, y, text in arrow_labels:
        placer.place(x, y, text, size=22, color="#f0c9b8", style="italic", gap_pt=10)
    # Region labels go last so they can dodge marker dots and labels: try the
    # representative point, then a grid of points inside the region.
    for geom, text in region_labels:
        _place_region_label(ax, placer, geom, text.upper())

    # title cartouche + frame -----------------------------------------------------
    if spec.title:
        # darken the top band so the title reads over any geography
        grad = np.linspace(0.6, 0, 64)[:, None] ** 1.5
        shade = np.zeros((64, 2, 4))
        shade[..., 3] = grad
        ax.imshow(shade, extent=(0, 1, 0.78, 1), transform=ax.transAxes, aspect="auto",
                  interpolation="bicubic", zorder=19)
        ax.text(0.5, 0.935, spec.title, transform=ax.transAxes, ha="center", va="center", fontsize=46,
                color=INK, family=SERIF, zorder=20, path_effects=_halo(9))
        ax.plot([0.40, 0.60], [0.885, 0.885], transform=ax.transAxes, color=GOLD, lw=1.6, zorder=20)
    for inset, lw in ((0.018, 2.0), (0.026, 0.8)):
        ax.add_patch(Rectangle((inset * 9 / 16, inset), 1 - 2 * inset * 9 / 16, 1 - 2 * inset,
                               transform=ax.transAxes, fill=False, ec=GOLD, lw=lw, alpha=0.55, zorder=21))

    fig.canvas.draw()
    rgba = np.asarray(fig.canvas.buffer_rgba())[..., :3].astype(np.float32)
    plt.close(fig)

    # parchment mottling over everything, then house grade
    rgba *= 1 + 0.06 * texture((rgba.shape[1], rgba.shape[0]), seed=seed + 7)[..., None]
    img = Image.fromarray(rgba.clip(0, 255).astype(np.uint8))
    if img.size != (OUT_W, OUT_H):
        img = img.resize((OUT_W, OUT_H), Image.Resampling.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    grade(img, warmth=0.06, desaturate=0.0, vignette=0.30, grain=2.0, seed=seed).save(out)
    return out
