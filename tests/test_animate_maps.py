from __future__ import annotations

import math
import pickle
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from historychannel import BUILD_DIR
from historychannel.animate import _mapbase as MB
from historychannel.animate import maps as M
from historychannel.animate import render_frame
from historychannel.animate import style as S
from historychannel.models import (
    Clip,
    MapArrow,
    MapMarker,
    MapRegion,
    MapSpec,
    MapStamp,
    MapText,
    Timeline,
    Visual,
)
from historychannel.render.video import FrameRenderer, build_plan

SAMPLES = BUILD_DIR / "samples" / "animate"
PARIS, VIENNA, BERLIN = (2.35, 48.86), (16.37, 48.21), (13.40, 52.52)


def demo_spec(**kw) -> MapSpec:
    base = dict(
        title="Revolution Spreads",
        extent=(0.0, 24.0, 44.0, 56.0),
        camera_to=(9.0, 18.0, 50.0, 54.5),
        basemap="modern",
        regions=[MapRegion(label="Box", polygon=[(10, 50), (16, 50), (16, 53), (10, 53)], at=0.1, hatch=True)],
        markers=[
            MapMarker(name="Paris", lon=PARIS[0], lat=PARIS[1], style="uprising", at=0.05),
            MapMarker(name="Vienna", lon=VIENNA[0], lat=VIENNA[1], style="capital", at=0.3),
            MapMarker(name="Berlin", lon=BERLIN[0], lat=BERLIN[1], style="capital", at=0.5, pulse=True),
            MapMarker(name="Potsdam", lon=13.06, lat=52.40, style="dot", at=0.5),
        ],
        arrows=[
            MapArrow(from_lonlat=PARIS, to_lonlat=VIENNA, at=0.15, label="Revolution spreads"),
            MapArrow(from_lonlat=PARIS, to_lonlat=BERLIN, at=0.25, style="flow"),
            MapArrow(from_lonlat=VIENNA, to_lonlat=BERLIN, at=0.3, style="route", via=[(15.0, 50.5)]),
            MapArrow(from_lonlat=BERLIN, to_lonlat=(10.0, 51.0), at=0.3, style="retreat"),
        ],
        texts=[MapText(text="Baltic Sea", lon=17, lat=55.5, at=0.2)],
        stamps=[MapStamp(at=0.0, text="24 Feb 1848"), MapStamp(at=0.5, text="18 Mar 1848")],
    )
    base.update(kw)
    return MapSpec(**base)


@pytest.fixture(scope="module")
def anim(tmp_path_factory) -> dict:
    """An anim dict over a synthetic base image (no network, no matplotlib)."""
    spec = demo_spec()
    layout = MB.plan_layout(spec.extent, spec.camera_to)
    w, h = layout.proj.w, layout.proj.h
    yy, xx = np.mgrid[0:h:4, 0:w:4].astype(np.float32)
    small = np.stack([40 + 60 * xx / w, 50 + 40 * yy / h, np.full_like(xx, 45)], -1).astype(np.uint8)
    path = tmp_path_factory.mktemp("mapbase") / "base.png"
    Image.fromarray(small).resize((w, h)).save(path, compress_level=1)
    return M.build_anim(spec, path, layout)


# --------------------------------------------------------------------------- projection / camera


def test_projection_is_affine_and_invertible():
    lay = MB.plan_layout((2.0, 24.0, 44.0, 56.0), (10.0, 16.0, 50.0, 54.0))
    p = lay.proj
    lon = np.array([2.0, 13.4, 24.0])
    lat = np.array([44.0, 52.5, 56.0])
    x, y = p(lon, lat)
    lon2, lat2 = p.inverse(x, y)
    assert np.allclose(lon, lon2) and np.allclose(lat, lat2)
    # equirectangular with x scaled by cos(midlat): one degree of lon = k * one degree of lat
    assert math.isclose(p.a / -p.c, lay.k, rel_tol=1e-3)
    assert y[2] < y[0]  # north is up
    assert p.w / p.h == pytest.approx(16 / 9, rel=1e-3)
    for cam in lay.cams:
        assert (cam[2] - cam[0]) / (cam[3] - cam[1]) == pytest.approx(16 / 9, rel=1e-3)
        assert cam[0] >= -1e-6 and cam[1] >= -1e-6 and cam[2] <= p.w + 1e-6 and cam[3] <= p.h + 1e-6
    tight = min(c[2] - c[0] for c in lay.cams)
    assert tight >= MB.SUPERSAMPLE * 1920 or p.w == MB.MAX_BASE_W


def test_base_size_capped_for_deep_zoom():
    lay = MB.plan_layout((-10.0, 40.0, 35.0, 60.0), (13.0, 14.0, 52.2, 52.8))
    assert lay.proj.w == MB.MAX_BASE_W


