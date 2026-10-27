# Pass 4: Harsh editor critique (round $round)

You are now the channel's most demanding editor. You have watched thousands of explainer videos lose their audience. Find every reason a viewer would click away, every place they would get lost, and every claim a historian would object to. Be specific and unsentimental; praise does not help the writer.

Target length: about $minutes minutes, about $words words. The current draft has $actual_words narration words across $scene_count scenes.
$automatic_checks
The script to critique is below. Score it from 0 to 100 against this rubric (weights in brackets):

1. **Hook and question [15]**: Does the opening show the big picture moving on a map and pose one clear central question within about 20 seconds? Any throat-clearing?
2. **Explanation [20]**: Does the viewer come away understanding *why*: structures, interests, power, geography? Is the answer to the central question stated explicitly, with reasons, and does it follow from what was shown?
3. **Geography and structure shown visually [15]**: Is every place, border, membership, movement and option that the narration relies on actually drawn on a map at the moment it is spoken? Are maps purposeful (one idea each, camera moves between scales, arrows for movements, regions for memberships and options) rather than decorative? Is the recurring timeline used at act transitions? Are `at` fractions plausibly timed to the words they illustrate (between 0.05 and 0.95, staggered in spoken order)?
4. **Clarity and signposting [15]**: Could a listener follow it on one hearing? Is the structure announced and kept ("three reasons ... first ... second")? Too many names or dates at once? Are people introduced with a person card and re-identified when they return?
5. **Retention [10]**: Walk through the script minute by minute (about 150 words per minute) and flag every stretch where attention would sag, or where the picture holds too long (over about 12 seconds for a still or card, over about 18 for a map).
6. **Economy and language [10]**: Filler, repetition, melodrama, clichés, empty adjectives, sentences too long for the ear.
7. **Accuracy and honesty [15]**: Claims the research dossier doesn't support, disputed figures stated as fact (in narration, stat cards or map labels), numbers on screen that differ from the narration, quotes that aren't verbatim or attributed, implausible coordinates, `historical` region names unlikely to exist in the chosen basemap.

Also check the shot mix: at least 55% maps, about 25% motion graphics, at most 20% archival, and captions only on archival stills.

Call the `submit_critique` tool once with:

- `score`: an integer from 0 to 100. Be honest: 85 or more means you would publish it as is.
- `issues`: every problem you found, each as `{scene_id, category, problem, suggestion}`. `category` is one of hook, clarity, structure, characters, retention, economy, language, accuracy, visuals, length.
- `must_fix`: the short list of changes without which the video should not ship, each a single imperative sentence.
- `retention_risks`: a list of `{minute, risk}`.
- `cut`: exact phrases or sentences to delete.
- `cliches`: exact clichés found.
- `unsupported_claims`: exact claims the dossier does not back.

Script to critique (JSON):

```json
$script
```
