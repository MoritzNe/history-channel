from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont

from historychannel import BUILD_DIR
from historychannel.models import AudioTrack, Clip, SubtitleCue, Timeline
from historychannel.render import generate_music_bed, render_video
from historychannel.render.audio import measure_lufs
from historychannel.render.motion import PreparedImage, view_box
from historychannel.render.overlays import MAX_CHARS, split_cues, write_srt
from historychannel.render.video import build_plan

SAMPLES = BUILD_DIR / "samples" / "render"
SR = 48_000


# --------------------------------------------------------------------------- fixtures


def make_image(path: Path, idx: int) -> Path:
    w, h = 3840, 2160
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    hues = [(70, 40, 30), (30, 50, 80), (40, 70, 45), (80, 60, 30)]
    base = np.array(hues[idx % 4], dtype=np.float32)
    grad = (xx / w)[..., None] * 120 + (yy / h)[..., None] * 60
    arr = np.clip(base + grad, 0, 255).astype(np.uint8)
    im = Image.fromarray(arr)
    d = ImageDraw.Draw(im)
    for x in range(0, w, 160):
        d.line([(x, 0), (x, h)], fill=(230, 230, 230), width=3)
    for y in range(0, h, 160):
        d.line([(0, y), (w, y)], fill=(230, 230, 230), width=3)
    d.ellipse((w / 2 - 300, h / 2 - 300, w / 2 + 300, h / 2 + 300), outline=(255, 220, 120), width=12)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Georgia.ttf", 260)
    except OSError:
        font = ImageFont.load_default(size=260)
    d.text((w / 2, h / 2), f"PLATE {idx + 1}", font=font, fill=(255, 255, 255), anchor="mm")
    im.save(path)
    return path


