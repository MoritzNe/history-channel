"""Kokoro-82M (Apache-2.0) via kokoro-onnx: free, local, CPU-fast on Apple Silicon."""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
import requests

from historychannel import CACHE_DIR

from .base import TTSProvider

log = logging.getLogger(__name__)

MODEL_DIR = CACHE_DIR / "models" / "kokoro"
_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"

# variant -> (filename, size bytes, sha256). Sizes/digests from the GitHub release API.
MODEL_VARIANTS: dict[str, tuple[str, int, str]] = {
    "fp32": ("kokoro-v1.0.onnx", 325505369,
             "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a"),
    "fp16": ("kokoro-v1.0.fp16.onnx", 163527961,
             "f3a290d384fbb27966d462905c71a46cef9e5fd00516b40df32a0b4afe77ac96"),
    "int8": ("kokoro-v1.0.int8.onnx", 114119327,
             "ae315a79b623f244700e4afb9246c46a26066782e049ba174bf3ba433970ee9c"),
}
VOICES_FILE = ("voices-v1.0.bin", 28214398,
               "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d")

DEFAULT_VARIANT = os.environ.get("HC_KOKORO_VARIANT", "fp32")
# Calm American male narrator; hours of training data (grade C+), the steadiest male voice.
DEFAULT_VOICE = os.environ.get("HC_KOKORO_VOICE", "am_michael")


def _download(filename: str, size: int, sha256: str, dest_dir: Path = MODEL_DIR) -> Path:
    dest = dest_dir / filename
    if dest.exists() and dest.stat().st_size == size:
        return dest
    dest_dir.mkdir(parents=True, exist_ok=True)
    url = f"{_RELEASE}/{filename}"
    tmp = dest.with_suffix(dest.suffix + ".part")
    log.info("Downloading %s (%.0f MB)", url, size / 1e6)
    h = hashlib.sha256()
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if sys.stderr.isatty():
                    sys.stderr.write(f"\r  {filename}: {done / 1e6:6.0f} / {size / 1e6:.0f} MB")
        if sys.stderr.isatty():
            sys.stderr.write("\n")
    if done != size or h.hexdigest() != sha256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Download of {filename} failed verification (size {done}, expected {size})")
    tmp.rename(dest)
    return dest


def ensure_model(variant: str = DEFAULT_VARIANT) -> tuple[Path, Path]:
    """Download (once) and return (model_path, voices_path)."""
    if variant not in MODEL_VARIANTS:
        raise ValueError(f"Unknown Kokoro variant {variant!r}; choose from {list(MODEL_VARIANTS)}")
    return _download(*MODEL_VARIANTS[variant]), _download(*VOICES_FILE)


@lru_cache(maxsize=None)
def _load(variant: str):
    import onnxruntime as ort
    from kokoro_onnx import Kokoro

    ort.set_default_logger_severity(3)  # silence constant-folding warnings on load

    model_path, voices_path = ensure_model(variant)
    return Kokoro(str(model_path), str(voices_path))


class KokoroTTS(TTSProvider):
    name = "kokoro"
    sample_rate = 24000

    def __init__(self, voice: str | None = None, variant: str = DEFAULT_VARIANT, lang: str | None = None):
        self.default_voice = voice or DEFAULT_VOICE
        self.variant = variant
        self.lang = lang  # None -> inferred from the voice prefix
        self._lock = threading.Lock()

    @property
    def kokoro(self):
        return _load(self.variant)

    def voices(self) -> list[str]:
        return self.kokoro.get_voices()

    def _style(self, voice: str) -> str | np.ndarray:
        """Accept "am_michael" or a blend "am_michael:0.6,bm_george:0.4"."""
        if "," not in voice and ":" not in voice:
            return voice
        parts = []
        for item in voice.split(","):
            name, _, w = item.strip().partition(":")
            parts.append((self.kokoro.get_voice_style(name), float(w or 1.0)))
        total = sum(w for _, w in parts)
        return sum(style * (w / total) for style, w in parts).astype(np.float32)

    def _lang_for(self, voice: str) -> str:
        if self.lang:
            return self.lang
        return "en-gb" if voice.lstrip().startswith("b") else "en-us"

    def _render_sentence(self, text: str, voice: str | None, speed: float, **_context) -> np.ndarray:
        voice = voice or self.default_voice
        with self._lock:  # onnxruntime session is not guaranteed re-entrant across threads
            audio, sr = self.kokoro.create(
                text, voice=self._style(voice), speed=speed, lang=self._lang_for(voice),
                trim=True, sentence_pause=0.25, clause_pause=0.1,
            )
        assert sr == self.sample_rate
        return np.asarray(audio, dtype=np.float32)
