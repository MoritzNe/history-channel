"""Prepare-time half of the animated map engine (main process only).

Everything that needs matplotlib / geopandas / shapely lives here:

* historical basemaps (aourednik/historical-basemaps, GPL-3.0) download + cache
* the map projection: equirectangular with x scaled by cos(mid-latitude), stored as an
  affine ``x = a*lon + b, y = c*lat + d`` in base-image pixels
* the static base layer (sea, land, polity tints, borders, rivers, lakes, graticule)
  rendered once at high resolution covering every camera view of the shot
* region geometry resolution (polygon / Natural Earth countries / historical NAMEs) to
  projected, simplified pixel rings, so render workers never import geopandas
"""

from __future__ import annotations

import difflib
import functools
import logging
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import requests

from historychannel import CACHE_DIR
from historychannel.animate import style as S

log = logging.getLogger(__name__)

HB_DIR = CACHE_DIR / "historical-basemaps"
HB_URL = "https://raw.githubusercontent.com/aourednik/historical-basemaps/master/geojson/world_{year}.geojson"
HB_LICENSE = "GPL-3.0"
HB_ATTRIBUTION = f"Historical borders: aourednik/historical-basemaps ({HB_LICENSE})"
NE_ATTRIBUTION = "Map data: Natural Earth (public domain)"

REF_W, REF_H = 1920, 1080  # all overlay sizes are specified in 1080p pixels
MAX_BASE_W = 8192
MIN_BASE_W = 3840
SUPERSAMPLE = 1.25  # tightest camera view keeps >= this many base px per output px
MARGIN = 0.03  # extra base coverage around the union of camera views (fraction of width)

BORDER_LIGHT = "#86765a"  # polity borders: a touch lighter than style.BORDER so they read when zoomed out

# Land tints for neighbouring polities: deliberately close to style.LAND so borders,
# not colour, separate states. Greedy graph colouring keeps neighbours distinct.
POLITY_TINTS = ["#3e3729", "#4a402f", "#373024", "#443b2b", "#4f4430"]


# --------------------------------------------------------------------------- data


def _ensure_hb(year: str) -> Path:
    HB_DIR.mkdir(parents=True, exist_ok=True)
    path = HB_DIR / f"world_{year}.geojson"
    if not path.exists() or path.stat().st_size < 10_000:
        from historychannel.visuals.wikimedia import USER_AGENT

        url = HB_URL.format(year=year)
        log.info("downloading %s", url)
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=120)
        r.raise_for_status()
        tmp = path.with_suffix(".part")
        tmp.write_bytes(r.content)
        tmp.replace(path)
    return path


@functools.cache
def load_historical(year: str):
    import geopandas as gpd

    gdf = gpd.read_file(_ensure_hb(year))
    gdf["geometry"] = gdf.geometry.make_valid()
    for col in ("NAME", "ABBREVN", "SUBJECTO", "PARTOF"):
        if col in gdf.columns:
            gdf[col] = gdf[col].fillna("").astype(str).str.strip()
    _apply_errata(gdf, year)
    return gdf


def _apply_errata(gdf, year: str) -> None:
    """Known data errors in the upstream snapshots, fixed in place.

    1815: the Southern Netherlands (Belgium, centroid ~4.6E 50.6N) is stored as part of
    the "Austrian Empire" multipolygon (it was ceded in 1797/1815); it belonged to the
    United Kingdom of the Netherlands (1815-1830), whose NAME is empty in the data, so
    the fix also gives that polity its NAME.
    """
    if year != "1815":
        return
    from shapely.geometry import MultiPolygon
    from shapely.ops import unary_union

    aus = gdf.index[gdf["NAME"] == "Austrian Empire"]
    nl = gdf.index[gdf["ABBREVN"] == "Netherlands"]
    if len(aus) != 1 or len(nl) != 1:
        return
    geom = gdf.at[aus[0], "geometry"]
    parts = list(getattr(geom, "geoms", [geom]))
    west = [p for p in parts if p.centroid.x < 8.0]
    if not west:
        return
    gdf.at[aus[0], "geometry"] = MultiPolygon([p for p in parts if p.centroid.x >= 8.0])
    gdf.at[nl[0], "geometry"] = unary_union([gdf.at[nl[0], "geometry"], *west])
    gdf.at[nl[0], "NAME"] = "United Kingdom of the Netherlands"


