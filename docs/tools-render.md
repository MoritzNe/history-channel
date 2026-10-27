# Render stage: tool choice

Goal: Timeline (stills + narration + music + captions + subtitles) -> YouTube-ready MP4
(H.264 high, yuv420p, 1080p30, AAC 48 kHz, ~-14 LUFS). Fast on one Mac, deterministic.

## Options compared

| Concern | Pure FFmpeg filtergraph | MoviePy 2 | Remotion (React) |
|---|---|---|---|
| Ken Burns on stills | `zoompan` rounds the crop origin to whole input pixels per frame -> visible stair-step jitter on slow zooms. Fixes: pre-upscale 4-8x before `zoompan` (slow, huge frames), or `scale`+`crop` with `t` expressions (crop x/y are also integer -> same jitter unless upscaled). | Per-frame Python `resize` via `transform`; smooth if you do the subpixel crop yourself, but slow (~1-3 fps at 1080p) | CSS `transform: scale()` on GPU, subpixel smooth, easy easing |
| Crossfades | `xfade` with offset math (offset = cumulative duration - n*fade); a 100-clip chain is one giant fragile graph | `CompositeVideoClip` + `crossfadein`, easy | Trivial (`interpolate` opacity) |
| Subtitles | `subtitles`/`ass` filters need **libass**; `drawtext` needs **freetype**. The Homebrew ffmpeg 8.0.1 here has *neither*. | `TextClip` needs ImageMagick/PIL fonts; ok | HTML/CSS text, best typography |
| Audio ducking | `sidechaincompress` works but tuning attack/release/threshold per voice is fiddly | none built in; numpy by hand | none; prep audio outside |
| Loudness | `loudnorm` two-pass (measure, then `linear=true`) is the standard | via ffmpeg | via ffmpeg |
| Speed / deps | Fastest when it works; zero Python deps | slow, pulls imageio/decorator stack | Node + headless Chrome, ~0.5-2x realtime, separate toolchain |

## Decision: hybrid "Python frames -> libx264", ffmpeg for audio/mux

Implemented in `src/historychannel/render/`:

* **Ken Burns** (`motion.py`): each still is cover-cropped to 16:9 and resampled once
  to 1.5x the output size. Per frame a float crop box is computed (cosine ease, zoom
  1.02 -> 1.14, pans at 1.10 zoom using 85 % of the margin) and sampled with
  `PIL.Image.resize(box=..., BICUBIC)`. Pillow derives filter taps from the float box,
  so motion is truly subpixel and the downscale is antialiased. Measured jitter
  (centroid residual vs. smooth fit, `test_ken_burns_is_subpixel_smooth`): < 0.03 px;
  integer-snapped crops (the zoompan failure mode) are several times worse. Note: a
  zoom starting at exactly 2.0x downscale hit an 8-bit rounding glitch in Pillow
  (~0.15 px); the 1.02 start / 1.5x source avoid it.
* **Crossfades**: abutting clips share a 0.7 s window (clip windows extended by half
  a fade on each side; capped at 40 % of the shorter clip); cosine blend in numpy.
  0.8 s fade from black, 1.2 s fade to black. No `xfade` offset bookkeeping.
* **Text** (`overlays.py`): rasterised with Pillow (Baskerville captions, Avenir Next
  subtitles, system-font fallbacks) and alpha-composited only over their bounding box.
  Captions: lower third at x=96, bar bottom at 0.80 H, gold accent rule, dark 66 %
  bar, soft shadow, fade in at +0.5 s / out 0.6 s before clip end. Subtitles:
  bottom-centre, white with blurred dark halo, <= 2 lines x 42 chars (balanced
  break), over-long cues split in time by character share. Bands never overlap.
  `.srt` sidecar (same re-wrapped cues) is always written next to the MP4.
* **Parallelism**: the frame range is cut into ~3 chunks per worker; a process pool
  renders each chunk and pipes rgb24 into its own `libx264` (crf 18, medium, high,
  bt709 tags) writing MPEG-TS; segments are joined with the concat demuxer
  (`-c copy`). libx264 is the bottleneck, not Python (~25 ms/frame).
* **Audio** (`audio.py`): every input decoded by ffmpeg to 48 k stereo float; mixing in
  numpy. Music is looped with 2 s equal-power crossfades, faded in/out, auto-levelled
  against measured speech RMS (-20 dB under voice, -13 dB in pauses) with an
  envelope follower (10 ms frames, 250 ms hold, 0.12 s attack, 0.7 s release) -
  i.e. sidechain ducking without `sidechaincompress` tuning. Then two-pass
  `loudnorm` I=-14, TP=-1, LRA=11 (linear), AAC 192 k (`aac_at` if present).
* **Music bed** (`music.py`): see module docstring; any licensed file in
  `Timeline.music` replaces it.

Why not MoviePy: it would do the same Python per-frame work, with more overhead and
less control. Why not pure filtergraph: jitter fixes cost more than our approach,
and the installed ffmpeg cannot render text at all.

## When Remotion is worth it

Switch (or add a Remotion sub-render producing ProRes/PNG-sequence clips that we treat
as "stills with motion=static") once we need **animated** maps (route arrows drawing
on, borders morphing, camera flights), kinetic typography or animated title/quote
cards. That is layout + animation work where React/CSS/SVG wins; the final assembly,
audio and loudness should stay here.

## Speed

Measured on the M5 Pro (18 cores), 1080p30, libx264 medium crf 18, 105 clips +
subtitles + music: **7 min of video in 110 s (3.8x realtime)**, 12 workers x 3 x264
threads (tried 6x3, 9x2, 12x1, 12x2, 16x2, 18x1). The 15 s test renders in ~4 s as a
preview. `preview=True` renders 960x540 with `veryfast`.
Overrides: `HC_RENDER_WORKERS`, `HC_RENDER_THREADS`. Callers must use an
`if __name__ == "__main__":` guard (spawn-based process pool on macOS).
