# History Channel

Script-first pipeline that turns a researched history script into a narrated YouTube video.

```
topic ──hc script──▶ content/<slug>/script.json ──hc build──▶ output/<slug>.mp4 ──hc upload──▶ YouTube (private)
         Claude API: research → outline → draft            Kokoro TTS · Wikimedia Commons · maps
         → critique loop → fact-check                      · title/quote cards · Ken Burns · music bed
```

## Setup

```bash
uv venv --python 3.12 && uv sync --extra dev && uv pip install -e .
cp .env.example .env   # only needed for `hc script` (Claude API), ElevenLabs, image generation, upload
```

Requires `ffmpeg` on PATH. The Kokoro voice model (~340 MB) and Natural Earth map data download into `.cache/` on first use.

## Usage

```bash
hc build content/1848-german-revolution/script.json            # full 1080p render (~2.5 min for 8 min of video on an M5 Pro)
hc build content/1848-german-revolution/script.json --preview  # fast 540p
hc script "The Thirty Years' War" --minutes 10                 # needs ANTHROPIC_API_KEY
hc upload content/1848-german-revolution/script.json           # needs client_secret.json, see docs/tools-upload.md
```

Outputs: `output/<slug>.mp4`, `.srt` subtitles, `.credits.txt` image attributions for the description.
Every stage caches under `build/<slug>/`, so editing one scene only re-narrates/re-fetches that scene.

## Layout

| Path | Stage |
|---|---|
| `src/historychannel/models.py` | Shared contracts: `VideoScript` → `Scene` → `Visual`; `Timeline` for the renderer |
| `src/historychannel/script/` | Claude API script generation; prompts in `script/prompts/*.md` |
| `src/historychannel/tts/` | Kokoro (default), macOS `say`, ElevenLabs; TTS text normalization |
| `src/historychannel/visuals/` | Wikimedia Commons fetch + grading, maps, title/quote cards, optional AI image providers |
| `src/historychannel/render/` | Ken Burns frames → libx264, crossfades, captions, subtitles, music bed, ducking, loudnorm |
| `src/historychannel/pipeline.py` | Narration → assets → timeline → render |
| `src/historychannel/upload/` | YouTube Data API upload with description, credits and AI disclosure |
| `content/<slug>/` | Scripts and research notes, one folder per video |
| `docs/tools-*.md` | Tool research and the reasoning behind each choice |

Tests: `pytest` (fast) or `pytest -m "slow or network"`.
