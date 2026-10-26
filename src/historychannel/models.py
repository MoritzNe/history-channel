"""Shared data contracts between pipeline stages.

Flow:  VideoScript (content/<slug>/script.json)
         -> tts:      one narration WAV per Scene            -> AudioResult
         -> visuals:  one image per Visual                   -> Asset
         -> pipeline: builds a Timeline from the above
         -> render:   Timeline -> final MP4

Every stage reads and writes plain files so it can be re-run in isolation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

# Output frame size for every render. Stills should be produced at >= this size;
# maps and title cards are rendered at 2x so Ken Burns zooms stay sharp.
FRAME_W = 1920
FRAME_H = 1080
FPS = 30

Motion = Literal["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "static"]
Mood = Literal["tense", "somber", "hopeful", "triumphant", "neutral"]


# --------------------------------------------------------------------------- script


class MapMarker(BaseModel):
    name: str
    lon: float
    lat: float
    label: str | None = None  # e.g. "Berlin, 18 March"


class MapArrow(BaseModel):
    from_lonlat: tuple[float, float]
    to_lonlat: tuple[float, float]
    label: str | None = None


class MapRegion(BaseModel):
    """A highlighted area, given either as a polygon or as Natural Earth country names."""

    label: str | None = None
    polygon: list[tuple[float, float]] | None = None  # (lon, lat) ring
    countries: list[str] = Field(default_factory=list)
    color: str | None = None  # hex; renderer picks from its palette if None


class MapSpec(BaseModel):
    title: str | None = None
    # lon_min, lon_max, lat_min, lat_max
    extent: tuple[float, float, float, float] = (2.0, 24.0, 44.0, 56.0)
    markers: list[MapMarker] = Field(default_factory=list)
    arrows: list[MapArrow] = Field(default_factory=list)
    regions: list[MapRegion] = Field(default_factory=list)


class Visual(BaseModel):
    """One shot inside a scene. Scenes split their narration time across visuals by weight."""

    kind: Literal["archival", "map", "title_card", "quote", "generated"]

    # archival: exact Commons title wins; otherwise search with `query`
    file: str | None = None  # e.g. "File:Barrikadenkampf Berlin 1848.jpg"
    query: str | None = None

    # map
    map: MapSpec | None = None

    # title_card / quote
    text: str | None = None
    subtext: str | None = None  # card subtitle, or quote attribution

    # generated (only used when an image provider is configured; otherwise falls back to `query`)
    prompt: str | None = None

    motion: Motion = "zoom_in"
    weight: float = 1.0
    caption: str | None = None  # lower-third label shown on screen


class Scene(BaseModel):
    id: str
    narration: str
    visuals: list[Visual]
    mood: Mood = "neutral"
    pause_after: float = 0.6  # seconds of silence after the narration
    sources: list[str] = Field(default_factory=list)


class VideoScript(BaseModel):
    slug: str
    title: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    language: str = "en"
    voice: str | None = None  # TTS voice id; provider default if None
    scenes: list[Scene]
    sources: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> VideoScript:
        return cls.model_validate_json(Path(path).read_text())

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2))


# --------------------------------------------------------------------------- stage outputs


class AudioResult(BaseModel):
    path: Path
    duration: float  # seconds
    sample_rate: int


class Asset(BaseModel):
    path: Path  # image file (PNG/JPG)
    kind: str
    attribution: str | None = None  # credit line for the description, e.g. "Painter, 1848, Public domain, via Wikimedia Commons"
    source_url: str | None = None


# --------------------------------------------------------------------------- render input


class Clip(BaseModel):
    image: Path
    start: float
    duration: float
    motion: Motion = "zoom_in"
    caption: str | None = None


class SubtitleCue(BaseModel):
    start: float
    end: float
    text: str


class AudioTrack(BaseModel):
    path: Path
    start: float = 0.0
    gain_db: float = 0.0


class Timeline(BaseModel):
    clips: list[Clip]
    narration: list[AudioTrack]
    music: AudioTrack | None = None  # looped/trimmed to video length, ducked under narration
    subtitles: list[SubtitleCue] = Field(default_factory=list)
    duration: float
    burn_subtitles: bool = True
    width: int = FRAME_W
    height: int = FRAME_H
    fps: int = FPS
