# Narration (TTS)

Checked October 2026. Code: `src/historychannel/tts/`.

## Pick: Kokoro-82M via `kokoro-onnx` (default)

- Apache-2.0 open weights, 82M params, 24 kHz mono. Runs on CPU via onnxruntime;
  on the M5 Pro it renders ~16 s of audio in ~2.4 s (RTF ~0.15) with no GPU setup.
- Model files come from the GitHub release `thewh1teagle/kokoro-onnx` `model-files-v1.1`.
  They download lazily to `.cache/models/kokoro/` and are checked against size and SHA-256:

  | file | size | note |
  |---|---|---|
  | `kokoro-v1.0.onnx` (fp32) | 325 MB | **default**: reference quality |
  | `kokoro-v1.0.fp16.onnx` | 164 MB | ~20% faster, slight waveform drift; `HC_KOKORO_VARIANT=fp16` |
  | `kokoro-v1.0.int8.onnx` | 114 MB | smallest; quantization artefacts reported |
  | `voices-v1.0.bin` | 28 MB | all 54 voice style vectors |

  Default footprint: 344 MB on disk.
- API used (kokoro-onnx 0.6.1): `Kokoro(model_path, voices_path).create(text, voice=..., speed=..., lang="en-us"|"en-gb", trim=True)`
  returns `(float32 audio, 24000)`. `voice` can also be an ndarray, which is how voice blends work.
  It splits phonemes at 510 tokens internally. We still synthesize **one sentence per call**,
  so the start and end of every sentence is known exactly. That gives accurate subtitle timing.

### Voice: `am_michael` (override with `HC_KOKORO_VOICE` or `VideoScript.voice`)

The grades come from the official VOICES.md (hexgrad/Kokoro-82M). Training data drives stability.

| voice | grade | data | note |
|---|---|---|---|
| `am_michael` | C+ | hours | calm, mid-low American male; the steadiest male voice. **Chosen** |
| `am_fenrir` | C+ | hours | deeper and more dramatic, a little faster |
| `bm_george` | C | minutes | classic British "documentary" timbre, but less stable |
| `bm_lewis` | D+ | hours | deep British male, lower graded quality |
| `bf_emma` | B- | 10-100 h | best British voice; female |
| `af_heart` / `af_bella` | A / A- | — | best overall quality; female, brighter |

The voices were not compared by ear; the choice rests on these grades. All sample durations were
similar (13.7–16.2 s for the same 2-sentence line). A British flavour is possible with a blend:
`voice="am_michael:0.6,bm_george:0.4"`. If a female narrator is acceptable, `af_heart` and `bf_emma`
are technically the best voices.

### Text normalization (needed: espeak front end)

Unaided, Kokoro reads "1848" as "one thousand eight hundred forty eight" and "IV" as "roman four".
`normalize_for_tts` fixes years, decades and year ranges; "18 March" / "March 18"; regnal
numerals after names ("Friedrich Wilhelm the Fourth"); "World War II"; St./Gen./Dr./c./e.g./i.e.

Counts such as "1848 soldiers" and "3,000 thalers" are left alone. Subtitles keep the text as written.

## Fallback: macOS `say`

`SayTTS` (voice `Daniel`, en_GB) writes AIFF and converts it to 24 kHz WAV with ffmpeg. It is
always available but clearly robotic. Use it only when Kokoro can't load (`get_tts("auto")` does this).

## Paid upgrades (no keys configured; verify prices before budgeting)

| service | price (third-party reports, Jun–Sep 2026) | notes |
|---|---|---|
| ElevenLabs Multilingual v2 / v3 | ~$0.10 per 1k chars API (~$100/M); Flash $0.05/1k | best narration quality and voice library; `ElevenLabsTTS` implemented (`ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, default "George") |
| Gemini 2.5 Flash / Pro TTS | $0.50 / $1 per M text tokens in; $10 / $20 per M audio tokens out (25 tok/s audio, so ~$0.015/min Flash) | style can be steered by prompt ("calm documentary narrator"); not implemented |
| Inworld TTS-1 / TTS-1-Max | ~$5 / $10 per M chars | very cheap and highly ranked on TTS arenas; not implemented |

A 10-minute video uses about 9k characters. That costs about $0.90 on ElevenLabs and a few cents on
Inworld or Gemini Flash. ElevenLabs also limits commercial use by subscription tier, so check its licence terms.

## Public API

```python
from historychannel.tts import get_tts, synthesize, synthesize_with_timings, sentence_spans, normalize_for_tts
tts = get_tts("auto" | "kokoro" | "say" | "elevenlabs", voice=None)   # cached per process
tts.synthesize(text, out_path, voice=None, speed=1.0) -> AudioResult
tts.synthesize_with_timings(text, out_path, voice=None, speed=1.0) -> (AudioResult, [(sentence, start_s, end_s)])
```

Output: 16-bit mono 24 kHz WAV; 0.25 s sentence / 0.55 s paragraph pauses; 50 ms edge silence;
speech RMS normalized to -20 dBFS, peaks kept at or below -1 dBFS.
