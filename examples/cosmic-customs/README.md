# Cosmic Customs

Intergalactic cargo inspection terminal and an auditable comparison of Nace Drex (`drex-max-v1.1`) with `openai/gpt-5.4` through OpenRouter. The rule labels come from a deterministic fictional oracle. Neither model generates or grades the cases.

This repository was built for the Nace Drexathon by Manoj Kumar Bashaboina. The source is published at <https://github.com/bashaboinamanojkumar/cosmic-customs>. The public dashboard is <https://cosmic-customs.vercel.app>. Opening it replays saved results and does not spend API budget.

## Run the dashboard

### Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

### Ubuntu WSL (Python 3.12)

Tested on 2026-11-14 with `uv` 0.13.8, Python 3.12.3, and the gitignored `.venv-wsl` environment. The Windows `.venv` is left intact and is never used by the WSL commands.

```bash
uv venv .venv-wsl --python 3.12
VIRTUAL_ENV=.venv-wsl uv pip install -r requirements.txt
VIRTUAL_ENV=.venv-wsl uv pip check
.venv-wsl/bin/python -m pytest
.venv-wsl/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

`VIRTUAL_ENV=.venv-wsl uv pip freeze > requirements.lock.txt` regenerates the lock. The WSL run verified here: 36 passed, dashboard replay on port 8000, and one live inspection on port 8001 with `APP_MODE=live`.

Open http://127.0.0.1:8000/. The page loads saved results and does not spend money on load.

The same app is deployed at <https://cosmic-customs.vercel.app> with `vercel.json`. The hosted process stays in `APP_MODE=mock` unless that environment sets live mode and both provider keys. Keys belong in the host environment, not in the repository. A hosted inspection finishes inside the request and is written under `/tmp`, because that filesystem does not keep project-directory writes. The measured replay is read from `results/live/`.

Copy `.env.example` to `.env` and fill the keys on the server. The browser never receives them. `APP_MODE` defaults to `mock`.

Verified with Python 3.12.7 (Windows) and 3.12.3 (WSL). Do not use a 3.11 interpreter for this project. Intern additionally requires the notes in `docs/INTERN_USAGE.md`.

## Mock and live on the dashboard

- The Inspect button is labeled MOCK or LIVE from `/api/health` (`inspect_mode`), and every saved inspection record carries `mode` and `measurements` (`simulated` or `measured`).
- Mock is the default. It is deterministic, opens no socket, and never spends budget.
- Set `APP_MODE=live` in `.env` to make the button call both providers for real (one Drex decision and one OpenRouter decision per click, within the configured budgets). The record stores measured latency, tokens, and cost.
- If `APP_MODE=live` is set but a key is missing, the request is refused with a visible error. A failed live request is never replaced by an undisclosed mock.
- The saved benchmark replay on the page stays read-only; only a clicked inspection makes fresh calls.

## Data and packets

```powershell
.\.venv\Scripts\python.exe generate_cases.py
.\.venv\Scripts\python.exe generate_cases.py --verify --sample 5
.\.venv\Scripts\python.exe generate_pdfs.py --count 10
```

The same commands run identically in WSL as `.venv-wsl/bin/python generate_cases.py ...`.

A separate 34-case diagnostic set lives in `data/hard_cases.json`. It is not part of the measured 100-case benchmark. Generate and check it with `generate_hard_cases.py --verify`. Run it offline with `benchmark_decisions.py --hard --mock`. A live run requires `--confirm-live` and still stops at the configured budgets. `diagnostics.py` prints a read-only report for a saved run and labels mock output as simulated. See `docs/HARD_CASES.md`.

The generator writes 20 development cases and 100 held-out cases (33 CLEAR, 33 HOLD, 34 REVIEW). Seeds, reserved signatures, and hashes are in `data/split_manifest.json`. The packets are newly generated synthetic documents. Holding out signatures and wording families is not a claim of zero training-data contamination.

Specimen art under `assets/generated/` is geometric Pillow artwork. `assets/adobe_firefly/` contains prompts and a manifest. No Adobe Firefly asset was created for this build, so the app does not claim Adobe usage.

## Benchmarks

Mock runs need no keys and are stored under `results/mock/`. They are simulated. Do not read them as model performance.

```powershell
.\.venv\Scripts\python.exe benchmark_decisions.py --mock --pilot 5
.\.venv\Scripts\python.exe benchmark_decisions.py --mock --full 100
.\.venv\Scripts\python.exe benchmark_ndi.py --mock --limit 10
```

Both providers always receive the same shared input (rulebook plus evidence); the run records store the input hash on every result and the held-out run verified the hashes matched across providers for all 100 cases.

### Measured held-out decision test (completed 2026-11-14)

```
.venv-wsl/bin/python benchmark_decisions.py --full 100
```

Run `2026-11-14T215926+0000-36032342`, 100 held-out cases, both providers, 0 failures, 100/100 attempts each.

| Provider | Accuracy | False clearance | Valid P50 / P95 | Known cost |
| --- | --- | --- | --- | --- |
| Drex | 89/100 | 8/67 | 285 ms / 447 ms | $0.00249756 |
| OpenRouter | 95/100 | 3/67 | 1083 ms / 1361 ms | $0.1967275 |

Median OpenRouter/Drex wall-clock latency on 100 matched valid pairs is 3.78 (above 1 means Drex returned first). No cost was unknown on either provider. Nothing was tuned after the development pilot freeze; these figures are reported as measured, including every miss.

The development pilot `2026-11-14T201845+0000-70aac090` (Drex 5/5, OpenRouter 4/5, one miss DEV-12) remains the development evidence and is not the held-out result.

### Document packets

One live document packet already ran:

```powershell
.\.venv\Scripts\python.exe benchmark_ndi.py --limit 1
```

`benchmark_ndi.py --limit 10` is implemented and was not launched. The local NDI budget is $2.00. The one packet settled at $0.12 in document charges. Completed paid document work is reused; it is not re-run to refresh the number.

Measured figures, sample sizes, and limitations are in `docs/BENCHMARK.md` and `submission/slides.md`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

`pytest.ini` sets `testpaths = tests`, so collection does not walk the gitignored `work/` Intern tree. WSL equivalent: `.venv-wsl/bin/python -m pytest`. The earlier WSL result on 2026-11-14 was 36 passed. After the diagnostic-suite additions, Windows `.venv` reported 95 passed.

`tests/test_intern_integrity.py` was written by the official Intern harness and then executed with pytest. See `docs/INTERN_USAGE.md`.