def test_camera_eases_from_extent_to_camera_to(anim):
    c0 = M.camera(anim, 0.0, 10.0)
    c1 = M.camera(anim, 10.0, 10.0)
    assert np.allclose(c0, anim["cam0"], atol=0.5) and np.allclose(c1, anim["cam1"], atol=0.5)
    widths = [M.camera(anim, t, 10.0)[2] - M.camera(anim, t, 10.0)[0] for t in np.linspace(0, 10, 21)]
    assert all(a >= b - 1e-6 for a, b in zip(widths, widths[1:]))  # monotone zoom-in
    # ease: slow at both ends
    assert widths[0] - widths[1] < widths[9] - widths[10]
    assert widths[-2] - widths[-1] < widths[9] - widths[10]
    # t outside [0, duration] (crossfades) clamps
    assert np.allclose(M.camera(anim, -1.0, 10.0), c0, atol=1e-6)


def test_static_camera_still_drifts(anim):
    still = dict(anim, cam1=None)
    a, b = M.camera(still, 0.0, 10.0), M.camera(still, 10.0, 10.0)
    wa, wb = a[2] - a[0], b[2] - b[0]
    assert 0.9 < wb / wa < 0.995


def test_start_times_respect_intro_and_none():
    assert M._start(None, 10) is None
    assert M._start(0.0, 10) == S.SHOT_INTRO
    assert M._start(0.5, 10) == 5.0
    assert M._prog(0.0, None, 1.0) == 1.0
    assert M._prog(5.0, 5.0, 1.0) == 0.0
    assert M._prog(5.5, 5.0, 1.0) == pytest.approx(0.5)
    assert S.ease_out_back(1.0) == pytest.approx(1.0)
    assert max(S.ease_out_back(x) for x in np.linspace(0, 1, 50)) > 1.0  # overshoot


def test_curve_ends_at_endpoints_and_passes_waypoints():
    P = M._curve([(0, 0), (100, 0)])
    assert np.allclose(P[0], (0, 0)) and np.allclose(P[-1], (100, 0))
    assert P[:, 1].min() < -5  # bends
    Q = M._curve([(0, 0), (50, 40), (100, 0)])
    assert np.allclose(Q[0], (0, 0)) and np.allclose(Q[-1], (100, 0), atol=1e-6)
    assert np.min(np.hypot(Q[:, 0] - 50, Q[:, 1] - 40)) < 1.0


# --------------------------------------------------------------------------- labels


def test_label_placement_is_deterministic_and_resolution_independent(anim, tmp_path):
    spec = demo_spec()
    layout = MB.plan_layout(spec.extent, spec.camera_to)
    again = M.build_anim(spec, Path(anim["base"]["path"]), layout)
    for a, b in zip(anim["markers"], again["markers"]):
        assert (a["dx"], a["dy"]) == (b["dx"], b["dy"])
    for a, b in zip(anim["arrows"], again["arrows"]):
        assert (a["label_s"], a["label_dx"], a["label_dy"]) == (b["label_s"], b["label_dx"], b["label_dy"])
    assert anim["id"] == again["id"]


def test_close_markers_get_non_overlapping_labels(anim):
    berlin, potsdam = anim["markers"][2], anim["markers"][3]
    cam = anim["cam1"]

    def box(m, kind, size):
        rx, ry = M._to_ref(cam, m["x"], m["y"])
        w, h = M.text_size(m["label"], kind, size)
        return M._box(float(rx) + m["dx"], float(ry) + m["dy"], w, h, pad=0)

    b1 = box(berlin, "serif_bold", M.CAPITAL_LABEL)
    b2 = box(potsdam, "serif", M.MARKER_LABEL)
    assert M._Boxes.overlap(b1, b2) == 0.0


def test_anim_is_json_and_pickle_safe(anim):
    import json

    assert json.loads(json.dumps(anim)) == anim
    assert pickle.loads(pickle.dumps(anim)) == anim


# --------------------------------------------------------------------------- frames


@pytest.mark.parametrize("size", [(1920, 1080), (960, 540)])
def test_frame_shape_dtype(anim, size):
    w, h = size
    f = render_frame(anim, 4.0, 10.0, w, h)
    assert f.shape == (h, w, 3) and f.dtype == np.uint8 and f.flags.c_contiguous and f.flags.writeable
    assert f.std() > 5


def test_preview_matches_full_resolution(anim):
    big = render_frame(anim, 8.0, 10.0, 1920, 1080).astype(np.float32)
    small = render_frame(anim, 8.0, 10.0, 960, 540).astype(np.float32)
    down = np.asarray(Image.fromarray(big.astype(np.uint8)).resize((960, 540), Image.Resampling.BOX), np.float32)
    assert np.abs(down - small).mean() < 6.0