def make_speechlike(path: Path, seconds: float, seed: int) -> Path:
    """Harmonic 'voice' with syllable-rate modulation and pauses (loudness-realistic)."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * SR)) / SR
    f0 = 130 + 15 * np.sin(2 * np.pi * 0.7 * t)
    phase = 2 * np.pi * np.cumsum(f0) / SR
    voice = sum(np.sin(k * phase) / k for k in range(1, 12))
    voice += 0.15 * rng.standard_normal(len(t))
    syll = 0.5 - 0.5 * np.cos(2 * np.pi * 4.0 * t)
    words = ((t % 2.6) < 2.0).astype(float)  # 2 s phrases, 0.6 s pauses
    sig = 0.25 * voice * syll * words
    sf.write(path, np.stack([sig, sig], 1).astype(np.float32), SR)
    return path


def make_say(path: Path, text: str) -> Path | None:
    if not shutil.which("say"):
        return None
    aiff = path.with_suffix(".aiff")
    try:
        subprocess.run(["say", "-o", str(aiff), text], check=True, capture_output=True, timeout=60)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(aiff), "-ar", str(SR), str(path)], check=True)
    except (subprocess.SubprocessError, OSError):
        return None
    return path


@pytest.fixture(scope="module")
def timeline(tmp_path_factory) -> Timeline:
    d = tmp_path_factory.mktemp("render_assets")
    imgs = [make_image(d / f"plate{i}.png", i) for i in range(4)]
    n1 = make_speechlike(d / "narr1.wav", 7.0, 1)
    n2 = make_say(d / "narr2.wav", "In March eighteen forty eight, barricades rose across Berlin.")
    n2 = n2 or make_speechlike(d / "narr2.wav", 6.0, 2)
    music = generate_music_bed([(0, 8, "tense"), (8, 15, "hopeful")], d / "bed.wav")
    motions = ["zoom_in", "pan_right", "zoom_out", "pan_up"]
    captions = ["Berlin, 18 March 1848", None, "Frankfurt Parliament, St. Paul's Church", None]
    clips = [
        Clip(image=imgs[i], start=i * 3.75, duration=3.75, motion=motions[i], caption=captions[i]) for i in range(4)
    ]
    subs = [
        SubtitleCue(start=0.3, end=3.4, text="In March 1848, barricades rose across Berlin."),
        SubtitleCue(
            start=3.6,
            end=7.2,
            text="Soldiers and citizens fought through the night, and by morning the king had "
            "ordered his troops out of the city, a concession nobody had expected.",
        ),
        SubtitleCue(start=8.0, end=11.0, text="Weeks later, delegates met in Frankfurt."),
        SubtitleCue(start=11.2, end=14.4, text="Their task: a constitution for all Germany."),
    ]
    return Timeline(
        clips=clips,
        narration=[AudioTrack(path=n1, start=0.3), AudioTrack(path=n2, start=8.0)],
        music=AudioTrack(path=music),
        subtitles=subs,
        duration=15.0,
    )


def ffprobe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    return json.loads(out)


def grab(video: Path, t: float, out: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{t}", "-i", str(video), "-frames:v", "1", str(out)], check=True
    )
    return out


# --------------------------------------------------------------------------- fast unit tests


def test_view_box_stays_inside_source():
    for motion in ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "static"]:
        for u in np.linspace(0, 1, 21):
            x0, y0, x1, y1 = view_box(motion, float(u), 3840, 2160)
            assert x0 >= -1e-6 and y0 >= -1e-6 and x1 <= 3840 + 1e-6 and y1 <= 2160 + 1e-6
            assert abs((x1 - x0) / (y1 - y0) - 16 / 9) < 1e-6


def test_ken_burns_is_subpixel_smooth(tmp_path):
    """A blob's centroid must glide along a smooth curve (no stair-stepping).

    Jitter = residual of the per-frame centroid against a smooth polynomial fit. Snapping
    the crop to whole source pixels (what ``zoompan`` effectively does) is used as a
    control to prove the metric detects jitter.
    """
    w, h = 3840, 2160
    yy, xx = np.mgrid[0:h, 0:w]
    blob = np.exp(-(((xx - 1500) ** 2 + (yy - 900) ** 2) / (2 * 120.0**2)))
    Image.fromarray((blob * 255).astype(np.uint8)).convert("RGB").save(tmp_path / "blob.png")
    im = PreparedImage.load(tmp_path / "blob.png", 1920, 1080)
    sw, sh = im.image.size

    def centroid(f: np.ndarray) -> tuple[float, float]:
        f = f[..., 0].astype(np.float64)
        tot = f.sum()
        return (f.sum(axis=0) * np.arange(f.shape[1])).sum() / tot, (f.sum(axis=1) * np.arange(f.shape[0])).sum() / tot

    def jitter(track: list[tuple[float, float]]) -> float:
        tr = np.asarray(track)
        x = np.linspace(-1, 1, len(tr))
        res = [tr[:, k] - np.polynomial.chebyshev.Chebyshev.fit(x, tr[:, k], 8)(x) for k in range(2)]
        return float(np.abs(res).max())

    n = 45
    for motion in ("zoom_in", "zoom_out", "pan_right", "pan_up"):
        ours, snapped = [], []
        for i in range(n):
            u = i / (n - 1)
            ours.append(centroid(im.frame(motion, u, 1920, 1080)))
            x0, y0, x1, y1 = view_box(motion, u, sw, sh)
            box = (round(x0), round(y0), round(x0) + round(x1 - x0), round(y0) + round(y1 - y0))
            snapped.append(centroid(np.asarray(im.image.resize((1920, 1080), Image.BICUBIC, box=box))))
        assert jitter(ours) < 0.03, (motion, jitter(ours))
        assert jitter(snapped) > 3 * jitter(ours), (motion, jitter(snapped), jitter(ours))


def test_subtitles_rewrap_and_srt(tmp_path):
    long = SubtitleCue(start=0, end=6, text="word " * 40)
    cues = split_cues([long, SubtitleCue(start=6, end=7, text="Short one.")])
    assert len(cues) >= 3
    for c in cues:
        lines = c.text.split("\n")
        assert len(lines) <= 2 and all(len(ln) <= MAX_CHARS for ln in lines)
    assert abs(cues[-2].end - 6) < 1e-6
    srt = write_srt(cues, tmp_path / "a.srt").read_text()
    assert srt.startswith("1\n00:00:00,000 --> ")


def test_plan_crossfades_abutting_clips(timeline):
    plan = build_plan(timeline, 1920, 1080)
    assert plan.n_frames == 450
    c0, c1 = plan.clips[0], plan.clips[1]
    assert c0.vis_start == 0 and c1.fade_in == pytest.approx(0.7)
    assert c0.vis_end == pytest.approx(c1.vis_start + c1.fade_in)
    assert plan.clips[-1].vis_end == pytest.approx(15.0)


def test_music_bed(tmp_path):
    p = generate_music_bed([(0, 5, "somber"), (5, 10, "triumphant")], tmp_path / "m.wav")
    x, sr = sf.read(p)
    assert sr == 48000 and x.shape == (480000, 2)
    assert np.abs(x).max() <= 10 ** (-3 / 20) + 1e-3
    assert np.abs(np.diff(x, axis=0)).max() < 0.1  # no clicks


# --------------------------------------------------------------------------- slow render tests


def _check_output(path: Path, w: int, h: int, duration: float):
    info = ffprobe(path)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = [s for s in info["streams"] if s["codec_type"] == "audio"]
    assert (v["width"], v["height"]) == (w, h)
    assert v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p"
    num, den = map(int, v["avg_frame_rate"].split("/"))
    assert abs(num / den - 30) < 0.01
    assert abs(float(info["format"]["duration"]) - duration) <= 0.3
    assert a and a[0]["codec_name"] == "aac" and a[0]["sample_rate"] == "48000"
    assert abs(measure_lufs(path) - (-14.0)) <= 2.0
    assert path.with_suffix(".srt").exists()


@pytest.mark.slow
def test_render_preview(timeline, tmp_path):
    out = render_video(timeline, tmp_path / "preview.mp4", tmp_path / "work", preview=True)
    _check_output(out, 960, 540, timeline.duration)
    assert not (tmp_path / "work" / "render_preview_preview").exists()


@pytest.mark.slow
def test_render_full(timeline, tmp_path):
    SAMPLES.mkdir(parents=True, exist_ok=True)
    out = render_video(timeline, SAMPLES / "test_render.mp4", tmp_path / "work")
    _check_output(out, 1920, 1080, timeline.duration)
    # sample frames for visual inspection: caption, crossfade, long subtitle, final fade
    for t in (0.2, 1.8, 3.75, 5.0, 9.5, 14.6):
        grab(out, t, SAMPLES / f"frame_{t:05.2f}.png")
