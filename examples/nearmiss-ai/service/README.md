# Builder B backend: service, agents, evidence

FastAPI service + four Drex-driven agents + NDI-cited evidence, implementing
`contracts.md` v1.0. No code from `data_pipeline/`, `dashboard/` or `evaluation/` is used.

## Setup (Windows / Git Bash paths shown; Python 3.10+)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r service/requirements.txt
cp .env.example .env        # then put your nace_sk_ key in .env (git-ignored)
```

## Plan steps and commands

| Plan step | Command | What it proves |
|---|---|---|
| B1 Drex | `.venv/Scripts/python -m service.check_nace` | Key works, lists models, one live `choice` decision with real probabilities |
| B1 NDI | `.venv/Scripts/python -m evidence.ingest --parse-only --only faa-ac-120-74b` | One real NDI Parse job on a public FAA PDF |
| B2 | `.venv/Scripts/python -m evidence.ingest` | Parse → Extract → Ground once per PDF; writes `evidence/cache/passages.json` |
| B3/B4 | `.venv/Scripts/python -m service.run_case tests/fixtures/case_pattern_mock.json` | Live 4-agent trace on a fixture, printed |
| B4 API | `.venv/Scripts/python -m uvicorn service.app:app --port 8000` | `GET /health`, `GET /evidence/status`, `POST /analyze` |
| Tests | `.venv/Scripts/python -m pytest tests/backend -q` | Offline: contract, policy, failure paths, SDK mapping |

Every `/analyze` run is also saved to `service/runs/` (git-ignored) so real successful
and challenged runs can be handed to Builder A as labeled cached demos.

## Evidence (NDI)

Sources are in `evidence/sources.json`: FAA AC 120-74B and AC 91-73B (taxi operations,
clearances/readback, runway incursion prevention). Both URLs were verified to serve PDFs.

A passage reaches an `EvidenceRef` only if NDI produced both its text and its location:
- `grounded`: NDI Ground found the extracted sentence; text = Ground `matched_text`, page from its location.
- `parsed`: Ground missed it but Extract returned a citation with `source_text`.
- anything else is rejected and listed under `rejected` in the cache.

## Agents and the decision flow

Round 1: **Pattern** → **Evidence** → **Investigation** → **Coordinator**, one Drex call each.
Each agent's Drex `state` contains the earlier agents' structured findings.
Exact question wording is in `agents/questions.py`. Rules that interpret Drex outputs are
in `agents/policy.py`, kept separate from inference:

- **P1:** an Evidence "supports" with no located citation is recorded as `insufficient`.
- **P2:** one targeted second round runs if the Coordinator selects `reevaluate`, or if Evidence `conflicts` with the claim.
- **P3:** if the case is still unresolved or conflicting after the capped second round, the final action is `specialist_review`.
- **P4:** `investigate_cluster` is not allowed when no historical candidate was judged a match.

Any Drex failure stops the run, and the response is `incomplete`. It keeps the real messages
produced so far and adds a typed error. No message is ever synthesized.

## Notes for Builder A (contract-compatible extensions)

All are optional extra fields, which contracts.md allows readers to ignore:

- `AgentMessage.auxiliary_decisions`: secondary Drex questions from the same call, with real scores
  (candidate-match and passage-relevance yes-probabilities, `review_priority`, `amended_next_action`, `conflict_focus`).
- `AgentMessage.drex_request_id`, `AgentMessage.drex_model`: provenance for each decision.
- `InvestigationResult.policy_notes`: plain-language list of policy rules that changed the outcome.
- `provider_model_details` also has `requested_model`, `evidence_documents` and `rounds_run`.

Error codes: `provider_not_configured`, `provider_auth_failed`, `provider_insufficient_credit`,
`provider_rate_limited`, `provider_timeout`, `provider_unavailable`, `provider_rejected_request`,
`invalid_provider_output`, `provider_error`, `internal_error`, and `evidence_unavailable`. The last one
also appears on `complete` runs: the run finished, but there were no NDI passages to cite.

A run makes 4 sequential Drex calls, or 8 with a second round. Set the dashboard timeout to cover that.

## Developer tooling (plan §8; optional, for exploring Nace from Claude Code)

From the Nace MCP docs:

```bash
uv tool install nace-cli        # or: pipx install nace-cli
nace login                      # paste the key; not echoed
claude mcp add nace -- uvx nace-mcp
nace skill install --agent claude-code   # drex-search + drex-workspace skills
```

At runtime the app calls only the documented `nace-sdk` API, not the MCP server.