def historical_geom(year: str, names: list[str]):
    """Union of every polygon whose NAME (else ABBREVN, else SUBJECTO) matches one of
    ``names`` case-insensitively. Logs near-matches when a name finds nothing."""
    gdf = load_historical(year)
    parts = []
    for name in names:
        want = name.strip().lower()
        hit = None
        for col in ("NAME", "ABBREVN", "SUBJECTO"):
            if col not in gdf.columns:
                continue
            m = gdf[col].str.lower() == want
            if m.any():
                hit = gdf[m]
                break
        if hit is None:
            pool = sorted({n for n in gdf["NAME"] if n})
            near = difflib.get_close_matches(name, pool, n=6, cutoff=0.5) or [
                n for n in pool if want[:4] in n.lower()
            ][:6]
            log.warning("map region: no %s basemap polygon named %r; near matches: %s", year, name, near)
            continue
        parts.append(hit.geometry.union_all())
    if not parts:
        return None
    from shapely.ops import unary_union

    return unary_union(parts)


# --------------------------------------------------------------------------- projection


@dataclass(frozen=True)
class Projection:
    """lon/lat -> base-image pixels. Linear (equirectangular, x scaled by cos(midlat))."""

    a: float
    b: float
    c: float
    d: float
    w: int  # base image size
    h: int

    def __call__(self, lon, lat):
        return self.a * np.asarray(lon, dtype=float) + self.b, self.c * np.asarray(lat, dtype=float) + self.d

    def inverse(self, x, y):
        return (np.asarray(x, dtype=float) - self.b) / self.a, (np.asarray(y, dtype=float) - self.d) / self.c

    @property
    def bounds_lonlat(self) -> tuple[float, float, float, float]:
        lon0, lat1 = self.inverse(0, 0)
        lon1, lat0 = self.inverse(self.w, self.h)
        return float(lon0), float(lon1), float(lat0), float(lat1)

    def to_dict(self) -> dict:
        return {"a": self.a, "b": self.b, "c": self.c, "d": self.d, "w": self.w, "h": self.h}


