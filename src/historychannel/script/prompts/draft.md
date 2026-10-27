# Pass 3: Draft

Topic: **$topic**
Target length: about $minutes minutes, about $words spoken words in total. Narration language: $language. Write all narration, titles, captions, card and map text, and the description in this language. Commons search queries may be in whatever language finds the best images.

Write the full script from the research dossier and the outline above, following the house style and visual vocabulary in the system prompt. Then call the `submit_script` tool once with the complete script.

Requirements for the `submit_script` input:

- `slug`: `$slug`
- `title`: the starred title from the outline (at most 100 characters).
- `language`: `$language`
- `description`: a YouTube description of 2 to 4 short paragraphs: the question the video answers and what the viewer will understand afterwards. No source list or chapter markers; they are added automatically.
- `tags`: 8 to 15 relevant search tags.
- `scenes`: in story order, with ids `s01`, `s02`, ... Each scene has:
  - `narration`: exactly what the narrator says, written for the ear, usually 40 to 100 words. The total across all scenes should be about $words words.
  - `visuals`: 1 to 5 shots in the order the narration reaches them, each with a `weight` equal to roughly its share of the scene's words. Picture change every 6 to 12 seconds; maps may hold 10 to 18.
  - `mood`: one of tense, somber, hopeful, triumphant, neutral.
  - `pause_after`: 0.4 to 1.5 seconds, longer at the end of an act.
  - `sources`: the dossier URLs that support this scene's factual claims.
- `sources`: every URL the script relies on.
- Leave `voice` unset.

Visual plan for the whole video:

- **At least 55% of shots are maps**, about 25% motion graphics, at most 20% archival.
- Scene 1: an animated map hook, then the `title_card`.
- The same `timeline` (5 to 7 events) at each act transition, with `highlight` moving forward.
- A `person` card the first time each key figure matters (2 to 6 people), using a portrait file from the dossier if one is listed there.
- A `stat` card for each key number (3 to 6 in total), each verified in the dossier, with hedges in `subtext`.
- A `compare` card for each real either/or (rival programmes, two solutions), 1 to 3 in total.
- Maps use `camera_to` to move between scales, staggered markers and stamps for sequences of events, arrows for every movement the narration describes (armies, news, flight, emigration), and regions for memberships, borders and options. `historical` names must exist in the chosen basemap. Otherwise use `polygon` and label it "(approx.)".
- Time every `at` to the moment its word is spoken within that shot (see the timing rule). All values must be between 0.05 and 0.95.

Before you call the tool, check that:

- The central question is posed within about 20 seconds.
- The structure is signposted, and the answer is stated explicitly near the end.
- Every number on a stat card or map label matches the narration and the dossier.
- Disputed figures are hedged.
- Every quote is verbatim from the dossier, with attribution.
- The geography is shown, not just named.
