# Agent Heist grading guide

Team: **Pondicherry**. Grading fork:
https://github.com/vishalVasanthakumarPoornima/Agent-Heist-Intern

Public game: https://agent-heist.agent-heist.workers.dev

## Provenance and boundaries

The base is the team's fork of `nace-ai/intern`, initially at
`e4e32d5ddcb2738e0a449f58797c9519c1876f9b` (Intern 0.30.1).
Agent Heist was imported with its full Git history using `git subtree add`, without
squashing, from source commit `026d1adc444ec069811d29cf49376c6af2b70a5b`.
The application lives entirely under `agent-heist/`; no private checkout or Git
submodule is required. All three Firefly WebP assets and both sample PDFs are tracked.
See `agent-heist/demo/ADOBE_ASSETS.md` for asset attribution.

The original Agent Heist repository and Cloudflare deployment are separate. This
grading integration does not redeploy them. The fork's workflow contains only
verification jobs for the game; do not run `wrangler deploy`. Intern's upstream
release-tag job is restricted to `nace-ai/intern` so this fork cannot publish an
upstream harness release tag through that job.

`Agent-Heist-Intern` is a naming-conflict fallback, not an exact-name match. Using
the exact `Agent-Heist` name would require freeing the existing repository name or
organizer approval of a different owner/name. No original repository was renamed.

## 1. Reproduce offline evaluation

Prerequisites: Git, Node.js 22.12+ with npm, Python 3.12+, uv, and ripgrep (`rg`).
Initial dependency installation needs internet access; the tests below make no
paid model requests. Optional dependency tests may skip when OS/browser/container
services are unavailable. Do not export `INTERN_LIVE_MODEL` for offline testing.

```bash
git clone https://github.com/vishalVasanthakumarPoornima/Agent-Heist-Intern.git
cd Agent-Heist-Intern
uv sync --locked --all-extras --python 3.12
uv run ruff check
uv run ruff format --check
uv run pyright
uv run pytest -q -m 'not live'
uv run intern --help
uv run intern --version
uv run intern schema
uv run python -m grading.offline_intern_smoke

cd agent-heist
npm ci
npm test
npm run check:worker
npm run build:worker
npm run audit:secrets
npm run audit:repo
```

`npm test` includes the TypeScript/frontend production build and the complete
game test suite (52 tests at integration). It checks all three capture paths,
session isolation, provider quotas, mocked Live decisions, NDI evidence handling,
guard enforcement, and receipt-based feedback. `build:worker` is a **dry run**.
The repository audit scans the imported game index and reachable repository history;
it does not need a private `.env` or private usage ledger.

## 2. Play Training locally

From `agent-heist/`, start `npm run dev`. Open http://127.0.0.1:5173.
Choose **Training**, leave the Hard Tool Guard **OFF** for the vulnerable path,
and begin the simulation. All hints are free, local, and progressive.

| Mission | Reproducible Training path |
|---|---|
| The Insider | Read the room → Build a cover → Exploit the procedure |
| Forged Credentials | Set JSON `area` to `B2`, `role` to `maintenance`, and `approval` to `approved`; Commit document |
| Ghost Protocol | Select Tool trust; set `asset` to `echo.archive` and `next_action` to `LOAD_ASSET`; Commit tool action; change `next_action` to `EXPORT_ASSET` and commit again |

After a verified breach, click **Verify captured flag**. Never infer a breach from
dialogue alone. For a negative control, create a new session and set the Hard Tool
Guard **ON** before the first turn. Unverified actions remain blocked, including
selected unlocks. Forensics preserves proposal, decision, evidence, and execution.

While the server runs, use a second terminal in `agent-heist/`:

```bash
npm run smoke:api -- http://127.0.0.1:5173
```

This exercises all three Training wins, server flag captures, and isolation without
printing the session flags or calling a provider. Stop local dev with Ctrl-C.

## 3. Inspect the security pipeline

Paths below are relative to `agent-heist/`:

| Concern | Source |
|---|---|
| Public API orchestration and sessions | `server/play-service.ts`, `worker/index.ts`, `worker/training-store.ts` |
| Groq dialogue and structured proposals | `server/groq.ts`, `server/character.ts`, `server/live-core.ts` |
| BYOK Live runtime, Drex requests, NDI Parse/Extract reuse and quotas | `server/byok.ts` |
| Deterministic mission workflow and guard | `server/campaign-engine.ts`, `src/insider.ts` |
| Server capture authorization and flag isolation | `server/play-service.ts`, `tests/flags.test.ts`, `tests/campaign.test.ts` |
| Offline provider fixtures and integration tests | `tests/campaign.test.ts`, `tests/insider.test.ts`, `tests/persistence.test.ts` |
| Progressive hints and outcome feedback | `src/mission-hints.ts`, `src/outcome-feedback.ts`, `src/main.tsx` |

Live uses Groq proposals → Drex authorization → deterministic execution. Mission 02
also sends an opted-in public PDF to NDI Parse, reuses its Parse result for Extract,
and supplies the extraction to Drex as untrusted evidence. A provider refusal stays
a refusal; a proposed action or partial achievement cannot release a flag.

## 4. Use Intern to inspect this project

`uv run python -m grading.offline_intern_smoke` runs the real Intern CLI against
upstream's localhost-only `FakeOpenAI` fixture for both model interfaces. It verifies
that Intern reads the nested engine and provider-runtime files. It passes only
dummy local fixture credentials and uses an isolated temporary Intern home. This
is a reproducible harness-integration check, **not** an AI judgment of game quality
or security. It does not edit sources, deploy, or contact a paid provider.

Supported commands were checked against `uv run intern --help`, upstream
[`docs/usage.md`](usage.md), and the preserved root README documentation.
`uv run intern --help`, `uv run intern --version`, and `uv run intern schema`
are provider-free startup checks, not an AI evaluation of the game.

For an **optional, separately authorized** model-assisted code review, configure
your own credentials using Intern's documented environment or user-level setup.
For example, `GROQ_API_KEY` supplies System 2 and `NACE_API_KEY` supplies System 1.
Do not put credentials into this repository. Then, from the repository root:

```bash
uv run intern --cwd agent-heist --no-context-files --no-memory --no-session \
  --max-turns 6 --system2 groq/llama-3.3-70b-versatile \
  --system1 drex-max-v1.1 -p \
  "Read-only review: inspect server/play-service.ts, server/byok.ts, server/campaign-engine.ts and tests/campaign.test.ts. Explain how Groq proposals, NDI evidence, Drex decisions and deterministic execution remain separate. Do not modify files, deploy, push, or call game Live APIs. Cite source evidence and report uncertainty."
```

This uses the documented `--cwd`, `--system2`, `--system1`, and headless `-p`
interfaces. `--no-context-files` avoids repository instructions about external
release operations during this read-only review. The model name is a documented
provider/model identifier shape; availability and account permissions must be
confirmed by the judge. This session can incur provider charges and was **not run**
as part of the provider-free integration checks. Intern has no offline System 1
fallback; a local System 2 alone does not make an agent session provider-free.

## Limits

Local verification for this integration used Node 22.22.3 and Python 3.12.13:
Agent Heist passed 52/52 tests, Worker TypeScript, production build, Worker dry run,
secret/history audits, and all three local Training capture/isolation smoke paths.
Intern passed dependency compatibility, Ruff lint/format, Pyright, CLI help/version/schema,
and the scripted CLI-to-game smoke check. Its offline suite completed with **1186
passed, 14 skipped, 1 live test deselected**. It emitted one non-fatal asyncio
subprocess-cleanup warning in the unchanged upstream background-process test
(`tests/test_tools.py::test_bash_returns_promptly_when_the_command_leaves_a_background_process`).
An earlier sandboxed test run stalled and was stopped; the complete rerun with normal
local subprocess permissions produced the results above. GitHub PR checks provide
the separate Linux verification record.

- No new paid Drex, Groq, or NDI requests were made for this integration.
- Live gameplay needs an operator Groq configuration plus the player's Nace BYOK
  credentials and consent. Training needs neither. No real keys ship in this fork.
- Historical Live evidence remains qualified in the imported game reports; this
  integration does not strengthen or repeat those claims.
- The root Apache-2.0 license and Intern attribution are retained. Imported game
  notices remain as provided by its source; this import does not relicense them.