def _fit16x9(x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
    """Grow a rect (in equal-area map units) about its centre to 16:9."""
    w, h = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if w / h < 16 / 9:
        w = h * 16 / 9
    else:
        h = w * 9 / 16
    return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2


@dataclass(frozen=True)
class Layout:
    proj: Projection
    cams: list[tuple[float, float, float, float]]  # camera rects in base px, 16:9
    k: float


def plan_layout(extent, camera_to=None, *, max_w: int = MAX_BASE_W) -> Layout:
    """Base-image size + projection covering every camera view, with ``SUPERSAMPLE``
    base px per output px at the tightest view (capped at ``max_w``)."""
    exts = [tuple(extent)] + ([tuple(camera_to)] if camera_to else [])
    lats = [v for e in exts for v in e[2:]]
    k = math.cos(math.radians((min(lats) + max(lats)) / 2))
    # map units: u = lon * k, v = -lat (y grows downward like pixels)
    rects = [_fit16x9(e[0] * k, -e[3], e[1] * k, -e[2]) for e in exts]
    ux0 = min(r[0] for r in rects)
    uy0 = min(r[1] for r in rects)
    ux1 = max(r[2] for r in rects)
    uy1 = max(r[3] for r in rects)
    m = MARGIN * (ux1 - ux0)
    ux0, uy0, ux1, uy1 = _fit16x9(ux0 - m, uy0 - m, ux1 + m, uy1 + m)
    tight = min(r[2] - r[0] for r in rects)
    want = SUPERSAMPLE * REF_W * (ux1 - ux0) / tight
    w = int(min(max_w, max(MIN_BASE_W, math.ceil(want))))
    h = round(w * 9 / 16)
    s = w / (ux1 - ux0)
    sy = h / (uy1 - uy0)  # == s up to rounding of h
    proj = Projection(a=k * s, b=-ux0 * s, c=-sy, d=-uy0 * sy, w=w, h=h)
    cams = [((r[0] - ux0) * s, (r[1] - uy0) * sy, (r[2] - ux0) * s, (r[3] - uy0) * sy) for r in rects]
    return Layout(proj=proj, cams=cams, k=k)


# --------------------------------------------------------------------------- geometry -> pixels


def geom_to_rings(geom, proj: Projection, *, tol_px: float = 1.0, min_area_px: float = 30.0) -> list[list[float]]:
    """Projected, simplified rings (exteriors and holes; fill with even-odd) as flat
    [x0, y0, x1, y1, ...] lists rounded to 0.1 px."""
    from shapely import affinity
    from shapely.geometry import box

    if geom is None or geom.is_empty:
        return []
    lon0, lon1, lat0, lat1 = proj.bounds_lonlat
    pad = 0.15 * (lon1 - lon0)
    geom = geom.intersection(box(lon0 - pad, lat0 - pad, lon1 + pad, lat1 + pad))
    g = affinity.affine_transform(geom, [proj.a, 0, 0, proj.c, proj.b, proj.d])
    g = g.simplify(tol_px, preserve_topology=True)
    rings = []
    polys = getattr(g, "geoms", [g])
    for p in polys:
        if p.geom_type != "Polygon" or p.area < min_area_px:
            continue
        for ring in [p.exterior, *p.interiors]:
            xy = np.asarray(ring.coords)[:, :2]
            if len(xy) >= 4:
                rings.append([round(float(v), 1) for v in xy.ravel()])
    return rings


def region_geom(region, basemap: str):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    from historychannel.visuals.maps import _countries_geom

    parts = []
    if region.polygon and len(region.polygon) >= 3:
        parts.append(Polygon(region.polygon).buffer(0))
    if region.countries:
        g = _countries_geom(region.countries)
        if g is not None:
            parts.append(g)
    if region.historical:
        year = basemap if basemap in ("1815", "1880") else "1815"
        g = historical_geom(year, region.historical)
        if g is not None:
            parts.append(g)
    if not parts:
        return None
    return unary_union(parts)


def label_candidates(geom, proj: Projection, cam) -> list[tuple[float, float]]:
    """Candidate label anchors (base px) inside the part of ``geom`` visible in ``cam``."""
    from shapely.geometry import Point, box

    x0, y0, x1, y1 = cam
    lon_a, lat_a = proj.inverse(x0, y1)
    lon_b, lat_b = proj.inverse(x1, y0)
    # keep labels out of the outer 10 % so they don't hug the frame
    dx, dy = 0.1 * (lon_b - lon_a), 0.1 * (lat_b - lat_a)
    vis = geom.intersection(box(lon_a + dx, lat_a + 2 * dy, lon_b - dx, lat_b - dy))
    if vis.is_empty:
        vis = geom
    # largest piece only (exclaves get no label)
    if hasattr(vis, "geoms"):
        vis = max(vis.geoms, key=lambda g: g.area)
    rp = vis.representative_point()
    try:
        from shapely import maximum_inscribed_circle  # shapely >= 2.1

        mic = maximum_inscribed_circle(vis).coords[0]
        pts = [mic, (rp.x, rp.y)]
    except Exception:
        pts = [(rp.x, rp.y)]
    bx0, by0, bx1, by1 = vis.bounds
    for fy in (0.5, 0.35, 0.65, 0.25, 0.75):
        for fx in (0.5, 0.35, 0.65, 0.2, 0.8):
            p = (bx0 + (bx1 - bx0) * fx, by0 + (by1 - by0) * fy)
            if vis.contains(Point(p)):
                pts.append(p)
    xs, ys = proj(np.array([p[0] for p in pts]), np.array([p[1] for p in pts]))
    return list(zip(xs.tolist(), ys.tolist()))


def inside_fn(geom, proj: Projection):
    """Vectorised point-in-geometry test for base-pixel coordinates."""
    import shapely

    shapely.prepare(geom)

    def inside(x, y):
        lon, lat = proj.inverse(x, y)
        return shapely.contains_xy(geom, lon, lat)

    return inside


# --------------------------------------------------------------------------- base layer


def _greedy_colors(geoms) -> list[int]:
    """Colour index per geometry so touching neighbours differ (welsh-powell-ish)."""
    from shapely.strtree import STRtree

    n = len(geoms)
    tree = STRtree(geoms)
    nbrs = [set() for _ in range(n)]
    for i, g in enumerate(geoms):
        for j in tree.query(g.buffer(0.05)):
            if j != i:
                nbrs[i].add(int(j))
    order = sorted(range(n), key=lambda i: -len(nbrs[i]))
    col = [-1] * n
    for i in order:
        used = {col[j] for j in nbrs[i]}
        c = 0
        while c in used and c < len(POLITY_TINTS) - 1:
            c += 1
        col[i] = c
    return col


def render_base(layout: Layout, basemap: str, out: Path, *, seed: int = 0) -> Path:
    """Static base map (no overlays) at the layout's base size -> PNG at ``out``."""
    import matplotlib

    matplotlib.use("Agg")
    import geopandas as gpd
    import matplotlib.pyplot as plt
    from PIL import Image
    from shapely.geometry import box

    from historychannel.visuals.maps import load_layer
    from historychannel.visuals.prep import grade, texture

    proj = layout.proj
    W, H = proj.w, proj.h
    lon0, lon1, lat0, lat1 = proj.bounds_lonlat
    # line widths are given in 1080p output px at the geometric-mean camera zoom
    cam_w = math.exp(sum(math.log(c[2] - c[0]) for c in layout.cams) / len(layout.cams))
    lw = cam_w / REF_W  # 1080p px -> base px (== points at dpi 72)

    fig = plt.figure(figsize=(W / 72, H / 72), dpi=72, facecolor=S.SEA)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(S.SEA)
    ax.set_xlim(lon0, lon1)
    ax.set_ylim(lat0, lat1)
    ax.set_axis_off()

    pad = 0.1 * (lon1 - lon0)
    view = box(lon0 - pad, lat0 - pad, lon1 + pad, lat1 + pad)
    countries = load_layer("countries").clip(view)
    land = countries.union_all()
    land_s = gpd.GeoSeries([land])

    land_s.boundary.plot(ax=ax, aspect=None, color=S.LAND_EDGE, linewidth=9 * lw, alpha=0.07, zorder=1)
    land_s.boundary.plot(ax=ax, aspect=None, color=S.LAND_EDGE, linewidth=4 * lw, alpha=0.08, zorder=1)
    land_s.plot(ax=ax, aspect=None, color=S.LAND, edgecolor="none", zorder=2)

    if basemap in ("1815", "1880"):
        hb = load_historical(basemap)
        hb = hb[hb.intersects(view)]
        pol = []
        for _, r in hb.iterrows():
            g = r.geometry.intersection(view).intersection(land)
            if not g.is_empty and g.area > 1e-4:
                pol.append((r.NAME or r.get("ABBREVN", ""), g))
        named = [(n, g) for n, g in pol if n]
        cols = _greedy_colors([g for _, g in named]) if named else []
        tints = gpd.GeoDataFrame(
            {"c": [POLITY_TINTS[c] for c in cols]}, geometry=[g for _, g in named], crs=countries.crs
        )
        if len(tints):
            tints.plot(ax=ax, aspect=None, color=tints["c"], edgecolor="none", zorder=3)
        # internal borders only: polity outlines minus the (Natural Earth) coastline
        from shapely.ops import unary_union

        lines = unary_union([g.boundary for _, g in pol])
        inner = lines.intersection(land.buffer(-0.02 * (lon1 - lon0) / 60))
        gs = gpd.GeoSeries([inner])
        gs.plot(ax=ax, aspect=None, color=S.HALO, linewidth=4.0 * lw, alpha=0.3, zorder=5)
        gs.plot(ax=ax, aspect=None, color=BORDER_LIGHT, linewidth=1.7 * lw, alpha=0.9, zorder=5)
    else:  # modern borders
        countries.boundary.plot(ax=ax, aspect=None, color=S.BORDER, linewidth=1.1 * lw, alpha=0.8, zorder=5)

    for layer, kw in (
        ("lakes", dict(color=S.SEA, edgecolor=S.WATER_LINE, linewidth=0.8 * lw, zorder=4)),
        ("rivers", dict(color=S.WATER_LINE, linewidth=1.5 * lw, alpha=0.9, zorder=4)),
    ):
        try:
            load_layer(layer).clip(view).plot(ax=ax, aspect=None, **kw)
        except Exception as e:  # decorative layers are optional
            log.warning("map: skipping %s layer (%s)", layer, e)

    land_s.boundary.plot(ax=ax, aspect=None, color=S.LAND_EDGE, linewidth=1.9 * lw, alpha=0.9, zorder=6)

    step = 5 if (lon1 - lon0) > 12 else 2
    for lo in range(int(math.floor(lon0 / step) * step), int(lon1) + step, step):
        ax.axvline(lo, color=S.GOLD, lw=0.8 * lw, alpha=0.07, zorder=7)
    for la in range(int(math.floor(lat0 / step) * step), int(lat1) + step, step):
        ax.axhline(la, color=S.GOLD, lw=0.8 * lw, alpha=0.07, zorder=7)

    fig.canvas.draw()
    arr = np.asarray(fig.canvas.buffer_rgba())[..., :3].astype(np.float32)
    plt.close(fig)
    if arr.shape[:2] != (H, W):  # matplotlib can be off by a pixel from float inches
        arr = np.asarray(Image.fromarray(arr.astype(np.uint8)).resize((W, H), Image.Resampling.LANCZOS), np.float32)
    # paper mottling at a scale that reads at the mean zoom
    tex = texture((max(64, W // 4), max(36, H // 4)), seed=seed + 7)
    tex = np.asarray(Image.fromarray(tex, mode="F").resize((W, H), Image.Resampling.BILINEAR))
    arr *= 1 + 0.05 * tex[..., None]
    img = Image.fromarray(arr.clip(0, 255).astype(np.uint8))
    img = grade(img, warmth=0.06, desaturate=0.0, vignette=0.0, grain=0.0, seed=seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, compress_level=1)
    return out
