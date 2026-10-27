"""Procedural ambient documentary bed (numpy only).

Sound design, kept deliberately unobtrusive:
  * pads: every chord tone is three detuned (+-7 cent) band-limited (polyBLEP) saws,
    spread in stereo, with a 2.5 s raised-cosine attack and 3 s release so chords
    melt into each other;
  * filter: two FIR low-passed copies (dark / brighter) crossfaded by a very slow
    LFO, which reads as a gently breathing filter sweep;
  * space: convolution with a 3.8 s exponentially decaying, low-passed stereo noise
    tail (smoother than a feedback delay, no metallic ringing);
  * moods: all progressions stay inside D minor / F major, so the 4 s equal-power
    crossfades between mood segments never clash. "tense" adds a soft low heartbeat
    pulse, "hopeful"/"triumphant" use major voicings, an upper octave and a brighter
    filter.

The renderer accepts any audio file as ``Timeline.music``; this is only the fallback.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import soundfile as sf

CHORD_LEN = {"tense": 6.0, "somber": 9.0, "hopeful": 8.0, "triumphant": 7.0, "neutral": 9.0}
ATTACK, RELEASE = 2.5, 3.0
MOOD_XFADE = 4.0

# MIDI note numbers (D2 = 38, F2 = 41, A2 = 45, D3 = 50)
PROGRESSIONS: dict[str, list[list[int]]] = {
    "somber": [[38, 50, 53, 57], [34, 50, 53, 58], [43, 50, 55, 58], [33, 50, 52, 57]],
    "tense": [[38, 45, 50, 53], [34, 46, 50, 53], [38, 46, 50, 55], [33, 45, 50, 52]],
    "neutral": [[41, 48, 52, 57], [38, 45, 52, 53], [34, 46, 50, 57], [36, 48, 52, 55]],
    "hopeful": [[41, 48, 53, 57, 60], [36, 48, 52, 55, 60], [38, 50, 53, 57, 62], [34, 46, 53, 58, 62]],
    "triumphant": [[34, 46, 53, 58, 62, 65], [41, 53, 57, 60, 65], [36, 48, 55, 60, 64], [41, 48, 57, 60, 65, 69]],
}
# (dark cutoff Hz, bright cutoff Hz, upper-octave level)
TONE = {
    "somber": (500.0, 1100.0, 0.0),
    "tense": (450.0, 950.0, 0.0),
    "neutral": (600.0, 1300.0, 0.08),
    "hopeful": (800.0, 1900.0, 0.18),
    "triumphant": (900.0, 2300.0, 0.28),
}


def _midi_hz(m: float) -> float:
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


def _saw(freq: float, n: int, sr: int, phase0: float) -> np.ndarray:
    dt = freq / sr
    ph = (phase0 + dt * np.arange(n)) % 1.0
    y = 2.0 * ph - 1.0
    lo = ph < dt
    t = ph[lo] / dt
    y[lo] -= t + t - t * t - 1.0
    hi = ph > 1.0 - dt
    t = (ph[hi] - 1.0) / dt
    y[hi] -= t * t + t + t + 1.0
    return y


def _lowpass_fir(cutoff: float, sr: int, taps: int = 511) -> np.ndarray:
    k = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(2 * cutoff / sr * k) * np.blackman(taps)
    return h / h.sum()


def _convolve(x: np.ndarray, h: np.ndarray, block: int = 1 << 17) -> np.ndarray:
    """Overlap-add FFT convolution of 1-D ``x`` with ``h``; output trimmed to len(x)."""
    n = len(x)
    m = len(h)
    nfft = 1 << math.ceil(math.log2(block + m - 1))
    H = np.fft.rfft(h, nfft)
    out = np.zeros(n + m - 1)
    for s in range(0, n, block):
        seg = x[s : s + block]
        y = np.fft.irfft(np.fft.rfft(seg, nfft) * H, nfft)[: len(seg) + m - 1]
        out[s : s + len(y)] += y
    return out[:n]


def _envelope(n: int, sr: int, attack: float, release: float) -> np.ndarray:
    env = np.ones(n)
    a = min(int(attack * sr), n // 2)
    r = min(int(release * sr), n // 2)
    if a:
        env[:a] = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, a))
    if r:
        env[-r:] = 0.5 + 0.5 * np.cos(np.linspace(0, np.pi, r))
    return env


def _render_mood(mood: str, t0: float, t1: float, sr: int, rng: np.random.Generator) -> np.ndarray:
    """Stereo pad for global time [t0, t1). Chords sit on a global grid of CHORD_LEN."""
    n = int(round((t1 - t0) * sr))
    out = np.zeros((n, 2))
    L = CHORD_LEN[mood]
    prog = PROGRESSIONS[mood]
    dark, bright, octave = TONE[mood]
    k0 = max(0, math.floor((t0 - RELEASE) / L) - 1)
    k = k0
    while k * L < t1:
        cs, ce = k * L, k * L + L + RELEASE
        if ce > t0:
            i0 = int(round((cs - t0) * sr))
            m = int(round((ce - cs) * sr))
            env = _envelope(m, sr, ATTACK, RELEASE)
            chord = np.zeros((m, 2))
            notes = [(nt, 1.0) for nt in prog[k % len(prog)]]
            if octave:
                notes += [(nt + 12, octave) for nt in prog[k % len(prog)][1:]]
            for note, lvl in notes:
                f = _midi_hz(note)
                # lower notes louder, higher notes softer: keeps the voicing warm
                amp = lvl * (0.9 if note < 45 else 0.55 if note < 60 else 0.35)
                for j, cents in enumerate((-7.0, 0.0, 7.0)):
                    v = _saw(f * 2 ** (cents / 1200), m, sr, rng.random())
                    pan = 0.5 + 0.35 * (j - 1) * (1 if note % 2 else -1)
                    chord[:, 0] += v * amp * math.cos(pan * math.pi / 2)
                    chord[:, 1] += v * amp * math.sin(pan * math.pi / 2)
            chord *= env[:, None]
            a, b = max(0, i0), min(n, i0 + m)
            if b > a:
                out[a:b] += chord[a - i0 : b - i0]
        k += 1

    # breathing filter: crossfade dark/bright low-passed copies with a ~25 s LFO
    tt = t0 + np.arange(n) / sr
    lfo = 0.5 - 0.5 * np.cos(2 * np.pi * tt / 25.0)
    hd, hb = _lowpass_fir(dark, sr), _lowpass_fir(bright, sr)
    for ch in range(2):
        d = _convolve(out[:, ch], hd)
        br = _convolve(out[:, ch], hb)
        out[:, ch] = d * (1 - 0.6 * lfo) + br * (0.6 * lfo)
    out *= 1.0 + 0.12 * np.sin(2 * np.pi * tt / 17.0)[:, None]  # slow swell

    if mood == "tense":  # soft low heartbeat, ~50 bpm
        period = 1.2
        ph = (tt % period) / period
        beat = np.where(ph < 0.02, 0.5 - 0.5 * np.cos(np.pi * ph / 0.02), np.exp(-(ph - 0.02) * 9.0))
        sub = 0.4 * np.sin(2 * np.pi * _midi_hz(26) * tt) + np.sin(2 * np.pi * _midi_hz(38) * tt)
        out += (0.7 * beat * sub)[:, None]
    return out


def _reverb_ir(sr: int, rt60: float, rng: np.random.Generator) -> np.ndarray:
    n = int(rt60 * sr)
    t = np.arange(n) / sr
    decay = np.exp(-6.9 * t / rt60)
    pre = int(0.03 * sr)
    ir = np.zeros((n + pre, 2))
    h = _lowpass_fir(3000.0, sr, 255)
    for ch in range(2):
        ir[pre:, ch] = np.convolve(rng.standard_normal(n), h, mode="same") * decay
    return ir / np.sqrt((ir**2).sum(axis=0))


def generate_music_bed(moods: list[tuple[float, float, str]], out_path: Path, sample_rate: int = 48000) -> Path:
    """Synthesize an ambient bed; ``moods`` are (start_s, end_s, mood) segments.

    Gaps are filled with "neutral", unknown moods fall back to "neutral". Writes a
    24-bit stereo WAV and returns its path.
    """
    sr = sample_rate
    rng = np.random.default_rng(1848)
    segs = sorted((float(a), float(b), m if m in PROGRESSIONS else "neutral") for a, b, m in moods if b > a)
    if not segs:
        raise ValueError("moods must contain at least one (start, end, mood) segment with end > start")
    total = segs[-1][1]
    # fill gaps, merge neighbours with the same mood
    filled: list[list] = []
    cur = 0.0
    for a, b, m in segs:
        a = max(a, cur)
        if a > cur + 1e-6:
            filled.append([cur, a, "neutral"])
        if b > a:
            filled.append([a, b, m])
        cur = max(cur, b)
    merged: list[list] = []
    for s in filled:
        if merged and merged[-1][2] == s[2]:
            merged[-1][1] = s[1]
        else:
            merged.append(list(s))

    n = int(round(total * sr))
    mix = np.zeros((n, 2))
    half = MOOD_XFADE / 2
    for i, (a, b, m) in enumerate(merged):
        ra = a - half if i > 0 else 0.0
        rb = b + half if i < len(merged) - 1 else total
        ra, rb = max(0.0, ra), min(total, rb)
        seg = _render_mood(m, ra, rb, sr, rng)
        tt = ra + np.arange(len(seg)) / sr
        w = np.ones(len(seg))
        if i > 0:
            v = np.clip((tt - (a - half)) / MOOD_XFADE, 0, 1)
            w *= np.sin(v * np.pi / 2)
        if i < len(merged) - 1:
            v = np.clip((tt - (b - half)) / MOOD_XFADE, 0, 1)
            w *= np.cos(v * np.pi / 2)
        i0 = int(round(ra * sr))
        k = min(len(seg), n - i0)
        mix[i0 : i0 + k] += seg[:k] * w[:k, None]

    ir = _reverb_ir(sr, 3.8, rng)
    wet = np.stack([_convolve(mix[:, ch], ir[:, ch]) for ch in range(2)], axis=1)
    out = 0.65 * mix + 0.55 * wet

    # gentle fades, then level to about -20 dBFS RMS with a -3 dBFS peak ceiling
    fi, fo = min(int(3 * sr), n // 4), min(int(4 * sr), n // 4)
    out[:fi] *= np.linspace(0, 1, fi)[:, None] ** 2
    out[n - fo :] *= np.linspace(1, 0, fo)[:, None] ** 2
    rms = np.sqrt((out**2).mean()) + 1e-12
    out *= 10 ** (-20 / 20) / rms
    peak = np.abs(out).max()
    if peak > 10 ** (-3 / 20):
        out *= 10 ** (-3 / 20) / peak

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(out_path, out.astype(np.float32), sr, subtype="PCM_24")
    return out_path
