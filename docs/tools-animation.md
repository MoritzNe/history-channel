# Animation stage: animated maps

Goal: explainer-style map shots (borders of the period, regions that fill in, arrows that
draw on, markers that pop, a date ticker) rendered frame by frame inside the existing
Python -> libx264 renderer, at <= 40 ms per 1080p frame per worker.

Code: `src/historychannel/animate/maps.py` (prepare + per-frame drawing),
`animate/_mapbase.py` (basemaps, projection, base layer, geometry; main process only),
`animate/style.py` (shared palette/easing). Stat/timeline/compare/person/title/quote
animations live in `animate/cards.py`.

## Decisions

| Concern | Choice | Why |
|---|---|---|
| Historical borders | [aourednik/historical-basemaps](https://github.com/aourednik/historical-basemaps) `world_1815` / `world_1880` GeoJSON, cached in `.cache/historical-basemaps/` | Only open world-scale dataset with period polities; attributes `NAME`, `ABBREVN`, `SUBJECTO` (overlord), `PARTOF`, `BORDERPRECISION`. **License GPL-3.0** -> credited in the asset attribution (`Historical borders: aourednik/historical-basemaps (GPL-3.0)`), which lands in `<slug>.credits.txt`. Coastlines/rivers/lakes stay Natural Earth (public domain); polities are clipped to NE land so coasts match. |
| Base layer | matplotlib + geopandas once per shot, at up to 8192 px wide | Reuses `visuals/maps.py` loading. Sized so the tightest camera view still has >= 1.25 base px per output px (`SUPERSAMPLE`), min 3840, cap 8192. Line widths are set for the geometric-mean zoom, so borders thicken slightly while zooming in, like a real map zoom. |
| Projection | Equirectangular, x scaled by cos(mid-latitude of all camera views) | Same look as the static maps, and linear: stored as an affine `x = a*lon + b, y = c*lat + d` in the anim dict; workers never need pyproj/geopandas. |
| Per-frame base sampling | `skia.Canvas.drawImageRect` with trilinear mipmaps | ~10 ms for any zoom level at 1080p and sub-pixel smooth. PIL box-resize from an 8K source was 40-110 ms. |
| Overlays | skia-python, drawn straight into a numpy-backed RGBA surface | Anti-aliased paths, dashes (`DashPathEffect`), draw-on (`TrimPathEffect`), text blobs with letter-spacing; no matplotlib in workers. |
| Label placement | Greedy, decided once in `prepare` | Candidate positions are scored against markers, arrow curves, other labels, title/stamp boxes, frame edges and the subtitle band, in the start, middle and end camera views. Offsets are stored in 1080p px relative to the anchor, so labels track their point during the zoom and never jump. Region labels are additionally scored on how much of the label lies inside the region. During the shot, labels fade out as the camera carries them to the frame edge. |
| Time | Every `at` is a fraction of the shot; start = max(at x duration, `SHOT_INTRO`) | The real duration is only known after TTS. Nothing animates during the incoming crossfade. `at=None` = visible from frame 0. |
| Scale | All sizes are 1080p px x `height/1080` | Preview (960x540) looks identical to the full render, just smaller. |

## Elements

* **Camera**: ease-in-out from `extent` to `camera_to`, zoom interpolated in log space with
  the centre tied to zoom progress (no "swing"). Without `camera_to`, a 4 % drift-in so
  maps never freeze.
* **Regions** (`polygon`, Natural Earth `countries`, historical `historical` names; any
  combination is unioned): fill fades to 20 % alpha over `FADE`; outline (dark casing +
  colour) draws around every ring in parallel over `DRAW`; `hatch` = diagonal hatching
  clipped to the shape; spaced-caps label fades in once the outline is half drawn.
* **Arrows**: quadratic arc (no `via`) or centripetal Catmull-Rom through `via`; drawn on
  over `draw` x duration with the arrowhead riding the tip. Styles: `advance` (thick red,
  dark casing), `retreat` (blue dashed), `flow` (thin gold, semi-transparent), `route`
  (dotted, a dot rides the tip, no head). The line stops short of a marker at either end;
  arrows that start away from a marker get a small tail dot. The label (italic) fades in at
  60 % drawn, beside the curve.
* **Markers**: `ease_out_back` pop + one expanding ring; `pulse=True` repeats a soft ring
  every 1.8 s. `city` gold dot, `capital` gold square-in-circle (bold label), `battle`
  crossed swords in a red-ringed disc, `uprising` red burst, `dot` small parchment dot.
* **Texts**: spaced italic caps (`small` 20 / `medium` 28 / `large` 40 px), dim parchment.
* **Stamps**: top-right panel with gold rule; on change the old date slides up and fades,
  the new one slides in (0.55 s). Panel width fits the longest stamp so it never resizes.
* **Title**: top-centre with gold rule and a top shade; vignette over the whole frame.

## Renderer integration

`render/video.py`: `ClipPlan.animation` carries `Clip.animation`. `FrameRenderer._clip_frame`
calls `animate.render_frame(anim, t - clip.start, clip.duration, W, H)`; crossfades, global
fades, captions and subtitles are applied on top exactly as for stills. If `render_frame`
raises (or returns a wrong shape), the clip is logged once and falls back to Ken Burns on
`clip.image` (the poster) for the rest of that worker's chunk. Anim dicts are plain JSON,
so they pickle into the process pool; the base PNG is loaded once per worker
(`_SCENES`, 2 entries, ~200 MB each for an 8K base).

## Performance (M5 Pro)

* `prepare`: 1.5-3 s per map (8K base, matplotlib) plus the 3840x2160 poster frame.
* `frame` at 1920x1080: **mean 28 ms, p95 32 ms** in one process (demo map: 2 regions,
  4 arrows, 5 markers, stamps, title). ~10 ms base sampling, ~5 ms RGBA->RGB copy, the rest
  overlays.
* End to end: the 12 s demo renders in 4.9 s through the real renderer (12 workers).

## Authoring tips (for scripts)

* Use `basemap="1815"` for 1815-1866 stories, `"1880"` after 1871 (unified Germany,
  Austria-Hungary, Italy), `"modern"` only for present-day context. Polity names:
  `docs/basemap-names.md`.
* Keep `camera_to` within ~4x of `extent`; deeper zooms hit the 8K cap and soften.
* Stagger `at` values (0.1-0.6) so elements arrive one idea at a time; keep 4-6 animated
  elements per shot.
* Labels near the bottom 20 % of the frame are avoided where possible (subtitles live there).

Demo: `.venv/bin/python scripts/map_demo.py --video` -> `build/samples/animate/map_demo.mp4`.
