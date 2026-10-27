"""Audio mix: narration placement, music bed with sidechain-style ducking, loudnorm.

Mixing and ducking are done in numpy (deterministic, no fragile filtergraph); only
decoding and the final two-pass EBU R128 ``loudnorm`` use ffmpeg.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from historychannel.models import Timeline

log = logging.getLogger(__name__)

SR = 48_000
TARGET_LUFS = -14.0
TARGET_TP = -1.0
TARGET_LRA = 11.0

MUSIC_UNDER_VOICE_DB = -20.0  # music level relative to speech while someone is talking
MUSIC_IN_GAPS_DB = -13.0  # ... and in pauses between narration
DUCK_ATTACK = 0.12  # s
DUCK_RELEASE = 0.7  # s
MUSIC_FADE_IN = 1.5
MUSIC_FADE_OUT = 3.0
LOOP_XFADE = 2.0


def decode(path: Path, sr: int = SR) -> np.ndarray:
    """Any audio file -> float32 (n, 2) at ``sr``."""
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(path),
        "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "2", "-ar", str(sr), "-",
    ]  # fmt: skip
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).copy()


def _db(x: float) -> float:
    return 10.0 ** (x / 20.0)


def _place(buf: np.ndarray, sig: np.ndarray, start: float, gain: float) -> None:
    i0 = round(start * SR)
    if i0 < 0:
        sig, i0 = sig[-i0:], 0
    n = min(len(sig), len(buf) - i0)
    if n > 0:
        buf[i0 : i0 + n] += sig[:n] * gain


def _loop_to(sig: np.ndarray, n: int) -> np.ndarray:
    """Loop ``sig`` with equal-power crossfades until it is at least ``n`` samples."""
    if len(sig) >= n:
        return sig[:n]
    xf = min(round(LOOP_XFADE * SR), len(sig) // 3)
    if xf < 16:
        reps = -(-n // len(sig))
        return np.tile(sig, (reps, 1))[:n]
    ramp = np.linspace(0, np.pi / 2, xf, dtype=np.float32)[:, None]
    fin, fout = np.sin(ramp), np.cos(ramp)
    out = sig.copy()
    while len(out) < n:
        tail = out[-xf:] * fout + sig[:xf] * fin
        out = np.concatenate([out[:-xf], tail, sig[xf:]])
    return out[:n]


def _speech_envelope(voice: np.ndarray) -> tuple[np.ndarray, float]:
    """Per-sample ducking amount in [0, 1] and the speech RMS (linear)."""
    mono = np.abs(voice).max(axis=1)
    hop = SR // 100  # 10 ms
    nwin = -(-len(mono) // hop)
    padded = np.zeros(nwin * hop, dtype=np.float32)
    padded[: len(mono)] = mono
    frames = padded.reshape(nwin, hop)
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1) + 1e-12)
    peak = rms.max() if len(rms) else 0.0
    if peak <= 1e-6:
        return np.zeros(len(voice), dtype=np.float32), 0.0
    active = rms > peak * _db(-35)
    speech_rms = float(np.sqrt((rms[active] ** 2).mean())) if active.any() else 0.0
    # hold for 250 ms so word gaps don't pump the music
    hold = 25
    held = active.copy()
    idx = np.flatnonzero(active)
    for k in range(1, hold + 1):
        held[np.clip(idx + k, 0, nwin - 1)] = True
    a_att = 1.0 - np.exp(-0.01 / DUCK_ATTACK)
    a_rel = 1.0 - np.exp(-0.01 / DUCK_RELEASE)
    env = np.empty(nwin, dtype=np.float32)
    level = 0.0
    for i, on in enumerate(held):
        target = 1.0 if on else 0.0
        level += (target - level) * (a_att if target > level else a_rel)
        env[i] = level
    per_sample = np.interp(np.arange(len(voice)) / hop, np.arange(nwin) + 0.5, env).astype(np.float32)
    return per_sample, speech_rms


def _rms(sig: np.ndarray) -> float:
    return float(np.sqrt((sig.astype(np.float64) ** 2).mean() + 1e-12))


def mix(timeline: Timeline) -> np.ndarray:
    n = round(timeline.duration * SR)
    voice = np.zeros((n, 2), dtype=np.float32)
    for tr in timeline.narration:
        _place(voice, decode(tr.path), tr.start, _db(tr.gain_db))
    out = voice.copy()

    if timeline.music is not None:
        m = timeline.music
        avail = n - max(0, round(m.start * SR))
        if avail > 0:
            music = _loop_to(decode(m.path), avail)
            fi, fo = min(round(MUSIC_FADE_IN * SR), avail // 4), min(round(MUSIC_FADE_OUT * SR), avail // 3)
            if fi:
                music[:fi] *= np.linspace(0, 1, fi, dtype=np.float32)[:, None]
            if fo:
                music[-fo:] *= np.linspace(1, 0, fo, dtype=np.float32)[:, None] ** 2
            duck, speech_rms = _speech_envelope(voice)
            music_rms = _rms(music[fi : len(music) - fo] if avail > fi + fo else music)
            if speech_rms > 0 and music_rms > 1e-6:
                base = speech_rms / music_rms
                g_gap, g_voice = base * _db(MUSIC_IN_GAPS_DB), base * _db(MUSIC_UNDER_VOICE_DB)
                i0 = n - avail
                gain = g_gap + (g_voice - g_gap) * duck[i0:]
                music *= gain[:, None]
            music *= _db(m.gain_db)
            _place(out, music, max(0.0, m.start), 1.0)
    return out


_JSON_RE = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


def loudnorm(src: Path, dst: Path) -> dict:
    """Two-pass loudnorm to -14 LUFS / -1 dBTP; returns the pass-1 measurement."""
    base = f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA={TARGET_LRA}"
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-af", base + ":print_format=json", "-f", "null", "-"],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    mt = _JSON_RE.search(p.stderr)
    if not mt:
        raise RuntimeError("loudnorm measurement failed:\n" + p.stderr[-2000:])
    meas = json.loads(mt.group(0))
    if float(meas["input_i"]) < -70:  # silence: nothing to normalise
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c:a", "pcm_s24le", str(dst)], check=True)
        return meas
    second = (
        f"{base}:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}"
        f":measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}"
        f":offset={meas['target_offset']}:linear=true"
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", second, "-ar", str(SR), "-c:a", "pcm_s24le", str(dst)],
        check=True,
    )  # fmt: skip
    return meas


def build_audio(timeline: Timeline, work_dir: Path) -> Path:
    raw = work_dir / "mix_raw.wav"
    final = work_dir / "mix_norm.wav"
    sig = mix(timeline)
    sf.write(raw, sig, SR, subtype="FLOAT")
    meas = loudnorm(raw, final)
    log.info("audio: input %.1f LUFS, %.1f dBTP -> normalised", float(meas["input_i"]), float(meas["input_tp"]))
    raw.unlink(missing_ok=True)
    return final


def measure_lufs(path: Path) -> float:
    """Integrated loudness via ffmpeg ebur128 (used by tests and sanity checks)."""
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-af", "ebur128", "-f", "null", "-"],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    vals = re.findall(r"I:\s+(-?[\d.]+) LUFS", p.stderr)
    return float(vals[-1])
