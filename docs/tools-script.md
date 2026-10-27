# Script generation (`historychannel.script`)

Turns a topic into a validated `VideoScript` (`content/<slug>/script.json`) using the Claude API in five passes. Every pass writes a file, so you can inspect it, edit it by hand and re-run: passes whose output already exists are skipped.

| Pass | Output | What happens |
|---|---|---|
| 1. Research | `research.md` | Claude with the server-side web search tool builds a dossier: timeline, people and motives, key moments, numbers with ranges, disputed points, verified quotes with URLs, coordinates, visual leads, sources. |
| 2. Outline | `outline.md` | Central question, 10 title options (★ = pick), thumbnail concept, hook, act structure (cold open, stakes, escalation, twist, payoff, modern relevance) with durations, characters, what to cut, risk notes. |
| 3. Draft | `draft.json` | Full `VideoScript` via the `submit_script` tool (input schema = `VideoScript.model_json_schema()`), validated with pydantic plus extra checks (map specs present, quote attributions, coordinates in range, title at most 100 chars). Validation errors go back to the model as an `is_error` tool result, up to 2 repairs. |
| 4. Critique loop | `critique_<n>.json`, `rewrite_<n>.json` | A harsh-editor pass scores the script 0 to 100 (hook, clarity, open loops, characters, per-minute retention risks, filler, clichés, unsupported claims) and returns `{score, issues[], must_fix[], ...}`. If the score is below the threshold (default 80), the script is rewritten. At most 2 rounds by default. |
| 5. Fact-check | `factcheck.md`, `script.json` | Every claim is extracted and checked against the dossier and the web; the report table is saved, and the corrected script becomes `script.json`. |

`usage.json` accumulates token counts, web searches and an estimated cost across runs.

Prompts live in `src/historychannel/script/prompts/*.md` (`system.md` is the shared house style; the others are per pass, with `$placeholders` filled by `string.Template`). Iterate on those first; they matter most for quality.

## Running

```bash
# .env: ANTHROPIC_API_KEY=...   (optional HC_MODEL, HC_EFFORT)
.venv/bin/python -m historychannel.script "The Berlin March Revolution of 1848" --minutes 8
# or, once wired by the CLI: hc script "The Berlin March Revolution of 1848"
```

Options: `--minutes`, `--language` (narration language code), `--out` (default `content/<slug>`), `--model`, `--rounds`, `--threshold`, `--force` (redo all passes).

To redo one pass, delete its file and the files of the passes after it (for example, delete `critique_*.json`, `rewrite_*.json`, `factcheck.md` and `script.json` after editing `draft.json` by hand). Deleting only `script.json` re-runs just the fact-check.

From Python:

```python
from pathlib import Path
from historychannel.script import generate_script
script = generate_script("The Berlin March Revolution of 1848", minutes=8, out_dir=Path("content/berlin-1848"))
```

## Model choice

- Default: **`claude-opus-5-5`** (Claude Opus 5.5), the current most capable general Opus model. Override with `HC_MODEL` or `--model`. `claude-sonnet-5-5` is about half the price and is a reasonable choice for drafts; `claude-fable-5-1` is the most capable model but 2.5x the price of Opus 5.5.
- **Adaptive thinking** is always on for Opus 5.5 (it can't be disabled); depth is set by `HC_EFFORT` (default `high`, since the Opus 5.5 API default is `medium`).
- Opus 5.5 rejects forced `tool_choice`, so structured passes use `tool_choice: auto` plus an explicit instruction, then validate client-side and ask for a repair if needed.
- Requests go through `client.beta.messages.stream(...)` with `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`) so a safety-classifier refusal is retried server-side on Anthropic's recommended fallback model. History topics (wars, massacres) can occasionally trip classifiers. If the whole chain still refuses, the run stops with a clear error. Fallbacks are sent only for models that support them.
- Web search uses `web_search_20260209` (dynamic filtering), `max_uses` 20 per request. `pause_turn` (the server-side search loop hit its cap) is resumed automatically.

### Prompt caching

All passes send the **same tool list** (`web_search`, `submit_critique`, `submit_script`) and the **same system prompt** (with `cache_control`), followed by the research dossier and outline as cached blocks. So `tools + system + dossier (+ outline)` is a shared prefix that passes 2 to 5 read from cache (5-minute TTL; the passes run back to back). Check `cache_read_input_tokens` in `usage.json`.

## Cost per video (estimate)

Claude Opus 5.5 pricing: $4 per million input tokens, $20 per million output tokens, cache reads $0.20 per million, 5-minute cache writes 1.25x input. Web search: $10 per 1,000 searches. Thinking tokens are billed as output.

Rough token profile for an 8-minute (about 1,200-word) script with one rewrite:

| Pass | Input tokens | Output tokens (incl. thinking) | Searches |
|---|---|---|---|
| Research | 80k to 150k (search results count as input) | 6k to 12k | 10 to 20 |
| Outline | about 15k | about 4k | 0 to 2 |
| Draft | about 20k (mostly cached) | about 12k | 0 |
| Critique x2 | about 25k each (mostly cached) | about 4k each | 0 |
| Rewrite x1 | about 30k | about 12k | 0 |
| Fact-check | 30k to 60k | about 15k | 5 to 10 |
| **Total** | **about 250k to 330k** (about 80k cache reads) | **about 55k to 65k** | **about 20 to 30** |

That's about $0.85 uncached input + $0.15 cache writes + $1.20 output + $0.25 search, so **roughly $2.50 per script with Opus 5.5** (expect $1.50 to $5 depending on how much the model searches and whether a second rewrite runs). `claude-sonnet-5-5` is about half that. `usage.json` records the actual numbers per run (its `estimated_usd` uses the table in `generate.py`; check current prices at https://platform.claude.com/docs/en/about-claude/pricing).
