# Pass 4: Harsh editor critique (round $round)

You are now the channel's most demanding editor. You have watched thousands of history videos lose their audience in the first minute. Find every reason a viewer would click away and every claim a historian would object to. Be specific and unsentimental; praise does not help the writer.

Target length: about $minutes minutes, about $words words. The current draft has $actual_words narration words across $scene_count scenes.
$automatic_checks
The script to critique is below. Score it from 0 to 100 against this rubric (weights in brackets):

1. **Hook [20]**: Does scene 1 start in a specific, vivid moment? Is the central question clear within about 30 seconds? Any throat-clearing?
2. **Clarity [15]**: Could a listener follow it on one hearing? Too many names, dates or abstractions at once? Are people re-identified when they return?
3. **Open loops and structure [15]**: Does each act raise a question the next one answers? Is there a real twist and a satisfying payoff? Does the modern relevance feel earned?
4. **Characters [10]**: Named people with clear motives and stakes, rather than faceless forces?
5. **Retention [15]**: Walk through the script minute by minute (about 150 words per minute) and flag every stretch where attention would sag, and why.
6. **Economy [10]**: Filler, repetition, throat-clearing, sentences to cut.
7. **Language [5]**: Clichés, purple prose, empty adjectives, sentences too long for the ear.
8. **Accuracy and honesty [10]**: Claims the research dossier doesn't support, disputed figures stated as fact, quotes that aren't verbatim or attributed, invented detail.

Also check the visual plan: 2 to 4 visuals per scene, real coordinates on maps, search queries likely to find period imagery, quote cards only for verified quotes.

Call the `submit_critique` tool once with:

- `score`: integer from 0 to 100. Be honest: 85 or more means you would publish it as is.
- `issues`: every problem you found, each as `{scene_id, category, problem, suggestion}`, where category is one of hook, clarity, structure, characters, retention, economy, language, accuracy, visuals, length.
- `must_fix`: the short list of changes without which the video should not ship, each a single imperative sentence.
- `retention_risks`: a list of `{minute, risk}`.
- `cut`: exact phrases or sentences to delete.
- `cliches`: exact clichés found.
- `unsupported_claims`: exact claims the dossier does not back.

Script to critique (JSON):

```json
$script
```
