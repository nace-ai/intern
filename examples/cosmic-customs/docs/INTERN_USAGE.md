# Intern usage

## WSL continuation session, 2026-11-14 (code contribution and verification)

A continuation session ran in Ubuntu WSL with Python 3.12.3 and the gitignored `.venv-wsl` (built with `uv` 0.13.8; the Windows `.venv` was left intact). `.venv-wsl/` was added to `.gitignore`; `.env` (the only file holding keys) was already ignored. Sanitized evidence is below; no key, token, or full request header is recorded.

Code contributed by that session:

- `app.py`: live inspection mode. `inspect_mode()` is live only when `APP_MODE=live` and both keys are present; `/api/inspect` then reuses the existing `DrexClient` and `OpenRouterClient` adapters (one call each, in parallel), re-checks the published Drex rate first and keeps the Drex cost unknown rather than guessed if the page no longer confirms it. With `APP_MODE=live` and a missing key the request is refused with a visible error — no mock fallback. Records are stored under `results/<mode>/inspections/` with `mode` and `measurements` (`simulated` or `measured`); `/api/runs/{run_id}` now finds both live and mock records; `/api/health` reports `inspect_mode`.
- `static/app.js`, `static/index.html`: the button is labeled MOCK or LIVE from `/api/health`, each verdict panel states whether the inspection was simulated or measured, and polling now allows up to 150 s for live calls.
- `tests/test_app.py`: two new tests — live mode without keys refuses instead of falling back to the mock, and the run lookup finds records in both the live and mock inspection directories.
- `benchmark_decisions.py`: console output no longer describes the completed full run as an unstarted pilot.
- `make_presentation.py`: slide 4 now reports the development pilot and the completed held-out test side by side from the saved run files; slide 5 describes the labeled mock/live button.
- `README.md`, `docs/BENCHMARK.md`, `BUILD_STATUS.md`, `submission/slides.md`: updated with the measured full-run figures and tested WSL commands.

Verification evidence, sanitized:

- `VIRTUAL_ENV=.venv-wsl uv pip check` → "Checked 37 packages … All installed packages are compatible".
- `.venv-wsl/bin/python -m pytest` → 36 passed (before the contribution this was 34).
- `.venv-wsl/bin/python generate_cases.py --verify --sample 0` → "verified 20 dev and 100 test cases" (oracle labels, balance 33/33/34, canonical hashes against `data/split_manifest.json`).
- Mock dashboard on 127.0.0.1:8000 (default `APP_MODE`): `/api/health` returned `inspect_mode: mock`; a mock inspection of TST-056 completed with `mode: mock`, `measurements: simulated`, no API call.
- Live dashboard on 127.0.0.1:8001 with `APP_MODE=live`: inspection `inspect-fa017041f4` on TST-056 completed with `mode: live`, `measurements: measured`, `drex_price_confirmed: true`. Drex `drex-max-v1.1` returned REVIEW in 208 ms at $0.00002517 (839 input tokens); OpenRouter `openai/gpt-5.4` returned REVIEW in 1529 ms at $0.001985. Both records share one `input_hash`, so both models saw identical evidence.
- Held-out decision test: `.venv-wsl/bin/python benchmark_decisions.py --full 100` → run `2026-11-14T215926+0000-36032342`, wall time about 2 minutes, 100/100 cases attempted per provider, 0 failures. Drex 89/100 correct, 8/67 false clearance, P50/P95 285/447 ms, $0.00249756. OpenRouter 95/100 correct, 3/67 false clearance, P50/P95 1083/1361 ms, $0.1967275. Median OpenRouter/Drex latency 3.78 on 100 matched pairs. Full breakdown in `docs/BENCHMARK.md`.
- No live API call in this session returned an error; if one had, the sanitized error text would be recorded here, and a failed live request was never replaced by a mock.

The earlier Intern configuration compliance notes below this section are unchanged.

The Drexathon quick start has not completed. On 2026-11-14 the official `uv` install of `intern-agent` 0.30.1, commit `e4e32d5ddcb2738e0a449f58797c9519c1876f9b`, was launched with System 1 `drex-latest` and System 2 `deepinfra/Qwen/Qwen3.8-27B`. `NACE_API_KEY` and `DEEPINFRA_API_KEY` were loaded from the gitignored `.env` into that process and were not printed. Intern exited 2 before any tool call. The console returned HTTP 403 with `x-vercel-mitigated: challenge` for `https://console.nace.ai/v1/systemone` and `https://console.nace.ai/v1/models`. The same challenge answered `curl.exe`. DeepInfra itself returned HTTP 200 and listed `Qwen/Qwen3.8-27B`. No project file was written by that run. `tests/test_intern_clearance.py` does not exist. Sanitized evidence: `docs/intern-drexathon.sanitized.jsonl`.

