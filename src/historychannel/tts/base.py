"""Provider interface and shared audio post-processing."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np
import soundfile as sf

from historychannel.models import AudioResult

from .text import normalize_for_tts, sentence_spans

SENTENCE_PAUSE = 0.25  # seconds of silence between sentences
PARAGRAPH_PAUSE = 0.55  # between paragraphs (blank line in the narration)
TARGET_RMS_DBFS = -20.0  # speech-active RMS target; keeps scenes at matching loudness
PEAK_CEILING_DBFS = -1.0
EDGE_SILENCE = 0.05  # seconds of silence kept at each end after trimming

# (sentence text as written, start seconds, end seconds) within the output WAV
SentenceTiming = tuple[str, float, float]


class TTSProvider(ABC):
    """Text -> mono WAV. Subclasses implement `_render_sentence`; timing comes for free."""

    name: str = "base"
    sample_rate: int = 24000
    default_voice: str | None = None

    @abstractmethod
    def _render_sentence(
        self, text: str, voice: str | None, speed: float,
        previous_text: str | None = None, next_text: str | None = None,
    ) -> np.ndarray:
        """Synthesize one already-normalized sentence -> float32 mono at `self.sample_rate`.

        `previous_text`/`next_text` are neighbouring sentences; providers that support
        prosody continuity (ElevenLabs) use them, others ignore them.
        """

    def synthesize(
        self, text: str, out_path: Path, voice: str | None = None, speed: float = 1.0
    ) -> AudioResult:
        return self.synthesize_with_timings(text, out_path, voice=voice, speed=speed)[0]

    def synthesize_with_timings(
        self, text: str, out_path: Path, voice: str | None = None, speed: float = 1.0
    ) -> tuple[AudioResult, list[SentenceTiming]]:
        """Synthesize sentence by sentence; returns the result and per-sentence (text, start, end)."""
        voice = voice or self.default_voice
        sr = self.sample_rate
        # (original sentence, normalized sentence, starts a new paragraph?)
        items: list[tuple[str, str, bool]] = []
        for para in (p for p in re.split(r"\n\s*\n", text) if p.strip()):
            for si, sent in enumerate(sentence_spans(para)):
                spoken = normalize_for_tts(sent)
                if any(ch.isalnum() for ch in spoken):
                    items.append((sent, spoken, si == 0))
        pieces: list[np.ndarray] = []
        timings: list[SentenceTiming] = []
        cursor = 0  # samples
        for i, (sent, spoken, new_para) in enumerate(items):
            prev_t = items[i - 1][1] if i > 0 else None
            next_t = items[i + 1][1] if i + 1 < len(items) else None
            raw = self._render_sentence(spoken, voice, speed, previous_text=prev_t, next_text=next_t)
            audio = trim_silence(np.asarray(raw, dtype=np.float32), sr, keep=0.02)
            if pieces:
                gap = PARAGRAPH_PAUSE if new_para else SENTENCE_PAUSE
                pieces.append(np.zeros(int(gap * sr), dtype=np.float32))
                cursor += len(pieces[-1])
            pieces.append(audio)
            timings.append((sent, cursor / sr, (cursor + len(audio)) / sr))
            cursor += len(audio)
        if not pieces:
            raise ValueError("Nothing to synthesize: text has no speakable content")

        lead = int(EDGE_SILENCE * sr)
        audio = np.concatenate([np.zeros(lead, np.float32), *pieces, np.zeros(lead, np.float32)])
        timings = [(s, a + EDGE_SILENCE, b + EDGE_SILENCE) for s, a, b in timings]
        audio = normalize_loudness(audio, sr)
        result = write_wav(audio, sr, out_path)
        return result, timings


# --------------------------------------------------------------------------- audio helpers


def write_wav(audio: np.ndarray, sample_rate: int, out_path: Path) -> AudioResult:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(out_path, np.clip(audio, -1.0, 1.0), sample_rate, subtype="PCM_16")
    return AudioResult(path=out_path, duration=len(audio) / sample_rate, sample_rate=sample_rate)


def _frame_rms(audio: np.ndarray, sr: int, frame_s: float = 0.02) -> np.ndarray:
    frame = max(1, int(sr * frame_s))
    n = len(audio) // frame
    if n == 0:
        return np.array([np.sqrt(np.mean(audio**2))]) if len(audio) else np.zeros(1)
    return np.sqrt(np.mean(audio[: n * frame].reshape(n, frame) ** 2, axis=1))


def trim_silence(audio: np.ndarray, sr: int, threshold_db: float = -45.0, keep: float = 0.05) -> np.ndarray:
    """Trim leading/trailing frames quieter than `threshold_db` relative to the peak frame."""
    if len(audio) == 0:
        return audio
    frame = max(1, int(sr * 0.02))
    rms = _frame_rms(audio, sr)
    ref = rms.max()
    if ref <= 0:
        return audio[:0]
    loud = np.nonzero(20 * np.log10(np.maximum(rms, 1e-10) / ref) > threshold_db)[0]
    if len(loud) == 0:
        return audio[:0]
    pad = int(keep * sr)
    start = max(0, loud[0] * frame - pad)
    end = min(len(audio), (loud[-1] + 1) * frame + pad)
    return audio[start:end]


def normalize_loudness(audio: np.ndarray, sr: int, target_dbfs: float = TARGET_RMS_DBFS) -> np.ndarray:
    """Scale so speech-active RMS hits `target_dbfs`, never letting the peak exceed the ceiling."""
    rms = _frame_rms(audio, sr)
    if rms.max() <= 0:
        return audio
    active = rms[rms > rms.max() * 0.05]  # ignore pauses
    level = float(np.sqrt(np.mean(active**2)))
    gain = 10 ** (target_dbfs / 20) / level
    peak = float(np.abs(audio).max())
    gain = min(gain, 10 ** (PEAK_CEILING_DBFS / 20) / peak)
    return (audio * gain).astype(np.float32)
