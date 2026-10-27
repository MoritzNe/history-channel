"""Animated map demo: German Confederation, March 1848.

    .venv/bin/python scripts/map_demo.py            # frames + timing only (fast)
    .venv/bin/python scripts/map_demo.py --video    # + render through the real renderer
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time

import numpy as np
import soundfile as sf
from PIL import Image

from historychannel import BUILD_DIR
from historychannel.animate import prepare, render_frame
from historychannel.models import (
    AudioTrack,
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

OUT = BUILD_DIR / "samples" / "animate"
DURATION = 12.0
PARIS = (2.35, 48.86)
VIENNA = (16.37, 48.21)
BERLIN = (13.40, 52.52)
MUNICH = (11.58, 48.14)
DRESDEN = (13.74, 51.05)

SPEC = MapSpec(
    title="1848: Revolution Spreads East",
    extent=(-1.0, 25.0, 43.5, 56.5),
    camera_to=(8.5, 19.5, 49.0, 54.8),
    basemap="1815",
    regions=[
        MapRegion(label="Prussia", historical=["Prussia"], color="#5b7fa6", at=0.04),
        MapRegion(label="Austrian Empire", historical=["Austrian Empire"], color="#e0b860", at=0.10),
    ],
    markers=[
        MapMarker(name="Paris", lon=PARIS[0], lat=PARIS[1], style="uprising", at=0.05),
        MapMarker(name="Vienna", lon=VIENNA[0], lat=VIENNA[1], style="capital", at=0.30),
        MapMarker(name="Munich", lon=MUNICH[0], lat=MUNICH[1], style="city", at=0.36),
        MapMarker(name="Dresden", lon=DRESDEN[0], lat=DRESDEN[1], style="city", at=0.42),
        MapMarker(name="Berlin", lon=BERLIN[0], lat=BERLIN[1], style="capital", at=0.50, pulse=True),
    ],
    arrows=[
        MapArrow(from_lonlat=PARIS, to_lonlat=VIENNA, at=0.14, draw=0.18, label="Revolution spreads"),
        MapArrow(from_lonlat=PARIS, to_lonlat=MUNICH, at=0.22, draw=0.16, style="flow"),
        MapArrow(from_lonlat=PARIS, to_lonlat=DRESDEN, at=0.30, draw=0.16, style="flow"),
        MapArrow(from_lonlat=PARIS, to_lonlat=BERLIN, at=0.36, draw=0.18),
    ],
    texts=[
        MapText(text="North Sea", lon=4.2, lat=55.2, size="small"),
        MapText(text="Baltic Sea", lon=17.5, lat=55.9, size="small"),
    ],
    stamps=[
        MapStamp(at=0.0, text="24 Feb 1848"),
        MapStamp(at=0.30, text="13 Mar 1848"),
        MapStamp(at=0.50, text="18 Mar 1848"),
    ],
)


def main(video: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    asset = prepare(Visual(kind="map", map=SPEC), OUT, index=900)
    print(f"prepare: {time.monotonic() - t0:.1f}s  poster={asset.path}  attribution={asset.attribution!r}")
    anim = asset.animation
    times = []
    for t in (0.3, 1.5, 2.5, 4.0, 6.0, 8.0, 11.9):
        t1 = time.perf_counter()
        f = render_frame(anim, t, DURATION, 1920, 1080)
        times.append(time.perf_counter() - t1)
        Image.fromarray(f).save(OUT / f"map_demo_t{t:04.1f}.png")
    Image.fromarray(render_frame(anim, 8.0, DURATION, 960, 540)).save(OUT / "map_demo_preview_t08.0.png")
    print("ms/frame (first incl. load):", [round(x * 1000, 1) for x in times])
    bench = []
    for i in range(90):
        t1 = time.perf_counter()
        render_frame(anim, i * DURATION / 90, DURATION, 1920, 1080)
        bench.append(time.perf_counter() - t1)
    print(f"steady ms/frame 1920x1080: mean {np.mean(bench) * 1000:.1f}  p95 {np.percentile(bench, 95) * 1000:.1f}")
    if not video:
        return
    sr = 48000
    silence = OUT / "silence.wav"
    sf.write(silence, np.zeros(int(sr * DURATION), dtype=np.float32), sr)
    tl = Timeline(
        clips=[Clip(image=asset.path, start=0.0, duration=DURATION, motion="static", caption="March 1848",
                    animation=anim)],
        narration=[AudioTrack(path=silence)],
        duration=DURATION,
        burn_subtitles=False,
    )
    from historychannel.render import render_video

    out = render_video(tl, OUT / "map_demo.mp4", OUT / "work")
    for t in (1.0, 3.0, 5.0, 7.5, 11.0):
        png = OUT / f"map_demo_video_t{t:04.1f}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", str(out), "-frames:v", "1", str(png)],
                       check=True)
    print("video:", out)


if __name__ == "__main__":
    main("--video" in sys.argv)