def test_animation_progresses(anim):
    a = render_frame(anim, 0.0, 10.0, 960, 540)
    b = render_frame(anim, 10.0, 10.0, 960, 540)
    assert np.abs(a.astype(int) - b.astype(int)).mean() > 2
    # deterministic: same t, same pixels
    assert np.array_equal(render_frame(anim, 3.3, 10.0, 960, 540), render_frame(anim, 3.3, 10.0, 960, 540))


def test_nothing_moves_during_intro(anim):
    """Only the camera ease (tiny at t~0) may change before SHOT_INTRO for elements with `at`."""
    still = dict(anim, cam1=None, title=None, stamps=[])
    still["id"] = anim["id"] + "-still"
    a = render_frame(still, 0.0, 10.0, 960, 540).astype(int)
    b = render_frame(still, S.SHOT_INTRO * 0.95, 10.0, 960, 540).astype(int)
    assert np.abs(a - b).mean() < 1.0


def test_frame_time_budget(anim):
    render_frame(anim, 1.0, 10.0, 1920, 1080)  # warm caches
    times = []
    for t in np.linspace(0, 10, 25):
        t0 = time.perf_counter()
        render_frame(anim, float(t), 10.0, 1920, 1080)
        times.append(time.perf_counter() - t0)
    assert float(np.mean(times)) < 0.12  # target is <= 40 ms; generous for loaded CI machines


# --------------------------------------------------------------------------- renderer integration


def _timeline(anim, poster: Path) -> Timeline:
    return Timeline(clips=[Clip(image=poster, start=0.0, duration=4.0, animation=anim)], narration=[], duration=4.0,
                    burn_subtitles=False)


def test_renderer_uses_animation(anim, tmp_path):
    poster = tmp_path / "poster.png"
    Image.new("RGB", (3840, 2160), (200, 0, 0)).save(poster)
    plan = build_plan(_timeline(anim, poster), 960, 540)
    f = FrameRenderer(plan).frame(60)  # t = 2.0 s, no fades
    ref = render_frame(anim, 2.0, 4.0, 960, 540)
    assert np.array_equal(f, ref)


def test_renderer_falls_back_to_ken_burns(tmp_path, caplog):
    poster = tmp_path / "poster.png"
    Image.new("RGB", (3840, 2160), (200, 0, 0)).save(poster)
    bad = {"kind": "map", "base": {"path": str(tmp_path / "missing.png"), "w": 10, "h": 10}}
    r = FrameRenderer(build_plan(_timeline(bad, poster), 960, 540))
    f = r.frame(60)
    assert f.shape == (540, 960, 3)
    assert abs(int(f[270, 480, 0]) - 200) < 4 and f[270, 480, 1] < 4  # the red poster
    r.frame(61)
    assert sum("falling back to Ken Burns" in m for m in caplog.messages) == 1  # logged once


# --------------------------------------------------------------------------- real data


@pytest.mark.network
def test_historical_names_case_insensitive_with_errata():
    g = MB.historical_geom("1815", ["prussia"])
    assert g is not None and g.area > 20
    aus = MB.historical_geom("1815", ["Austrian Empire"])
    assert aus.bounds[0] > 8.0  # Belgium is not part of the Austrian Empire in 1815 (errata)
    nl = MB.historical_geom("1815", ["Netherlands"])
    assert nl.contains(__import__("shapely").geometry.Point(4.35, 50.85))  # Brussels
    assert MB.historical_geom("1815", ["Prusia"]) is None


@pytest.mark.network
@pytest.mark.slow
def test_prepare_real_1815_map():
    SAMPLES.mkdir(parents=True, exist_ok=True)
    spec = demo_spec(
        basemap="1815",
        regions=[
            MapRegion(label="Prussia", historical=["Prussia"], at=0.05),
            MapRegion(label="Austria", historical=["austrian empire"], at=0.1),
        ],
    )
    asset = M.prepare(Visual(kind="map", map=spec), SAMPLES, index=950)
    assert asset.kind == "map" and asset.animation["kind"] == "map"
    assert "historical-basemaps" in asset.attribution and "GPL-3.0" in asset.attribution
    assert Image.open(asset.path).size == (3840, 2160)
    base = Image.open(asset.animation["base"]["path"])
    assert base.size == (asset.animation["base"]["w"], asset.animation["base"]["h"])
    assert len(asset.animation["regions"]) == 2 and all(r["rings"] for r in asset.animation["regions"])
    assert all(r["lx"] is not None for r in asset.animation["regions"])
    f = render_frame(asset.animation, 6.0, 10.0, 1920, 1080)
    Image.fromarray(f).save(SAMPLES / "test_real_1815_t6.png")
    assert f.shape == (1080, 1920, 3)