An earlier, different configuration did run and is described below. It does not satisfy the posted quick start.

Intern compliance for that earlier configuration is one bounded coding task. Installing the package was not treated as usage. The harness ran, called tools, and wrote a file that pytest then executed.

## What ran

- Package: `intern-agent` 0.30.1, resolved to Git commit `e4e32d5ddcb2738e0a449f58797c9519c1876f9b` from https://github.com/nace-ai/intern
- Entry point: `intern` (`intern.cli:main`)
- System 2: `openrouter/openai/gpt-5.4`
- System 1: `drex-max-v1.1` on the decisions API (`decisions-api:drex-max-v1.1` in the event log)
- `INTERN_HOME`: `work/intern-home` (gitignored)
- Flags: `-p --json --max-turns 6 --no-context-files --no-session`
- Run id: `766e0a9083db`
- Stop reason: `turn_budget` after 7 turns (the CLI exit code was 1 because the budget ended the run)
- System 2 usage reported by Intern: 39,604 input tokens, 1,678 output tokens, 19,456 cache-read tokens, cost $0.080404
- System 1: 12 asks, 0 failed, reported cost $0.0
- These Intern charges are not included in the benchmark totals

The task was to add `tests/test_intern_integrity.py` and no other file. The tests check that model input omits hidden case fields, that an error status is a confusion-matrix FAILURE rather than a successful REVIEW, and that a missing cost stays unknown. Intern read `prompts.py`, `schemas.py`, `evaluation.py`, and the start of `data/dev_20.json`. An edit against a missing path failed. The following write created the 61-line test. Intern did not run pytest and did not read `.env` or `results/live`.

## Verification

`python -m pytest tests/test_intern_integrity.py` passed, 3 tests, after the run. The application modules those tests import were not edited by Intern.

A sanitized event log is `work/intern-run.sanitized.jsonl`. The raw JSONL stays in ignored `work/` because tool results contain source text.

## Windows runtime notes

Intern 0.30.1 imports Unix `fcntl` and reads `signal.SIGHUP` at CLI import time. On this machine:

- `work/wincompat/fcntl.py` supplies `flock` through `msvcrt.locking` and is placed on `PYTHONPATH` only for the Intern process.
- The installed copy in `.venv` changes `ENDING_SIGNALS` to skip signals this Python build does not define.

Those two local compatibility edits are not part of the submitted application. The agent loop, tool calls, System 2 requests, and System 1 decisions are the official harness.

## Hard-case test task, 2026-11-14

A later session used the official harness for one bounded test-writing task. The console 403 from the quick-start attempt above did not recur.

- Package: `intern-agent` 0.30.1
- Entry point: `intern.exe`, launched by `work/run_intern.py`
- System 1: `drex-latest`
- System 2: `deepinfra/Qwen/Qwen3.8-27B`
- Flags: `-p --json --max-turns 14 --no-context-files --no-session --no-memory`
- Run id: `e073f6ddab5c`
- Process exit code: 0
- Stop reason: `completed` after 12 turns
- System 1: 24 asks, 0 failed, reported cost $0.0
- System 2 usage reported by Intern: 167,775 input tokens, 14,452 output tokens, 148,960 cache-read tokens, reported cost $0.0
- `NACE_API_KEY` and `DEEPINFRA_API_KEY` were loaded from the environment into that process. The values were not printed, and this note does not contain them
- The task was to add `tests/test_intern_clearance.py` only, unless a failing test proved a defect in `rules.py` or `evaluation.py`. Intern wrote that file. Those two modules were not edited. The tests do not open a socket and do not read `.env`, `prompts.py`, `data/`, or `results/`
- Independent check after the run: `.venv\Scripts\python.exe -m pytest tests/test_intern_clearance.py -q` is included in the full suite result of 95 passed. The new file alone is 16 tests
- Sanitized event summary: `work/intern-drexathon.sanitized.jsonl` (112 retained events). The raw JSONL stays in ignored `work/` because tool results contain source text
- The sanitized summary has no HTTP 403, `x-vercel-mitigated`, or challenge event. System 1 reported zero failed asks, so this run is not another console 403
