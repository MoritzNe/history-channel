"""Timeline -> MP4.

Pipeline:
  1. Plan: per-clip visible windows (clips extended by half a crossfade on each side),
     captions, re-wrapped subtitle cues, global fade from/to black.
  2. Video: the frame range is cut into chunks; a process pool renders each chunk
     frame-by-frame in Python (sub-pixel Ken Burns, crossfades, overlays) and pipes
     rgb24 into its own libx264 process, writing an MPEG-TS segment.
  3. Audio: numpy mix + ducking, two-pass loudnorm (see audio.py).
  4. Mux: concat demuxer (stream copy) + AAC 192k, ``+faststart``.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from historychannel.models import Motion, SubtitleCue, Timeline
from historychannel.render.audio import build_audio
from historychannel.render.motion import PreparedImage
from historychannel.render.overlays import (
    Overlay,
    caption_opacity,
    render_caption,
    render_subtitle,
    split_cues,
    subtitle_opacity,
    write_srt,
)

log = logging.getLogger(__name__)

CROSSFADE = 0.7  # s between consecutive clips
FADE_IN = 0.8  # s from black at the start
FADE_OUT = 1.2  # s to black at the end
PREVIEW_SIZE = (960, 540)


@dataclass
class ClipPlan:
    image: str
    motion: Motion
    start: float  # nominal timeline start (for captions)
    end: float
    vis_start: float  # extended window used for motion + crossfade
    vis_end: float
    fade_in: float  # crossfade length shared with the previous clip (0 = hard start)
    caption: str | None
    animation: dict | None = None  # animated shot: frames from animate.render_frame


@dataclass
class RenderPlan:
    width: int
    height: int
    fps: int
    duration: float
    n_frames: int
    clips: list[ClipPlan]
    cues: list[SubtitleCue] = field(default_factory=list)
    burn_subtitles: bool = True


def build_plan(timeline: Timeline, width: int, height: int) -> RenderPlan:
    clips = sorted(timeline.clips, key=lambda c: c.start)
    plans: list[ClipPlan] = []
    xf = [0.0] * (len(clips) + 1)  # xf[i] = crossfade between clip i-1 and clip i
    for i in range(1, len(clips)):
        a, b = clips[i - 1], clips[i]
        gap = b.start - (a.start + a.duration)
        if abs(gap) < 0.05:  # abutting clips crossfade; gaps/overlaps cut
            xf[i] = min(CROSSFADE, 0.4 * a.duration, 0.4 * b.duration)
    for i, c in enumerate(clips):
        end = c.start + c.duration
        plans.append(
            ClipPlan(
                image=str(c.image),
                motion=c.motion,
                start=c.start,
                end=end,
                vis_start=max(0.0, c.start - xf[i] / 2),
                vis_end=min(timeline.duration, end + xf[i + 1] / 2),
                fade_in=xf[i],
                caption=c.caption,
                animation=c.animation,
            )
        )
    return RenderPlan(
        width=width,
        height=height,
        fps=timeline.fps,
        duration=timeline.duration,
        n_frames=round(timeline.duration * timeline.fps),
        clips=plans,
        cues=split_cues(timeline.subtitles),
        burn_subtitles=timeline.burn_subtitles,
    )


# --------------------------------------------------------------------------- frame renderer


class FrameRenderer:
    """Renders frames of a plan; caches decoded images and text sprites."""

    def __init__(self, plan: RenderPlan):
        self.plan = plan
        self._images: dict[str, PreparedImage] = {}
        self._captions: dict[int, Overlay] = {}
        self._subs: dict[int, Overlay] = {}
        self._anim_failed: set[int] = set()  # clips whose animation raised: Ken Burns from then on

    def _image(self, path: str) -> PreparedImage:
        im = self._images.get(path)
        if im is None:
            if len(self._images) >= 4:
                self._images.pop(next(iter(self._images)))
            im = PreparedImage.load(Path(path), self.plan.width, self.plan.height)
            self._images[path] = im
        return im

    def _clip_frame(self, c: ClipPlan, t: float) -> np.ndarray:
        if c.animation is not None and id(c) not in self._anim_failed:
            from historychannel.animate import render_frame

            try:
                # shot-local time; slightly outside [0, duration] during crossfades
                f = render_frame(c.animation, t - c.start, c.end - c.start, self.plan.width, self.plan.height)
                if f.shape != (self.plan.height, self.plan.width, 3) or f.dtype != np.uint8:
                    raise ValueError(f"animated frame has shape {f.shape} {f.dtype}")
                return f
            except Exception:
                self._anim_failed.add(id(c))
                log.exception("animation %r failed at t=%.2f; falling back to Ken Burns on %s",
                              c.animation.get("kind"), t, c.image)
        u = (t - c.vis_start) / max(1e-6, c.vis_end - c.vis_start)
        return self._image(c.image).frame(c.motion, u, self.plan.width, self.plan.height)

    def frame(self, idx: int) -> np.ndarray:
        """uint8 HxWx3 frame. Stays in uint8 except where blending is needed."""
        p = self.plan
        t = idx / p.fps
        active = [i for i, c in enumerate(p.clips) if c.vis_start <= t < c.vis_end]
        if not active:
            out = np.zeros((p.height, p.width, 3), dtype=np.uint8)
        else:
            out = self._clip_frame(p.clips[active[0]], t)
            for i in active[1:]:
                c = p.clips[i]
                w = 1.0
                if c.fade_in > 0:  # cosine crossfade over the shared window
                    v = min(1.0, max(0.0, (t - c.vis_start) / c.fade_in))
                    w = 0.5 - 0.5 * math.cos(math.pi * v)
                nxt = self._clip_frame(c, t)
                if w >= 1.0:
                    out = nxt
                elif w > 0.0:
                    a = out.astype(np.float32)
                    a += (nxt.astype(np.float32) - a) * w
                    out = (a + 0.5).astype(np.uint8)
            if not out.flags.writeable:
                out = out.copy()

        fade = min(1.0, t / FADE_IN if FADE_IN else 1.0, (p.duration - t) / FADE_OUT if FADE_OUT else 1.0)
        fade = max(0.0, fade) ** 1.5

        for i in active:
            c = p.clips[i]
            if not c.caption:
                continue
            op = caption_opacity(t, c.start, c.end)
            if op > 0:
                ov = self._captions.get(i)
                if ov is None:
                    ov = self._captions[i] = render_caption(c.caption, p.width, p.height)
                ov.composite(out, op)

        if p.burn_subtitles and p.cues:
            for j, cue in self._cues_at(t):
                ov = self._subs.get(j)
                if ov is None:
                    ov = self._subs[j] = render_subtitle(cue.text, p.width, p.height)
                ov.composite(out, subtitle_opacity(t, cue.start, cue.end))
            for j in [k for k in self._subs if p.cues[k].end < t - 1]:
                del self._subs[j]

        if fade < 1.0:
            out = (out.astype(np.float32) * fade + 0.5).astype(np.uint8)
        return out

    def _cues_at(self, t: float):
        for j, cue in enumerate(self.plan.cues):
            if cue.start <= t < cue.end:
                yield j, cue


# --------------------------------------------------------------------------- encoding


def _x264_args(preview: bool, threads: int) -> list[str]:
    preset, crf = ("veryfast", "23") if preview else ("medium", "18")
    return [
        "-c:v", "libx264", "-preset", preset, "-crf", crf, "-profile:v", "high",
        "-pix_fmt", "yuv420p", "-g", "60", "-bf", "2", "-threads", str(threads),
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
    ]  # fmt: skip


def _render_chunk(plan: RenderPlan, f0: int, f1: int, out: str, preview: bool, threads: int) -> tuple[str, int]:
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{plan.width}x{plan.height}", "-r", str(plan.fps),
        "-i", "-",
        "-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv420p",
        *_x264_args(preview, threads), "-an", "-f", "mpegts", out,
    ]  # fmt: skip
    r = FrameRenderer(plan)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(f0, f1):
            proc.stdin.write(r.frame(i).tobytes())
    finally:
        proc.stdin.close()
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg failed for chunk {out} (exit {rc})")
    return out, f1 - f0


def _chunks(n_frames: int, fps: int, workers: int) -> list[tuple[int, int]]:
    # ~3 chunks per worker for load balancing, at least 2 s each
    size = max(2 * fps, math.ceil(n_frames / (workers * 3)))
    return [(s, min(s + size, n_frames)) for s in range(0, n_frames, size)]


def pool_shape() -> tuple[int, int]:
    """(parallel chunk workers, x264 threads per worker)."""
    cpu = os.cpu_count() or 4
    # Tuned on an 18-core M5 Pro: 12 workers x 3 x264 threads (2x oversubscribed) was
    # fastest; libx264 is the bottleneck, Python frame synthesis is ~25 ms/frame.
    workers = int(os.environ.get("HC_RENDER_WORKERS", 0)) or max(1, min(16, cpu * 2 // 3))
    threads = int(os.environ.get("HC_RENDER_THREADS", 0)) or max(1, 2 * cpu // workers)
    return workers, threads


def _aac_encoder() -> str:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return "aac_at" if " aac_at " in out else "aac"


def render_video(timeline: Timeline, out_path: Path, work_dir: Path, *, preview: bool = False) -> Path:
    """Render ``timeline`` to an H.264/AAC MP4 at ``out_path``.

    Always writes an ``.srt`` sidecar next to ``out_path``. ``preview=True`` renders
    960x540 with a fast preset. Intermediates live in ``work_dir`` and are removed.
    """
    t0 = time.monotonic()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(work_dir) / f"render_{out_path.stem}{'_preview' if preview else ''}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    width, height = PREVIEW_SIZE if preview else (timeline.width, timeline.height)
    plan = build_plan(timeline, width, height)
    write_srt(plan.cues, out_path.with_suffix(".srt"))

    workers, threads = pool_shape()
    chunks = _chunks(plan.n_frames, plan.fps, workers)
    log.info("render: %d frames, %d chunks, %d workers", plan.n_frames, len(chunks), workers)

    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futs = [
                pool.submit(_render_chunk, plan, f0, f1, str(tmp / f"seg_{k:04d}.ts"), preview, threads)
                for k, (f0, f1) in enumerate(chunks)
            ]
            audio_path = build_audio(timeline, tmp)  # overlaps with the video workers
            segs = [f.result()[0] for f in futs]
        t_video = time.monotonic() - t0

        listing = tmp / "segments.txt"
        listing.write_text("".join(f"file '{s}'\n" for s in segs))
        cmd = [
            "ffmpeg", "-v", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", _aac_encoder(), "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-t", f"{timeline.duration:.3f}",
            "-movflags", "+faststart", str(out_path),
        ]  # fmt: skip
        subprocess.run(cmd, check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    wall = time.monotonic() - t0
    log.info(
        "render: %.1fs of video in %.1fs (%.2fx realtime; video stage %.1fs) -> %s",
        timeline.duration, wall, timeline.duration / wall, t_video, out_path,
    )  # fmt: skip
    return out_path
