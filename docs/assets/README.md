# README assets

The README's images. Each comes in a light and a dark version, picked by `<picture>`.
The diagrams are hand-drawn SVG animated in CSS, in the look of nace.ai's figures: its sand neutrals, hairline
rules, headings in Newsreader, text in Inter and labels in Geist Mono, with one accent for what System 1 does. Each
carries the glyphs it uses of those three fonts (subsets, as data in its `<style>`; their licences are in `fonts/`),
so it reads the same wherever it is drawn, and falls back to system fonts; under reduced motion each stops on its most
telling frame. Every number on them comes from the run or measurement their row names.

| File | What it shows |
|---|---|
| `tui-light.png`, `tui-dark.png` | The terminal UI at the end of a session with code search on: a failing test, re-reads System 1 calls stuck at its look and the next turn, the nudge, a `decision_code` search that ranks the right file first (folded to its summary line), the fix and the passing test. System 2's turns are scripted; System 1 is live |
| `how-it-works-light.svg`, `how-it-works-dark.svg` | One run of nine turns, animated in CSS: calls going straight to the tools past the guard (dashed, off by default), a `decision_code` search System 1 walks, the journal, the session cut, System 1's looks at turns 4, 8 and 9 and the nudge, and the turns folding into the State and the kept reads at compaction |
| `supervise-light.svg`, `supervise-dark.svg` | Thirteen turns of one run with System 1's looks at turns 4, 8, 9 and 13 (`supervise.EVERY`, `STRIKES`), each look's mode bars (drawn for illustration) against the 0.7 stuck threshold (`STUCK_THRESHOLD`), and the nudge. Draw it again when the rule changes |
| `code-search-diagram-light.svg`, `code-search-diagram-dark.svg` | The mechanism of the `code-search` GIFs' search, with the scores of an earlier run of it (the GIFs' run scored a little differently): grep's two hits in screen text, System 1's walk from `src/intern` to files and definitions with the cut, the compare, and the ranked result |
| `files-search-diagram-light.svg`, `files-search-diagram-dark.svg` | The mechanism of the `files-search` GIFs' search, with the scores of an earlier run over the papers it was first taken on: the walk and BM25 lanes, System 1 judging both, the hop through the best passages' terms, the result, and what `decision_files` reads |
| `code-search-light.gif`, `code-search-dark.gif` | `decision_code` searching this repository's own source for where a run that goes in circles is detected: the search tree growing as System 1 scores folders, files and definitions, then the result. System 2's turns are scripted; System 1 is live |
| `files-search-light.gif`, `files-search-dark.gif` | `decision_files` searching a folder of seven audit papers, made up for the recording, for "Has the client ever been the victim of a scam or theft, and what did it cost?": the walk and the BM25 lane filling, System 1's judgements, the hop through the seeds' terms, then the result. System 2's turns are scripted; System 1 is live |
| `compaction-light.gif`, `compaction-dark.gif` | `/compact` over a session that read twenty files of a made-up date library: System 2 writing its State into the compaction's card, System 1 judging the reads being cut and keeping the three your intern is still working on, then a click opening the State. System 2's turns are scripted; System 1 is live |
| `brand/intern-idle-light.svg`, `brand/intern-idle-dark.svg` | Intern's mark at the top of the README: the Nace cube with two eyes in the window on its top face, in one colour (ink on light, white on dark), looking around and blinking in a loop. Exported from the Nace brand playground's Intern kit, as the terminal UI's sprites (`tui/sprites/`) are; export both again when the character changes |
| `brand/intern-wordmark-black.svg`, `brand/intern-wordmark-white.svg` | The README's title: "Intern" in Inter, as outlines with no text |
| `brand/intern-banner-light.svg`, `brand/intern-banner-dark.svg` | The README's banner, 1280 by 640, which is also the size of a social card: the mark's outline nested in dashes, a family on each side of the headline for the two systems, each grown about the point nearest the middle so none crosses another, one outline in the accent; the headline in Newsreader. Its text is outlines, so it needs no font |
| `brand/intern-mark-*.svg`, `brand/intern-lockup-*.svg`, `brand/intern-app-icon.svg` | The still mark, the mark beside the wordmark, and the app icon, for use outside the README |
