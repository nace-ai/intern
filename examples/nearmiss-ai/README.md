# NearMiss AI — Drexathon entry (Swarm Intelligence)

Cooperative aviation incident investigation over real NASA ASRS narratives: four Drex agents (Pattern, Evidence, Investigation, Coordinator) exchange structured findings, NDI-grounded FAA guidance, and a capped second round when the Coordinator selects `reevaluate` or evidence conflicts.

**Canonical repo (development):** https://github.com/MrunalKotkar/nearmiss-ai-drex

This folder is a Drexathon submission copy for review next to [Intern](https://github.com/nace-ai/intern). It does **not** modify Intern itself and is not required to merge into Intern.

## What it does

1. Browse **503** de-identified ASRS reports (June 2024 export) via a local Next.js dashboard.
2. Select an incident; the data layer returns a bounded `CaseContext` (narrative + 3–5 text-matched neighbors).
3. **Run collaborative analysis** — Pattern → Evidence → Investigation → Coordinator; optional **second round** (up to 8 Drex calls).
4. Inspect decision scores, uncertainty, policy overrides, and **page-grounded** FAA citations (AC 120-74B / AC 91-73B).

Offline: the dashboard still browses cases when the analysis service is down. Cached prior live runs are labeled.

## How it uses Nace products

| Product | Role |
|--------|------|
| **Drex** | Every agent discretionary step is a bounded `choice` question; probabilities recorded in `AgentMessage`. |
| **NDI** | One-time Parse → Extract → Ground on two public FAA PDFs; cache reused per case. |
| **Intern** | *Not integrated in the MVP* (hackathon scope was multi-agent Drex + NDI first). |

Shared contracts: `contracts.md` (v1.0). Builder A: `data_pipeline/`, `dashboard/web/`. Builder B: `service/`, `agents/`, `evidence/`.

## Evidence (live swarm)

Captured 2026-11-14; details in `evaluation/live_runs.md`.

| Case | Behavior |
|------|----------|
| **2134024** | 4 agents; Evidence `supports` + 1 FAA citation; `investigate_cluster`. |
| **2132897** | 8 agents / 2 rounds; Coordinator `reevaluate`; pattern `runway_surface` → `communication_coordination`; `specialist_review`. |

Across **53** logged local runs: **52** complete, **38** with ≥1 citation; **8** triggered a second round; **5** revised pattern family. **0** runs with Evidence `conflicts` in round 1 (honest limitation — demo uses `reevaluate` / `unresolved` for swarm proof).

Saved demos: `dashboard/web/public/demos/2134024.json`, `2132897.json`.

## Optional benchmark (Apex Predator / narrative-only)

Separate from the swarm MVP: `evaluation/benchmark/results/main/` — Drex `drex-max-v1.1` vs OpenAI `gpt-6-luna` on 1,000 class-balanced cases × 2 tasks (communication breakdown, primary problem). **No NDI, no multi-agent trace.**

See `report.md` for measured accuracy, latency, cost, and limitations. **Pitch stays Swarm Intelligence** — OpenAI leads slightly on comm-breakdown accuracy; primary-problem task is a statistical tie.

## Quickstart (from this folder)

Python 3.11+ and Node 20+; Nace key in `.env` (see `.env.example`).

```sh
python -m venv .venv && .venv/bin/pip install -r service/requirements.txt -r requirements-a.txt
# Import ASRS CSV per data/manifest.md → data/asrs.duckdb (not committed)
.venv/bin/python -m evidence.ingest   # once, if cache missing
.venv/bin/uvicorn data_pipeline.api:app --port 8001 &
.venv/bin/uvicorn service.app:app --port 8000 &
cd dashboard/web && npm ci && npm run dev
```

Open http://127.0.0.1:3000

Tests: `.venv/bin/python -m unittest tests/test_builder_a.py -v` and `.venv/bin/python -m pytest tests/backend -q` (fixtures; no live Nace for most tests).

## Where to look

- `agents/orchestrator.py` — round 1 / optional round 2, incomplete handling.
- `agents/policy.py` — P1–P4 (citation integrity, second round, specialist_review, cluster guard).
- `evidence/ingest.py` — NDI pipeline and cache rules.
- `dashboard/web/src/app/page.tsx` — investigation UI and saved runs.
- `evaluation/DEMO_PATH_PLAN.md` — frozen demo path for judges.

## Known gaps vs Intern `CONTRIBUTING.md`

Hackathon submission, not an Intern feature PR:

- Does not call `ctx.decisions` or embed in Intern's agent loop.
- Separate `pyproject`/Node app from Intern's `uv` workspace.
- Large example tree under `examples/nearmiss-ai/` (whole project snapshot).

Track: **Swarm Intelligence** (primary). Optional benchmark folder included for transparency, not as a performance win claim.