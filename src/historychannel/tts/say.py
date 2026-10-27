"""macOS `say` fallback: always available on a Mac, robotic next to Kokoro."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from .base import TTSProvider

BASE_RATE_WPM = 175


class SayTTS(TTSProvider):
    name = "say"
    sample_rate = 24000

    def __init__(self, voice: str | None = None):
        if not shutil.which("say"):
            raise RuntimeError("macOS `say` not found")
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg not found on PATH (needed to convert say's AIFF)")
        self.default_voice = voice or "Daniel"  # en_GB male

    def _render_sentence(self, text: str, voice: str | None, speed: float, **_context) -> np.ndarray:
        with tempfile.TemporaryDirectory() as td:
            txt, aiff, wav = Path(td, "in.txt"), Path(td, "out.aiff"), Path(td, "out.wav")
            txt.write_text(text)  # via file so text starting with "-" is never parsed as a flag
            cmd = ["say", "-v", voice or self.default_voice, "-r", str(int(BASE_RATE_WPM * speed)),
                   "-o", str(aiff), "-f", str(txt)]
            subprocess.run(cmd, check=True, capture_output=True)
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff),
                 "-ac", "1", "-ar", str(self.sample_rate), "-c:a", "pcm_s16le", str(wav)],
                check=True, capture_output=True,
            )
            audio, _ = sf.read(wav, dtype="float32")
        return audio
