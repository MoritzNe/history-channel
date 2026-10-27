from __future__ import annotations

import time

import numpy as np
import pytest
from PIL import Image

from historychannel import animate
from historychannel.animate import cards
from historychannel.animate.cards import format_value, parse_value
from historychannel.models import CompareColumn, TimelineEvent, Visual

LONG = ("An extraordinarily long line of descriptive text that keeps going well past what any "
        "sensible script would ever put on a card, just to make sure the layout wraps and shrinks")


def _events(n: int, long: bool = False) -> list[TimelineEvent]:
    labels = [
        "Revolution in Paris", "Uprisings in Vienna and Berlin", "Frankfurt Parliament opens",
        "Robert Blum executed in Vienna", "Constitution adopted", "Prussian king refuses the crown",
        "Rastatt falls", "Exile and emigration to America",
    ]
    return [
        TimelineEvent(date=f"Mon {1848 + i // 4}", label=(LONG if long and i % 3 == 0 else labels[i % 8]),
                      highlight=(i == n - 2))
        for i in range(n)
    ]


VISUALS = {
    "stat": Visual(kind="stat", value="80,000", text="Germans emigrated", subtext="In the years after 1849"),
    "timeline": Visual(kind="timeline", text="1848–49", events=_events(6)),
    "compare": Visual(kind="compare", columns=[
        CompareColumn(title="Greater Germany", points=["Includes Austria", "Habsburg-led"]),
        CompareColumn(title="Lesser Germany", points=["Excludes Austria", "Prussian king"], color="#6f8f5f"),
    ]),
    "person": Visual(kind="person", file="File:Robert Blum.jpg", text="Robert Blum", subtext="1807–1848"),
    "title_card": Visual(kind="title_card", text="The Revolution That Almost Made Germany", subtext="1848–49"),
    "quote": Visual(kind="quote", text="I die for German freedom, for which I have fought.",
                    subtext="Robert Blum"),
}


@pytest.fixture
def no_portrait(monkeypatch):
    monkeypatch.setattr(cards, "_fetch_portrait", lambda v: None)


def _in_frame(anim: dict, margin: float = 8.0) -> None:
    for x0, y0, x1, y1 in anim["boxes"]:
        assert x0 >= margin and x1 <= 1920 - margin, (x0, x1)
        assert y0 >= margin and y1 <= 1080 - margin, (y0, y1)


# ------------------------------------------------------------------ numbers


@pytest.mark.parametrize("raw, number, prefix, suffix, count", [
    ("809", 809, "", "", True),
    ("80,000", 80000, "", "", True),
    ("1,500", 1500, "", "", True),
    ("39", 39, "", "", True),
    ("c. 1 million", 1, "c. ", " million", False),
    ("1 in 3", 1, "", " in 3", False),
    ("1848", 1848, "", "", False),  # a year: never count up
    ("40%", 40, "", "%", True),
    ("2.5 million", 2.5, "", " million", True),
])
def test_parse_value(raw, number, prefix, suffix, count):
    spec = parse_value(raw)
    assert spec["number"] == number
    assert (spec["prefix"], spec["suffix"], spec["count"]) == (prefix, suffix, count)
    assert format_value(spec) == raw


def test_parse_value_non_numeric():
    spec = parse_value("Thousands")
    assert spec["number"] is None and not spec["count"]
    assert format_value(spec) == "Thousands"


def test_format_value_intermediate():
    assert format_value(parse_value("80,000"), 12345.7) == "12,345"
    assert format_value(parse_value("c. 1,500 dead"), 0) == "c. 0 dead"
    assert format_value(parse_value("2.5 million"), 1.25) in {"1.2 million", "1.3 million"}


# ------------------------------------------------------------------ frames


@pytest.mark.parametrize("kind", list(VISUALS))
def test_prepare_and_frames(kind, tmp_path, no_portrait):
    asset = animate.prepare(VISUALS[kind], tmp_path, index=3)
    assert asset.kind == kind and asset.animation["kind"] == kind
    with Image.open(asset.path) as im:
        assert im.size == (3840, 2160)
    anim = asset.animation
    import json
    json.dumps(anim)  # must survive the trip to render workers
    d = 6.0
    for w, h in ((1920, 1080), (960, 540)):
        a0 = animate.render_frame(anim, 0.0, d, w, h)
        a1 = animate.render_frame(anim, d, d, w, h)
        assert a0.shape == (h, w, 3) and a0.dtype == np.uint8 and a0.flags.c_contiguous
        assert np.abs(a0.astype(int) - a1.astype(int)).mean() > 1.0
    # clamped outside the shot
    assert animate.render_frame(anim, -1.0, d, 960, 540).shape == (540, 960, 3)
    assert animate.render_frame(anim, d + 1, d, 960, 540).shape == (540, 960, 3)
    _in_frame(anim)


@pytest.mark.parametrize("kind", list(VISUALS))
def test_nothing_moves_during_crossfade(kind, tmp_path, no_portrait):
    anim = animate.prepare(VISUALS[kind], tmp_path, index=1).animation
    a = animate.render_frame(anim, 0.0, 8.0, 960, 540).astype(int)
    b = animate.render_frame(anim, cards.T0 * 0.9, 8.0, 960, 540).astype(int)
    assert np.abs(a - b).mean() < 1.5  # only the background drift


