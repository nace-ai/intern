# census-bench

Can we build our own genealogy dataset from public records, instead of relying on FamilySearch or Ancestry? This repo measures how well different AI readers transcribe historical records, and how well different judges decide whether two records are the same person.

Built on 2026-11-14 for Relavy (a private family network) during the Nace.AI Drexathon, using Nace (Perception/NDI and Drex), DeepInfra, and Claude Code on a subscription.

All source records are public domain: NARA's 1950 census images and Internet Archive scans of Hill's Wilmington, NC city directories (rights statement "No Copyright – United States").

## What we measured

### 1. Handwritten records: 1950 census (20 pages, Kent County, Delaware)

Scored against NARA's volunteer name corrections (531 lines). The volunteer corrections themselves have errors, so treat these as approximate.

| Reader | First names right | Surnames right | Time per page | Cost, 20 pages |
|---|---|---|---|---|
| Claude Sonnet 5.5 (Claude Code) | 91% | 89% | 20 s | $4.29 at API list price* |
| Claude Haiku 5.5 (Claude Code) | 84% | 75% | 36 s | $0.26* |
| Nemotron 3.5 Super VL (DeepInfra) | 52% | 63% | 36 s | $0.19 |
| Nace Extract, whole page | 23% | 46% | 73 s | $0.31 |
| Nace Extract, page cut into 3 strips (3 pages) | 58% | 55% | 62 s | $0.047/page |
| NARA machine index (baseline) | 39% | 62% | n/a | free |

\* Run through Claude Code on a subscription; the figure is API list price including Claude Code's own setup tokens.

Nace returned no people on 11 of 20 whole pages (twice, billed as succeeded): its parse step read the printed form but not the handwriting. Cutting the table into strips fixed the empty results but not the accuracy.

### 2. Printed records: 1928 city directory (20 pages, ~780 entries)

No hand-made answer key; scored against the Internet Archive's own OCR of the same page, and against each other.

| Reader | Entries | Name in IA OCR | Address in IA OCR | Cost, 20 pages |
|---|---|---|---|---|
| Claude Sonnet 5.5 | 785 | 96% | 93% | $4.08* |
| Claude Haiku 5.5 | 783 | 96% | 93% | $0.21* |
| Nace Extract | 775 | 96% | 93% | $0.31 |
| Nemotron 3.5 Super VL (16 pages) | 626 | 96% | 94% | $0.10 |

On print, every reader is about equally good, so the cheapest one wins.

### 3. Record linking: same person in 1928 and 1930?

Candidate pairs: same surname, similar given names. Judges answer "same person?" with a probability. With no human labels yet, the check set is the 83 of 100 sampled pairs where a jury of four models (DeepSeek V4 Pro, Kimi K3.1 Pro, Claude Sonnet 5.5, Gemini 3.1 Pro) all agree.

| Judge | Right on the 83 check pairs | Time per pair | Cost |
|---|---|---|---|
| Drex Max v1.1 (Nace) | 81/81 at 0.5 | 0.66 s | $0.03 per 1M input tokens |
| Drex v2.0 (Nace) | 63/83 at 0.5, 78/83 at 0.73 | 0.27 s | $0.03 per 1M input tokens |
| DeepSeek V4 Flash | 83/83 | 2.9 s | ~$0.00009 per pair |
| Claude Sonnet 5.5 (via DeepInfra) | 83/83 (jury member) | 1.6 s | |
| Gemini 3.1 Pro | 83/83 (jury member) | 6.6 s | |

Drex costs about $2–3 per million pairs. Drex v2.0's probabilities run high, so it needs a cut-off near 0.75; Drex Max v1.1 works at 0.5. Claude Sonnet and Gemini are part of the jury, so their 83/83 is by construction. Confirm all of this with human labels.

### 4. Hybrid matcher: rules, then Drex, then people

`bench/hybrid_link.py` decides every candidate pair in three stages:

1. **Rules** (free, deterministic). Given names are expanded with a table of period directory abbreviations (jno → john, wm → william, ...) and compared by sound code (soundex); occupations are expanded with an abbreviation table (lab → laborer, slsmn → salesman, ...). Different "widow of", different first names or different spouse with nothing else in common → no link. Same first name plus same spouse or same address, with no Jr/Sr disagreement → link. Same name and occupation → link.
2. **Drex Max v1.1** for the pairs the rules cannot settle: p ≥ 0.75 link, p ≤ 0.25 no link. Pairs where only the name matches need p ≥ 0.9, because Drex over-links those.
3. **Human review** for everything in between.

Finally a one-to-one limit: each entry links to at most one entry in the other edition, and the strongest candidate wins.

On the 83 check pairs: rules decided 71 and got 71 right, Drex decided 9 and got 9 right, and 3 went to review.

### 5. Self-improving loop

`bench/mine_rules.py` reads Drex Max's decisions on the pairs the rules sent it and proposes new rules: given-name variants and occupation synonyms that Drex confidently links, and reasons that Drex almost always rejects. It changes nothing itself; a person checks each proposal before it goes into the tables in `hybrid_link.py`. The "same name and occupation" rule came from this loop (Drex Max linked 680 of 697 such pairs and rejected none). Each round moves more pairs from Drex to free rules.

## Takeaways

- Handwriting: a strong vision model (Claude Sonnet) is needed; cheaper readers lose too many names.
- Print: Nace, Haiku and open vision models are all good enough, and cheap.
- City directories (printed, every year or two) are the easiest source to scale; they also carry address and occupation, which link people across years.
- Drex fits the linking decision: fast and cheap enough to judge every candidate pair, with a probability that can route doubtful links to people for review.
- Rules first, Drex for the uncertain middle, people for the rest: most pairs cost nothing, and Drex's decisions teach the rules.

## Layout

- `bench/schema.py`, `bench/directory_schema.py`: the fields every reader fills.
- `bench/run_claude.py`: Claude Code readers (`claude -p`, strips `ANTHROPIC_API_KEY` so the subscription is billed).
- `bench/run_nace*.py`: Nace Extract readers (URL, upload, crops, full directories).
- `bench/run_deepinfra_*.py`: DeepInfra vision readers.
- `bench/compare.py`, `bench/compare_directory.py`: scoring.
- `bench/link_directories.py`, `bench/link_editions.py`, `bench/judge_sample.py`: candidate pairs and judges (Drex, DeepInfra models).
- `bench/hybrid_link.py`: the hybrid matcher (rules → Drex Max → review). `bench/mine_rules.py`: proposes new rules from Drex's decisions.
- `data/directory/manifest.json`: the directory benchmark pages. `data/reference/kent-eds.json`: the Kent County enumeration districts.
- `bench/build_census_manifest.py`: rebuilds `data/reference/manifest.json` (the 20 census pages, with NARA's name readings) from NARA's public search API. That file holds real names, so it is not in the repo; run this script first.
- Images are downloaded by the scripts and not stored in the repo.
- `docs/design.md`: the original design.

## Running it

Put keys in `.env.local` (never committed):

```
NACE_API_KEY=...
DEEPINFRA_API_KEY=...
```

Then, for example:

```bash
python3 bench/build_census_manifest.py
python3 bench/run_nace.py --jobs 4
python3 bench/run_claude.py --model sonnet
python3 bench/compare.py results/claude-sonnet results/nace-high
```

Results are written to `results/`, which is git-ignored.

## License and data

Code: MIT, see [LICENSE](LICENSE).

The source records (NARA's 1950 census, Internet Archive scans of Hill's Wilmington, NC directories) are public domain. This repository contains no transcriptions and no names of real people: the scripts fetch the records and write their output to the git-ignored `results/` folder.
