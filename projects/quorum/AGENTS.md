# Rules for agents working in this repository

Quorum: accounts-payable invoice decisions (`approve`, `verify`, `block`) using Nace NDI, Drex Search and Drex.

## Layout
- `backend/app/`: FastAPI app and pipeline modules. `backend/scripts/`: one-off scripts. `backend/tests/`: pytest.
- `backend/data/`: generated files, git-ignored. `dataset/`: source parquet files, git-ignored, read-only.
- `web/`: Vite + React + TypeScript front end.
- `.plans/`: specs and task briefs. `.plans/SPEC-FINAL.md` wins over every other plan file.
- `.plans/from-himanshu/nace-docs/`: Nace API docs. Read `NACE_AGENT_GUIDE.md` there before writing Nace code.

## Hard rules
1. Never read, print, copy or commit `.env` files or API keys. Code reads keys from environment variables only.
2. `backend/data/cases.jsonl` holds expected labels. Only `backend/scripts/run_bench.py` may read it. No module under `backend/app/` may import or open it.
3. IBANs and account numbers are strings. Money is an integer in minor units plus a currency code. Use `Decimal`, never `float`, to convert amounts.
4. A failed API request is recorded as a failure with `error` set. It is never treated as an approval.
5. The guard may raise an action (`approve` < `verify` < `block`). It may never lower one.
6. Do not edit files under `.plans/`, `dataset/`, `.intern-home/` or `.venv/`.
7. Do not run `git push`, and do not delete files you did not create.

## Style
- Python 3.12, type hints, pydantic models from `backend/app/contracts.py`. Small pure functions. No new dependencies without need.
- Every module you write gets a pytest file under `backend/tests/`. Run the tests and fix failures before you finish.
- Finish by stating what you created and the test summary line.
