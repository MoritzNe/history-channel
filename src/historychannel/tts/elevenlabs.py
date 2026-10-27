"""ElevenLabs REST provider (paid). Active only when ELEVENLABS_API_KEY is set.

Requests raw 16-bit PCM at 24 kHz so no MP3 decoding is needed, and passes the
neighbouring sentences as previous_text/next_text for prosody continuity.
Not tested live (no key configured); written against the v1 text-to-speech API docs.
"""

from __future__ import annotations

import os
import time

import numpy as np
import requests

from .base import TTSProvider

API_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
# "George" premade voice: warm, mature British male narrator. Override per script via `voice`.
DEFAULT_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
DEFAULT_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")


class ElevenLabsTTS(TTSProvider):
    name = "elevenlabs"
    sample_rate = 24000

    def __init__(self, voice: str | None = None, model_id: str = DEFAULT_MODEL,
                 api_key: str | None = None, stability: float = 0.5, similarity_boost: float = 0.75):
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY")
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is not set")
        self.default_voice = voice or DEFAULT_VOICE_ID
        self.model_id = model_id
        self.voice_settings = {"stability": stability, "similarity_boost": similarity_boost}

    def _render_sentence(self, text: str, voice: str | None, speed: float,
                         previous_text: str | None = None, next_text: str | None = None) -> np.ndarray:
        body: dict = {
            "text": text,
            "model_id": self.model_id,
            "voice_settings": {**self.voice_settings, "speed": max(0.7, min(1.2, speed))},
        }
        if previous_text:
            body["previous_text"] = previous_text
        if next_text:
            body["next_text"] = next_text
        url = API_URL.format(voice_id=voice or self.default_voice)
        headers = {"xi-api-key": self.api_key, "Content-Type": "application/json"}
        for attempt in range(4):
            r = requests.post(url, params={"output_format": f"pcm_{self.sample_rate}"},
                              json=body, headers=headers, timeout=120)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            break
        if r.status_code != 200:
            raise RuntimeError(f"ElevenLabs TTS failed ({r.status_code}): {r.text[:300]}")
        return np.frombuffer(r.content, dtype="<i2").astype(np.float32) / 32768.0
