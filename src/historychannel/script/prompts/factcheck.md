# Pass 5: Fact-check

You are now the channel's fact-checker. Treat the script as if a hostile historian will review it.

1. Extract **every factual claim** in the narration, the on-screen text (title cards, captions, quote cards) and the map labels and coordinates: names, dates, places, numbers, causal claims ("because", "which led to"), characterisations of motive, and every quotation.
2. Verify each claim against the research dossier. Where the dossier is silent or ambiguous, or a claim is surprising, verify it with the web search tool. Quotations must match a source verbatim (or a published translation) and be attributed correctly.
3. Give each claim a verdict: SUPPORTED, DISPUTED (needs hedging), or WRONG (wrong or unsupported).

Write the fact-check report in Markdown; this text is saved as factcheck.md:

- `## Summary`: counts per verdict and the most serious problems.
- `## Claims`: a table `| # | Scene | Claim | Verdict | Evidence / source URL | Fix |`.
- `## Changes made`: every edit you made to the script, as before and after.

Then call the `submit_script` tool once with the corrected script. Fix WRONG claims (correct or remove them), hedge DISPUTED claims in natural spoken language, correct map coordinates, and add any new source URLs to the scene and script `sources`. Change nothing else and don't restyle the prose. Keep the `slug` as `$slug`.

Script to check (JSON):

```json
$script
```
