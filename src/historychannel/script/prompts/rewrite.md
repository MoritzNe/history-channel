# Pass 4b: Rewrite after critique (round $round)

The editor scored the script $score/100. Rewrite it to fix every `must_fix` item and as many of the other issues as you can, without introducing new unsupported claims. Keep what works. Stay at about $words narration words in total (currently $actual_words). Keep the `slug` as `$slug` and the language as `$language`.

Then call the `submit_script` tool once with the complete revised script (all scenes, not a diff).

Editor's critique (JSON):

```json
$critique
```

Current script (JSON):

```json
$script
```
