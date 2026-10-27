# Pass 3: Draft

Topic: **$topic**
Target length: about $minutes minutes, about $words spoken words in total. Narration language: $language. Write all narration, titles, captions, card text and the description in this language; Commons search queries may be in whatever language finds the best images.

Write the full script from the research dossier and the outline above, following the house style in the system prompt. Then call the `submit_script` tool once with the complete script.

Requirements for the `submit_script` input:

- `slug`: `$slug`
- `title`: the starred title from the outline (at most 100 characters).
- `language`: `$language`
- `description`: a YouTube description of 2 to 4 short paragraphs: what the video is about (without spoiling the twist) and why it matters. Do not include a source list or chapter markers; they are added automatically.
- `tags`: 8 to 15 relevant search tags.
- `scenes`: in story order, with ids `s01`, `s02`, ... Each scene has:
  - `narration`: exactly what the narrator says, written for the ear, 20 to 60 seconds long (about 50 to 150 words). The total across all scenes should be about $words words.
  - `visuals`: 2 to 4 visuals as described in the system prompt, ordered to match the narration. If geography matters, use at least two maps across the video. Use a title card at the transition from cold open to title, or at chapter breaks. Use quote cards only for quotes verified in the dossier.
  - `mood`: one of tense, somber, hopeful, triumphant, neutral.
  - `pause_after`: 0.4 to 1.5 seconds; longer after a reveal or the end of an act.
  - `sources`: the dossier URLs that support this scene's factual claims.
- `sources`: every URL the script relies on.
- Leave `voice` unset.

Before you call the tool, check: does the first scene hook in under 30 seconds and pose the central question? Does every act end with an open loop? Are disputed figures hedged? Is every quote verbatim from the dossier, with attribution?
