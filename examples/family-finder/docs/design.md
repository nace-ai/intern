# Census bench: Nace vs Claude Code on 1950 census pages (design)

Written 2026-11-14. Approved in conversation the same day.

## Intent

Start building our own genealogy dataset from public records instead of relying on FamilySearch or Ancestry. Transcription is the expensive part, so before choosing a tool, measure two of them on the same pages: Nace Perception (Extract) and Claude through Claude Code on the owner's subscription (`claude -p`, never the API key).

## Design (as agreed)

- **Overlap set:** 20 population-schedule pages (Form P1) from the 1950 census, Kent County, Delaware, read by both systems. Pages chosen because each has at least 20 rows of volunteer name corrections on NARA's site. Later, pages outside the overlap can go to whichever system wins.
- **Independent validation:** agreement between the two systems is not proof of correctness, so:
  - Names are checked against two outside readings from NARA's site API: the machine-made name index and the volunteer corrections (per row). Volunteer corrections are the reference.
  - Other fields (relationship, sex, age, marital status, birthplace, occupation) have no outside reference: every row where the systems disagree is checked by hand against the scan, plus a random sample of about 50 rows where they agree.
- **Measured per system:** accuracy (as above), seconds per page, and cost. Claude's speed is reported both as total wall time per page (includes Claude Code startup) and as model time (`duration_api_ms`). Claude's cost is tokens and the API-equivalent dollar figure from the CLI; Nace's is the actual charge.

## Constraints

- Data is public domain (NARA). No Relavy member data is used.
- `claude -p` runs without `ANTHROPIC_API_KEY` in its environment, so it bills the subscription.
- Nace key lives in `.env.local` (git-ignored). The event credit is $35; this set should cost well under $1.
- Both systems get the same 20 original JPGs (about 4000x4800 px). Nace reads them from NARA's public URLs; Claude Code reads the local copies with its own Read tool, which resizes images as Claude Code normally does. That is part of what is being compared.

- Results stay local in `results/` (git-ignored, not committed), one JSON file per page per system with raw output, timing and cost. They may be loaded into the Relavy Supabase project later, in a schema kept apart from member data, via a Drizzle migration in the Relavy repo (owner's decision, 2026-11-14).

## Builder's calls

- Fields per row: line number, full name (surname filled in where the enumerator used a ditto mark), relationship, race, sex, age, marital status, birthplace, occupation.
- Claude models: Sonnet 5.5 first; Haiku 5.5 if time allows.
- Nace: Extract with `parse_mode: high`, inline schema, URL source.
