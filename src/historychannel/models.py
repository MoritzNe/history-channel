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


# Animation timing: every `at` below is a FRACTION of the shot's on-screen duration
# (0.0 = shot start, 1.0 = shot end), because the real duration is only known
# after narration is synthesized. None = visible from the first frame.

Extent = tuple[float, float, float, float]  # lon_min, lon_max, lat_min, lat_max


class MapMarker(BaseModel):
    name: str
    lon: float
    lat: float
    label: str | None = None  # e.g. "Berlin, 18 March"
    style: Literal["city", "capital", "battle", "uprising", "dot"] = "city"
    at: float | None = None  # pops in at this fraction
    pulse: bool = False  # keeps pulsing after it appears (use for the current focus)


class MapArrow(BaseModel):
    from_lonlat: tuple[float, float]
    to_lonlat: tuple[float, float]
    label: str | None = None
    style: Literal["advance", "retreat", "flow", "route"] = "advance"
    color: str | None = None
    at: float | None = None  # starts drawing at this fraction
    draw: float = 0.25  # fraction of the shot spent drawing the line on
    via: list[tuple[float, float]] = Field(default_factory=list)  # waypoints for routes


class MapRegion(BaseModel):
    """A highlighted area: a polygon, Natural Earth country names, or historical basemap names."""

    label: str | None = None
    polygon: list[tuple[float, float]] | None = None  # (lon, lat) ring
    countries: list[str] = Field(default_factory=list)  # modern Natural Earth names
    historical: list[str] = Field(default_factory=list)  # polygon NAMEs in the historical basemap
    color: str | None = None  # hex; renderer picks from its palette if None
    at: float | None = None  # fades in at this fraction
    hatch: bool = False  # diagonal hatching, e.g. contested/occupied areas


class MapText(BaseModel):
    """Free-floating map text, e.g. a big spaced-out area name or a sea name."""

    text: str
    lon: float
    lat: float
    size: Literal["small", "medium", "large"] = "medium"
    at: float | None = None


class MapStamp(BaseModel):
    """Date/info ticker in a map corner; the latest stamp whose `at` has passed is shown."""

    at: float = 0.0
    text: str  # e.g. "18 March 1848"


class MapSpec(BaseModel):
    title: str | None = None
    extent: Extent = (2.0, 24.0, 44.0, 56.0)
    camera_to: Extent | None = None  # if set, the camera eases from `extent` to this over the shot
    basemap: Literal["1815", "1880", "modern"] = "1815"  # borders drawn underneath
    markers: list[MapMarker] = Field(default_factory=list)
    arrows: list[MapArrow] = Field(default_factory=list)
    regions: list[MapRegion] = Field(default_factory=list)
    texts: list[MapText] = Field(default_factory=list)
    stamps: list[MapStamp] = Field(default_factory=list)


class TimelineEvent(BaseModel):
    date: str  # display text, e.g. "Mar 1848"
    label: str
    highlight: bool = False  # the event this shot is about


class CompareColumn(BaseModel):
    title: str
    points: list[str]  # 2-4 short bullet lines
    color: str | None = None


class Visual(BaseModel):
    """One shot inside a scene. Scenes split their narration time across visuals by weight.

    Animated kinds (map, stat, timeline, compare, person, title_card, quote) are drawn
    frame by frame by `historychannel.animate`; archival/generated stills get Ken Burns.
    """

    kind: Literal["archival", "map", "title_card", "quote", "generated", "stat", "timeline", "compare", "person"]

    # archival: exact Commons title wins; otherwise search with `query`
    file: str | None = None  # e.g. "File:Barrikadenkampf Berlin 1848.jpg"
    query: str | None = None

    # map
    map: MapSpec | None = None

    # title_card / quote / stat label / person name
    text: str | None = None
    subtext: str | None = None  # card subtitle, quote attribution, stat context, person role + dates

    # stat: big number that counts up if numeric, e.g. "809" or "80,000" or "1 in 3"
    value: str | None = None

    # timeline
    events: list[TimelineEvent] = Field(default_factory=list)

    # compare: exactly two columns side by side
    columns: list[CompareColumn] = Field(default_factory=list)

    # person: portrait comes from `file`/`query` (Commons), name in `text`, role in `subtext`

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
    path: Path  # image file (PNG/JPG); for animated assets, the final-state poster frame
    kind: str
    attribution: str | None = None  # credit line for the description, e.g. "Painter, 1848, Public domain, via Wikimedia Commons"
    source_url: str | None = None
    # Set for animated shots: a JSON-serializable spec handed to
    # historychannel.animate.render_frame(). Must contain "kind".
    animation: dict | None = None


# --------------------------------------------------------------------------- render input


class Clip(BaseModel):
    image: Path
    start: float
    duration: float
    motion: Motion = "zoom_in"
    caption: str | None = None
    animation: dict | None = None  # if set, frames come from animate.render_frame instead of Ken Burns


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
