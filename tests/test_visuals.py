from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from historychannel import BUILD_DIR
from historychannel.models import Asset, MapArrow, MapMarker, MapRegion, MapSpec, Visual
from historychannel import visuals as V
from historychannel.visuals import attribution_lines, resolve_visual
from historychannel.visuals.prep import OUT_SIZE, fit_frame, prepare_still
from historychannel.visuals.wikimedia import CommonsClient, CommonsError, clean_html, is_free_license

SAMPLES = BUILD_DIR / "samples" / "visuals"


def _size(p: Path) -> tuple[int, int]:
    with Image.open(p) as im:
        return im.size


# ----------------------------------------------------------------- offline


def test_output_size_is_2x_frame():
    assert OUT_SIZE == (3840, 2160)


def test_title_card(tmp_path):
    a = resolve_visual(Visual(kind="title_card", text="The Springtime of Peoples",
                              subtext="Germany, 1848"), tmp_path, index=0)
    assert a.kind == "title_card" and a.attribution is None
    assert _size(a.path) == OUT_SIZE


def test_title_card_uses_script_title(tmp_path):
    a = resolve_visual(Visual(kind="title_card"), tmp_path, index=0, script_title="1848")
    assert _size(a.path) == OUT_SIZE


def test_quote_card_long_text(tmp_path):
    q = "Not through speeches and majority decisions are the great questions of the day decided " * 3
    a = resolve_visual(Visual(kind="quote", text=q, subtext="Otto von Bismarck"), tmp_path, index=1)
    assert a.kind == "quote" and _size(a.path) == OUT_SIZE


def test_prep_portrait_composed_on_blurred_backdrop():
    # bright red portrait: the composed frame must keep it whole in the centre with darker sides
    portrait = Image.new("RGB", (800, 1200), (220, 40, 40))
    out = fit_frame(portrait)
    assert out.size == OUT_SIZE
    arr = np.asarray(out).astype(int)
    centre = arr[1080, 1920]
    edge = arr[1080, 60]
    assert centre[0] > 200  # subject intact
    assert edge.sum() < centre.sum() * 0.6  # backdrop darkened
    # subject fits vertically (not cropped): top and bottom centre rows near the margin are red
    assert arr[150, 1920][0] > 180 and arr[2010, 1920][0] > 180


def test_prep_landscape_cover_and_grade():
    img = Image.new("RGB", (1600, 900), (128, 128, 128))
    out = prepare_still(img)
    arr = np.asarray(out).astype(int)
    assert out.size == OUT_SIZE
    r, g, b = arr[1080, 1920]
    assert r > b  # warm grade
    assert arr[20, 20].sum() < arr[1080, 1920].sum()  # vignette


def test_prep_handles_odd_modes():
    for mode, color in (("RGBA", (10, 200, 10, 128)), ("L", 128), ("P", 3)):
        out = fit_frame(Image.new(mode, (500, 400), color))
        assert out.mode == "RGB" and out.size == OUT_SIZE


def test_license_filter():
    assert is_free_license("Public domain", "pd")
    assert is_free_license("CC BY-SA 4.0", "cc-by-sa-4.0")
    assert is_free_license("CC0")
    assert not is_free_license("CC BY-NC-SA 2.0", "cc-by-nc-sa-2.0")
    assert not is_free_license("CC BY-ND 4.0")
    assert not is_free_license("Fair use")
    assert not is_free_license(None)
    assert not is_free_license("Public domain", "pd", nonfree="true")


def test_clean_html():
    raw = '<bdi><a href="x"><span title="painter">Theodor&nbsp;Hosemann</span></a></bdi>'
    assert clean_html(raw) == "Theodor Hosemann"
    assert clean_html("") is None


def test_archival_falls_back_to_card_when_network_fails(tmp_path, monkeypatch):
    def boom(self, *a, **k):
        raise CommonsError("offline")

    client = CommonsClient(cache_dir=tmp_path / "cache")
    monkeypatch.setattr(CommonsClient, "_get", boom)
    monkeypatch.setattr(V, "_client", client)
    v = Visual(kind="archival", file="File:Nope.jpg", query="Barrikade Berlin 1848",
               caption="Barricades in Berlin, March 1848")
    a = resolve_visual(v, tmp_path, index=7)
    assert a.kind == "fallback" and a.attribution is None
    assert _size(a.path) == OUT_SIZE


