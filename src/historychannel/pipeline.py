"""End-to-end build: script.json -> narration -> assets -> timeline -> MP4.

Each stage caches its outputs under build/<slug>/ keyed by a content hash, so
editing one scene's narration only re-synthesizes that scene, and swapping one
visual only re-fetches that image.
"""

from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from historychannel import BUILD_DIR, REPO_ROOT
from historychannel.models import (
    Asset,
    AudioResult,
    AudioTrack,
    Clip,
    Scene,
    SubtitleCue,
    Timeline,
    VideoScript,
    Visual,
)

log = logging.getLogger(__name__)

LEAD_IN = 0.8  # seconds of picture before the first word
TAIL = 2.5  # seconds of picture after the last word
MIN_CLIP = 2.5  # never show a still for less than this


def _digest(*parts: object) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:12]


# --------------------------------------------------------------------------- narration


def narrate(script: VideoScript, work: Path, tts_name: str = "auto") -> list[tuple[AudioResult, list[tuple[str, float, float]]]]:
    """Synthesize one WAV per scene. Returns (audio, sentence timings) per scene."""
    from historychannel.tts import get_tts

    audio_dir = work / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    tts = None
    results = []
    for scene in script.scenes:
        key = _digest(scene.narration, script.voice, tts_name)
        wav = audio_dir / f"{scene.id}-{key}.wav"
        meta = wav.with_suffix(".json")
        if wav.exists() and meta.exists():
            cached = json.loads(meta.read_text())
            results.append((AudioResult.model_validate(cached["audio"]), [tuple(t) for t in cached["timings"]]))
            continue
        tts = tts or get_tts(tts_name)
        log.info("narrating %s (%d words)", scene.id, len(scene.narration.split()))
        audio, timings = tts.synthesize_with_timings(scene.narration, wav, voice=script.voice)
        meta.write_text(json.dumps({"audio": audio.model_dump(mode="json"), "timings": timings}))
        results.append((audio, timings))
    return results


# --------------------------------------------------------------------------- visuals


def gather_assets(script: VideoScript, work: Path) -> list[list[Asset]]:
    """Resolve every visual to an image file, in parallel (mostly network-bound)."""
    from historychannel.visuals import resolve_visual

    asset_dir = work / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)

    jobs: list[tuple[int, int, Visual]] = [
        (si, vi, v) for si, scene in enumerate(script.scenes) for vi, v in enumerate(scene.visuals)
    ]

    def run(job: tuple[int, int, Visual]) -> tuple[int, int, Asset]:
        si, vi, visual = job
        key = _digest(visual.model_dump(mode="json"))
        meta = asset_dir / f"{si:02d}-{vi:02d}-{key}.json"
        if meta.exists():
            asset = Asset.model_validate_json(meta.read_text())
            if asset.path.exists():
                return si, vi, asset
        asset = resolve_visual(visual, asset_dir, index=si * 100 + vi, script_title=script.title)
        meta.write_text(asset.model_dump_json())
        return si, vi, asset

    out: list[list[Asset | None]] = [[None] * len(s.visuals) for s in script.scenes]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for si, vi, asset in pool.map(run, jobs):
            out[si][vi] = asset
    return out  # type: ignore[return-value]


# --------------------------------------------------------------------------- timeline


def _split_scene(scene: Scene, length: float) -> list[float]:
    """Divide a scene's on-screen time across its visuals by weight, honoring MIN_CLIP."""
    weights = [max(v.weight, 0.05) for v in scene.visuals]
    n = len(weights)
    if length < MIN_CLIP * n:  # too short for every visual: keep as many as fit
        keep = max(1, int(length // MIN_CLIP))
        weights = weights[:keep] + [0.0] * (n - keep)
    total = sum(weights)
    return [length * w / total for w in weights]


def build_timeline(
    script: VideoScript,
    narration: list[tuple[AudioResult, list[tuple[str, float, float]]]],
    assets: list[list[Asset]],
    music: Path | None,
) -> tuple[Timeline, list[tuple[float, float, str]]]:
    clips: list[Clip] = []
    tracks: list[AudioTrack] = []
    subs: list[SubtitleCue] = []
    moods: list[tuple[float, float, str]] = []

    t = LEAD_IN
    for i, (scene, (audio, timings), scene_assets) in enumerate(zip(script.scenes, narration, assets)):
        last = i == len(script.scenes) - 1
        voice_start = t
        scene_len = audio.duration + scene.pause_after
        # The first scene's pictures start at 0 so the lead-in isn't black.
        pic_start = 0.0 if i == 0 else t
        pic_len = scene_len + (voice_start - pic_start) + (TAIL if last else 0.0)

        tracks.append(AudioTrack(path=audio.path, start=voice_start))
        for text, s, e in timings:
            subs.append(SubtitleCue(start=voice_start + s, end=voice_start + e, text=text))

        offset = pic_start
        for visual, asset, length in zip(scene.visuals, scene_assets, _split_scene(scene, pic_len)):
            if length <= 0:
                continue
            clips.append(Clip(image=asset.path, start=offset, duration=length, motion=visual.motion, caption=visual.caption))
            offset += length

        moods.append((pic_start, pic_start + pic_len, scene.mood))
        t = voice_start + scene_len

    duration = t + TAIL
    music_track = AudioTrack(path=music, gain_db=0.0) if music else None
    return Timeline(clips=clips, narration=tracks, music=music_track, subtitles=subs, duration=duration), moods


# --------------------------------------------------------------------------- entry point


def build(
    script_path: Path,
    *,
    tts_name: str = "auto",
    preview: bool = False,
    music_file: Path | None = None,
    out_dir: Path | None = None,
) -> Path:
    from historychannel.render import render_video
    from historychannel.render.music import generate_music_bed
    from historychannel.visuals import attribution_lines

    script = VideoScript.load(script_path)
    work = BUILD_DIR / script.slug
    work.mkdir(parents=True, exist_ok=True)
    out_dir = out_dir or REPO_ROOT / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("[1/4] narration")
    narration = narrate(script, work, tts_name)
    log.info("[2/4] visuals")
    assets = gather_assets(script, work)

    log.info("[3/4] timeline + music")
    timeline, moods = build_timeline(script, narration, assets, music_file)
    if music_file is None:
        bed = work / f"music-{_digest(moods)}.wav"
        if not bed.exists():
            generate_music_bed(moods, bed)
        timeline.music = AudioTrack(path=bed)
    (work / "timeline.json").write_text(timeline.model_dump_json(indent=2))

    credits = attribution_lines([a for scene in assets for a in scene])
    (out_dir / f"{script.slug}.credits.txt").write_text("\n".join(credits) + "\n")

    log.info("[4/4] render (%.0f s of video, %d clips)", timeline.duration, len(timeline.clips))
    suffix = "-preview" if preview else ""
    out = out_dir / f"{script.slug}{suffix}.mp4"
    return render_video(timeline, out, work / "render", preview=preview)