def test_preview_matches_full_size(tmp_path, no_portrait):
    anim = animate.prepare(VISUALS["title_card"], tmp_path, index=0).animation
    big = animate.render_frame(anim, 5.0, 6.0, 1920, 1080)
    small = animate.render_frame(anim, 5.0, 6.0, 960, 540)
    down = np.asarray(Image.fromarray(big).resize((960, 540), Image.Resampling.BOX)).astype(int)
    assert np.abs(down - small.astype(int)).mean() < 4.0


def test_long_text_stays_in_frame(tmp_path, no_portrait):
    vs = [
        Visual(kind="title_card", text=LONG, subtext=LONG),
        Visual(kind="quote", text=LONG + " " + LONG, subtext=LONG),
        Visual(kind="stat", value="c. 1,250,000,000 people", text=LONG, subtext=LONG),
        Visual(kind="person", text="Friedrich Wilhelm Ludwig Georg von Hohenzollern-Sigmaringen", subtext=LONG),
        Visual(kind="compare", text=LONG, columns=[
            CompareColumn(title=LONG[:60], points=[LONG[:90]] * 4),
            CompareColumn(title="Short", points=["One", "Two"]),
        ]),
        Visual(kind="timeline", text=LONG, events=_events(8, long=True)),
    ]
    for i, v in enumerate(vs):
        anim = cards.prepare(v, tmp_path, index=i).animation
        _in_frame(anim)


@pytest.mark.parametrize("n", [3, 5, 8])
def test_timeline_event_counts(n, tmp_path):
    v = Visual(kind="timeline", events=_events(n))
    anim = cards.prepare(v, tmp_path, index=n).animation
    assert len(anim["events"]) == n and anim["hl"] == n - 2
    _in_frame(anim)  # overview layout fits before the camera moves
    xs = [e["x"] for e in anim["events"]]
    assert xs == sorted(xs)
    # highlight ends nearer the centre than it started
    hx = xs[n - 2]
    z, tx = anim["cam"]["z"], anim["cam"]["tx"]
    assert abs(960 + (hx - 960) * z + tx - 960) <= abs(hx - 960) + 1e-6
    for t in (0.0, 2.0, 4.0, 8.0):
        assert cards.frame(anim, t, 8.0, 960, 540).shape == (540, 960, 3)


def test_timeline_without_highlight(tmp_path):
    v = Visual(kind="timeline", events=[TimelineEvent(date="1848", label="A"), TimelineEvent(date="1849", label="B")])
    anim = cards.prepare(v, tmp_path, index=0).animation
    assert anim["hl"] is None and anim["cam"]["z"] == 1.0
    cards.frame(anim, 3.0, 6.0, 960, 540)


def test_person_fallback_without_portrait(tmp_path, no_portrait):
    asset = cards.prepare(VISUALS["person"], tmp_path, index=0)
    assert asset.attribution is None and "portrait" not in asset.animation
    assert asset.animation["monogram"] == "RB" and asset.animation["align"] == "center"


def test_person_with_local_portrait(tmp_path, monkeypatch):
    class FakeImage:
        attribution, page_url = "Someone, public domain", "https://commons.example/x"

    img = Image.fromarray((np.random.default_rng(0).random((900, 700, 3)) * 255).astype(np.uint8))
    monkeypatch.setattr(cards, "_fetch_portrait", lambda v: (img, FakeImage()))
    asset = cards.prepare(VISUALS["person"], tmp_path, index=0)
    assert asset.attribution == "Someone, public domain" and asset.source_url
    assert asset.animation["align"] == "left"
    assert cards.frame(asset.animation, 4.0, 6.0, 960, 540).shape == (540, 960, 3)


def test_compare_needs_two_columns(tmp_path):
    with pytest.raises(ValueError):
        cards.prepare(Visual(kind="compare", columns=[CompareColumn(title="A", points=["x"])]), tmp_path, index=0)


def test_frame_time_budget(tmp_path, no_portrait):
    for kind, v in VISUALS.items():
        anim = cards.prepare(v, tmp_path, index=0).animation
        cards.frame(anim, 1.0, 8.0, 1920, 1080)  # warm caches
        t0 = time.perf_counter()
        n = 15
        for i in range(n):
            cards.frame(anim, 8.0 * i / n, 8.0, 1920, 1080)
        ms = (time.perf_counter() - t0) / n * 1000
        assert ms < 80, f"{kind}: {ms:.1f} ms/frame"


# ------------------------------------------------------------------ network


@pytest.mark.network
def test_person_real_portrait(tmp_path):
    v = Visual(kind="person", file="File:Robert Blum.jpg", query="Robert Blum portrait",
               text="Robert Blum", subtext="1807–1848 · Democrat, executed in Vienna")
    asset = cards.prepare(v, tmp_path, index=0)
    assert asset.animation.get("portrait"), "portrait should have been fetched"
    assert asset.attribution and "Commons" in asset.attribution
    assert asset.source_url and asset.source_url.startswith("https://")
    fr = cards.frame(asset.animation, 6.0, 6.0, 1920, 1080)
    # the cameo area is not flat background
    assert fr[300:780, 350:770].std() > 15
