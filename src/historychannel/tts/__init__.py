"""Narration: text -> mono WAV (+ per-sentence timings for subtitles).

    from historychannel.tts import get_tts
    tts = get_tts()                      # Kokoro if it loads, else macOS say
    result, timings = tts.synthesize_with_timings(scene.narration, out / f"{scene.id}.wav",
                                                  voice=script.voice)
    # timings: [(sentence_as_written, start_s, end_s), ...]
"""

from __future__ import annotations

import logging
from pathlib import Path

from historychannel.models import AudioResult

from .base import SentenceTiming, TTSProvider
from .text import normalize_for_tts, sentence_spans

log = logging.getLogger(__name__)

__all__ = [
    "AudioResult", "SentenceTiming", "TTSProvider", "KokoroTTS", "SayTTS", "ElevenLabsTTS",
    "get_tts", "synthesize", "synthesize_with_timings", "sentence_spans", "normalize_for_tts",
]


def __getattr__(name: str):  # lazy provider imports keep `import historychannel.tts` cheap
    if name == "KokoroTTS":
        from .kokoro import KokoroTTS
        return KokoroTTS
    if name == "SayTTS":
        from .say import SayTTS
        return SayTTS
    if name == "ElevenLabsTTS":
        from .elevenlabs import ElevenLabsTTS
        return ElevenLabsTTS
    raise AttributeError(name)


_PROVIDERS: dict[str, TTSProvider] = {}


def get_tts(name: str = "auto", voice: str | None = None) -> TTSProvider:
    """Provider by name: "kokoro", "say", "elevenlabs", or "auto" (Kokoro if it loads, else say).

    Instances are cached per (name, voice); the Kokoro model itself loads once per process.
    """
    key = f"{name}|{voice}"
    if key in _PROVIDERS:
        return _PROVIDERS[key]
    if name == "auto":
        try:
            prov = get_tts("kokoro", voice)
            prov.kokoro  # force download/load now so a failure falls through to say
        except Exception as e:  # noqa: BLE001 - any load failure means fall back
            log.warning("Kokoro unavailable (%s); falling back to macOS say", e)
            prov = get_tts("say", None)
    elif name == "kokoro":
        from .kokoro import KokoroTTS
        prov = KokoroTTS(voice=voice)
    elif name == "say":
        from .say import SayTTS
        prov = SayTTS(voice=voice)
    elif name == "elevenlabs":
        from .elevenlabs import ElevenLabsTTS
        prov = ElevenLabsTTS(voice=voice)
    else:
        raise ValueError(f"Unknown TTS provider {name!r}")
    _PROVIDERS[key] = prov
    return prov


def synthesize(text: str, out_path: Path, voice: str | None = None, speed: float = 1.0,
               provider: str = "auto") -> AudioResult:
    return get_tts(provider).synthesize(text, out_path, voice=voice, speed=speed)


def synthesize_with_timings(text: str, out_path: Path, voice: str | None = None, speed: float = 1.0,
                            provider: str = "auto") -> tuple[AudioResult, list[SentenceTiming]]:
    return get_tts(provider).synthesize_with_timings(text, out_path, voice=voice, speed=speed)
