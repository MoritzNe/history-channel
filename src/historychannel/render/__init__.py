"""Render stage: Timeline -> final MP4 (Ken Burns, crossfades, overlays, mixed audio)."""

from historychannel.render.music import generate_music_bed
from historychannel.render.video import render_video

__all__ = ["generate_music_bed", "render_video"]
