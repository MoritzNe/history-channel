"""AI image generation for `generated` visuals (optional; only active when an API key is set).

Providers return raw image bytes; the caller runs them through the normal 16:9 prep/grade.
YouTube requires disclosure of realistic synthetic content, so generated assets carry an
"AI-generated illustration" attribution and the house style steers away from photorealism.
"""

from __future__ import annotations

import base64
import hashlib
import os
import time
from pathlib import Path
from typing import Protocol

import requests

from historychannel import CACHE_DIR

HOUSE_STYLE = os.environ.get(
    "HC_IMAGE_STYLE",
    "19th-century oil painting and lithograph style, muted earthy palette, soft natural light, "
    "visible brushwork, period-accurate clothing and architecture, documentary illustration, "
    "no text, no captions, no modern objects, not photorealistic",
)


def styled(prompt: str, style: str | None = None) -> str:
    return f"{prompt.strip().rstrip('.')}. {style or HOUSE_STYLE}"


class ImageProvider(Protocol):
    name: str

    def generate(self, prompt: str) -> bytes: ...


def _post(url: str, headers: dict, payload: dict, tries: int = 3, timeout: int = 180) -> dict:
    delay = 2.0
    for attempt in range(tries):
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        if r.status_code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
            raise RuntimeError(f"{url}: HTTP {r.status_code}: {r.text[:300]}")
        time.sleep(delay)
        delay *= 2
    raise RuntimeError("unreachable")


class FalFluxProvider:
    """fal.ai FLUX via the synchronous endpoint https://fal.run/<model>."""

    name = "FLUX via fal.ai"

    def __init__(self, key: str | None = None, model: str | None = None):
        self.key = key or os.environ["FAL_KEY"]
        self.model = model or os.environ.get("HC_FAL_MODEL", "fal-ai/flux/dev")

    def generate(self, prompt: str) -> bytes:
        data = _post(
            f"https://fal.run/{self.model}",
            {"Authorization": f"Key {self.key}", "Content-Type": "application/json"},
            {
                "prompt": prompt,
                "image_size": {"width": 1920, "height": 1080},
                "num_images": 1,
                "output_format": "png",
                "enable_safety_checker": True,
            },
        )
        url = data["images"][0]["url"]
        if url.startswith("data:"):
            return base64.b64decode(url.split(",", 1)[1])
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        return r.content


class OpenAIImageProvider:
    """OpenAI Images API (gpt-image-1 returns base64)."""

    name = "OpenAI gpt-image"

    def __init__(self, key: str | None = None, model: str | None = None):
        self.key = key or os.environ["OPENAI_API_KEY"]
        self.model = model or os.environ.get("HC_OPENAI_IMAGE_MODEL", "gpt-image-1")

    def generate(self, prompt: str) -> bytes:
        data = _post(
            "https://api.openai.com/v1/images/generations",
            {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            {"model": self.model, "prompt": prompt, "size": "1536x1024", "quality": "high", "n": 1},
        )
        item = data["data"][0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        r = requests.get(item["url"], timeout=120)
        r.raise_for_status()
        return r.content


def get_provider() -> ImageProvider | None:
    """First configured provider (HC_IMAGE_PROVIDER=fal|openai forces one), else None."""
    forced = os.environ.get("HC_IMAGE_PROVIDER", "").lower()
    if forced in ("", "fal") and os.environ.get("FAL_KEY"):
        return FalFluxProvider()
    if forced in ("", "openai") and os.environ.get("OPENAI_API_KEY"):
        return OpenAIImageProvider()
    return None


def generate_cached(provider: ImageProvider, prompt: str) -> Path:
    """Generate once per (provider, prompt); reruns reuse the cached file."""
    d = CACHE_DIR / "imagegen"
    d.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{provider.name}|{prompt}".encode()).hexdigest()[:20]
    path = d / f"{key}.png"
    if not path.exists():
        tmp = path.with_suffix(".part")
        tmp.write_bytes(provider.generate(prompt))
        tmp.replace(path)
    return path
