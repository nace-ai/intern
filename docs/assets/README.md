# README assets

The README's images. Each but the mascot and the wordmark comes in a light and a dark version, picked by `<picture>`.

| File | What it shows |
|---|---|
| `tui-light.png`, `tui-dark.png` | The terminal UI at the end of a session: a failing test, three turns System 1 calls stuck and the nudge after them, a `sudo` call the guard's fixed rules refuse, the fix and the passing test |
| `loop-light.svg`, `loop-dark.svg` | One run of four turns, animated in CSS: calls through the guard (one refused), the journal, the session cut, System 1's verdicts and three strikes to a nudge, and the turns folding into the State at compaction |
| `code-search-light.gif`, `code-search-dark.gif` | `decision_code` searching this repository's own source for where a run that goes in circles is detected: the search tree growing as System 1 scores folders, files and definitions, then the result |
| `files-search-light.gif`, `files-search-dark.gif` | `decision_files` searching a folder of audit papers for "Has the client ever been the victim of a scam or theft, and what did it cost?": the walk and the BM25 lane filling, System 1's judgements, the hop through the seeds' terms, then the result |
| `compaction-light.gif`, `compaction-dark.gif` | `/compact` over a session that read twenty files of a made-up date library: System 2 writing its summary into the compaction's card, System 1 judging the reads being cut and keeping the three your intern is still working on, then a click opening the summary |
| `wordmark.svg` | The README's title, one for both looks: the terminal UI's block letters (`widgets.ART`), a violet a row (`widgets.SHADES`) and the shadow a cell to their right (`widgets.SHADOW`), as rectangles with no text. Draw it again when those change |
| `mascot.svg` | Intern's mascot, one for both looks: the Nace agent cube in violet with round glasses, its eyes and a smile on the front face, exported from the Nace agent mascot renderer with a mouth. CSS in the SVG makes it breathe, glance left and right (the eyes and mouth move together behind still glasses) and blink twice on an 8 s loop; it stays still under reduced motion. Draw the terminal UI's `tui/mascot.png` and `widgets.MASCOT` again when it changes |
