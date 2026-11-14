# Build status

Updated 2026-11-14 14:15 America/Los_Angeles (WSL continuation). Deadline 15:00 America/Los_Angeles.

## Done

- Offline app, oracle, datasets, ten PDFs, mock decision pilot, mock 100-case decision run, and mock 10-packet document run.
- Live development pilot `2026-11-14T201845+0000-70aac090`: Drex 5/5, OpenRouter 4/5. Charges $0.00012312 and $0.0096500. Freeze written locally.
- **Held-out 100-case decision test `2026-11-14T215926+0000-36032342` (run in WSL `.venv-wsl`): 100/100 attempted per provider, 0 failures, identical shared input hash on all 100 pairs.** Drex 89/100, false clearance 8/67, P50/P95 285/447 ms, $0.00249756. OpenRouter 95/100, false clearance 3/67, P50/P95 1083/1361 ms, $0.1967275. Median OpenRouter/Drex latency 3.78 on 100 matched pairs. No tuning after the freeze.
- Live NDI packet `TST-056` (`2026-11-14T201933+0000-3b155911`), document cost $0.1200, unchanged; completed paid document work was reused, not repeated.
- Dashboard live inspection implemented and verified: `APP_MODE=live` + keys makes the Inspect button call both providers for real (verified with TST-056: Drex REVIEW 208 ms $0.00002517, OpenRouter REVIEW 1529 ms $0.001985, `drex_price_confirmed: true`). Default stays mock; live with a missing key is refused, never silently mocked. Records carry `mode` and `measurements`.
- WSL path verified end to end: `uv venv .venv-wsl`, `uv pip check` clean (37 packages), 36 pytest passes, dashboard on 8000 (mock) and 8001 (live).
- Tests: 36 passed in `.venv-wsl` (34 before this session; +2 for inspect-mode gating and live/mock run lookup).
- Deck and docs updated with the measured full-run numbers: `submission/presentation.pdf`, `submission/slides.md`, `README.md`, `docs/BENCHMARK.md`, `submission/demo_script.md`. Contribution log: `docs/INTERN_USAGE.md`.
- `.gitignore` now ignores `.venv-wsl/` in addition to `.venv/` and `.env`.

## Not done

- `benchmark_ndi.py --limit 10` was not run (the one authorized live packet stands).
- No Adobe Firefly asset was created. The packets use geometric fallbacks.
- Source repository: https://github.com/bashaboinamanojkumar/cosmic-customs. Public dashboard: https://cosmic-customs.vercel.app. The public site stays in mock mode.

## Budgets after the measured calls

- Drex decisions (pilot + held-out 100 + one live inspection): $0.00012312 + $0.00249756 + $0.00002517 = $0.00264585 of the $1.00 decision budget.
- OpenRouter decisions (pilot + held-out 100 + same-evidence NDI call + one live inspection): $0.0096500 + $0.1967275 + $0.00195 + $0.001985 = $0.2103125 of the $3.00 budget.
- NDI document jobs: $0.1200 of the $2.00 budget.
- Intern System 2 cost $0.080404 is outside those benchmark totals.