def test_generated_without_key_falls_back(tmp_path, monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(V, "_client", None)
    a = resolve_visual(Visual(kind="generated", prompt="a barricade at night"), tmp_path, index=3)
    assert a.kind == "fallback"


def test_archival_uses_cache_offline(tmp_path, monkeypatch):
    """A cached file is served without touching the network."""
    client = CommonsClient(cache_dir=tmp_path / "cache")
    src = tmp_path / "src.jpg"
    Image.new("RGB", (2000, 1200), (90, 80, 70)).save(src)
    monkeypatch.setattr(CommonsClient, "_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    cand = {"title": "File:Test.jpg", "width": 2000, "height": 1200, "thumb_url": "x.jpg",
            "page_url": "https://commons.wikimedia.org/wiki/File:Test.jpg", "license": "Public domain",
            "artist": "A. Painter", "credit": None}
    meta_p, stem = client._paths("File:Test.jpg")
    shutil.copy(src, stem.with_suffix(".jpg"))
    import json
    meta_p.write_text(json.dumps({**{k: v for k, v in cand.items() if k not in ("thumb_url",)},
                                  "path": str(stem.with_suffix(".jpg"))}))
    monkeypatch.setattr(V, "_client", client)
    a = resolve_visual(Visual(kind="archival", file="Test.jpg"), tmp_path, index=2)
    assert a.kind == "archival"
    assert a.attribution.startswith("A. Painter") and "Public domain" in a.attribution


def test_attribution_lines_dedup():
    assets = [
        Asset(path=Path("a"), kind="archival", attribution="X, PD", source_url="u1"),
        Asset(path=Path("b"), kind="archival", attribution="X, PD", source_url="u1"),
        Asset(path=Path("c"), kind="title_card"),
        Asset(path=Path("d"), kind="map", attribution="Map data: Natural Earth"),
    ]
    assert attribution_lines(assets) == ["X, PD (u1)", "Map data: Natural Earth"]


def test_bad_map_spec_does_not_crash(tmp_path):
    a = resolve_visual(Visual(kind="map", caption="Europe"), tmp_path, index=4)
    assert a.kind == "fallback"


# ----------------------------------------------------------------- network


@pytest.mark.network
def test_real_commons_search():
    SAMPLES.mkdir(parents=True, exist_ok=True)
    V._client = None
    a = resolve_visual(Visual(kind="archival", query="Barrikade Berlin 1848"), SAMPLES, index=1)
    assert a.kind == "archival", a
    assert a.attribution and "Wikimedia Commons" in a.attribution
    assert a.source_url and a.source_url.startswith("https://commons.wikimedia.org/")
    assert _size(a.path) == OUT_SIZE


@pytest.mark.network
@pytest.mark.slow
def test_real_map_render():
    SAMPLES.mkdir(parents=True, exist_ok=True)
    spec = MapSpec(
        title="The German Confederation, 1848",
        extent=(5, 24, 45, 56),
        markers=[
            MapMarker(name="Berlin", lon=13.40, lat=52.52, label="Berlin, 18 March"),
            MapMarker(name="Vienna", lon=16.37, lat=48.21),
            MapMarker(name="Frankfurt", lon=8.68, lat=50.11, label="Frankfurt Parliament"),
            MapMarker(name="Dresden", lon=13.74, lat=51.05),
            MapMarker(name="Rastatt", lon=8.20, lat=48.86),
        ],
        arrows=[MapArrow(from_lonlat=(2.35, 48.86), to_lonlat=(8.68, 50.11), label="News from Paris")],
        regions=[MapRegion(label="Austria", countries=["Austria", "Czechia"])],
    )
    a = resolve_visual(Visual(kind="map", map=spec), SAMPLES, index=10)
    assert a.kind == "map"
    assert _size(a.path) == OUT_SIZE
