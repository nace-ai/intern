"""Run the Drex vs OpenAI benchmark. Makes real, billed API calls only when started with --yes.

  python -m evaluation.benchmark.run --name smoke --limit-cases 10 --openai-model <ID> --dry-run
  python -m evaluation.benchmark.run --name smoke --limit-cases 10 --openai-model <ID> --yes
  python -m evaluation.benchmark.run --name main --openai-model <ID> --yes

Every response is appended to results/<name>/raw.jsonl as soon as it arrives, so an interrupted run
resumes where it stopped (same --name). Errors are recorded, never dropped or retried silently.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from service.config import REPO_ROOT  # also loads the git-ignored .env

from evaluation.benchmark.providers import Decision, Provider, _system_prompt
from evaluation.benchmark.tasks import TASKS, Case, Task, sample_cases

RESULTS_ROOT = REPO_ROOT / "evaluation" / "benchmark" / "results"
DEFAULT_SEED = 20261114


def result_key(row: dict) -> tuple[str, str, str]:
    return (row["task"], row["case_id"], row["provider"])


def load_rows(path: Path) -> dict[tuple[str, str, str], dict]:
    """Latest row per (task, case, provider); a later line replaces an earlier one."""
    rows: dict[tuple[str, str, str], dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                rows[result_key(row)] = row
    return rows


def _row(task: Task, case: Case, provider: Provider, decision: Decision) -> dict:
    return {
        "task": task.name,
        "case_id": case.case_id,
        "label": case.label,
        "provider": provider.name,
        "requested_model": getattr(provider, "requested_model", None),
        "model": decision.model,
        "option": decision.option,
        "probabilities": decision.probabilities,
        "latency_seconds": round(decision.latency_seconds, 4),
        "input_tokens": decision.input_tokens,
        "output_tokens": decision.output_tokens,
        "server_time_ms": decision.server_time_ms,
        "request_id": decision.request_id,
        "error": decision.error,
        "raw": decision.raw,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
    }


def run_benchmark(
    cases_by_task: dict[str, list[Case]],
    providers: dict[str, Provider],
    out_dir: Path,
    workers: int = 2,
    retry_errors: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "raw.jsonl"
    existing = load_rows(raw_path)
    jobs = []
    for task_name, cases in cases_by_task.items():
        for case in cases:
            for provider_name in providers:
                key = (task_name, case.case_id, provider_name)
                previous = existing.get(key)
                if previous is not None and not (retry_errors and previous.get("error")):
                    continue
                jobs.append((TASKS[task_name], case, providers[provider_name]))

    lock = threading.Lock()
    counts = {"planned": len(jobs), "done": 0, "errors": 0}

    def work(task: Task, case: Case, provider: Provider) -> dict:
        try:
            decision = provider.decide(task, case.narrative)
        except Exception as error:  # a bug must be recorded as an error row, never a silent gap
            decision = Decision(None, 0.0, error={"code": "internal_error", "message": type(error).__name__})
        row = _row(task, case, provider, decision)
        with lock, raw_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    started = time.perf_counter()
    executor = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        futures = [executor.submit(work, *job) for job in jobs]
        for future in as_completed(futures):
            row = future.result()
            counts["done"] += 1
            counts["errors"] += 1 if row["error"] else 0
            if counts["done"] % 50 == 0 or counts["done"] == counts["planned"]:
                log(f"  {counts['done']}/{counts['planned']} calls done, {counts['errors']} errors, {time.perf_counter() - started:.0f}s elapsed")
    except KeyboardInterrupt:
        executor.shutdown(wait=False, cancel_futures=True)
        log("Interrupted. Completed calls are saved; re-run with the same --name to resume.")
        raise
    finally:
        executor.shutdown(wait=True)
    return counts


def build_meta(cases_by_task: dict[str, list[Case]], args: argparse.Namespace, provider_models: dict[str, str | None]) -> dict:
    return {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": args.seed,
        "n_requested": args.n,
        "workers": args.workers,
        "providers": provider_models,
        "openai_mode": "plain_text" if args.openai_text_mode else "json_schema",
        "openai_temperature": args.openai_temperature,
        "tasks": {
            name: {
                "title": TASKS[name].title,
                "ground_truth_field": TASKS[name].ground_truth_field,
                "options": TASKS[name].options,
                "instructions": TASKS[name].instructions,
                "cases": {case.case_id: case.label for case in cases},
            }
            for name, cases in cases_by_task.items()
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=REPO_ROOT / "data" / "asrs.duckdb")
    parser.add_argument("--tasks", default=",".join(TASKS), help=f"comma-separated, from: {', '.join(TASKS)}")
    parser.add_argument("--n", type=int, default=1000, help="cases per task (class-balanced)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--providers", default="drex,openai")
    parser.add_argument("--drex-model", default="drex-latest")
    parser.add_argument("--openai-model", help="OpenAI model ID (required when openai is used; no default is assumed)")
    parser.add_argument("--openai-temperature", type=float, default=None, help="omitted from the request when not set")
    parser.add_argument("--openai-text-mode", action="store_true", help="plain-text answers instead of JSON-schema structured output")
    parser.add_argument("--workers", type=int, default=2, help="parallel calls; lower it if you see provider_rate_limited errors")
    parser.add_argument("--limit-cases", type=int, help="smoke test: only the first N cases of each task")
    parser.add_argument("--name", default="benchmark", help="results folder name; reuse it to resume")
    parser.add_argument("--retry-errors", action="store_true", help="re-run calls that previously ended in an error")
    parser.add_argument("--dry-run", action="store_true", help="show the plan and prompts; make no API calls")
    parser.add_argument("--yes", action="store_true", help="confirm that billed API calls may be made")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import duckdb

    args = parse_args(argv)
    names = [t.strip() for t in args.tasks.split(",") if t.strip()]
    provider_names = [p.strip() for p in args.providers.split(",") if p.strip()]
    unknown = [t for t in names if t not in TASKS] + [p for p in provider_names if p not in ("drex", "openai")]
    if unknown:
        print(f"Unknown task or provider: {unknown}", file=sys.stderr)
        return 2
    if "openai" in provider_names and not args.openai_model:
        print("--openai-model is required (no default model is assumed).", file=sys.stderr)
        return 2
    if not args.db.exists():
        print(f"ASRS database not found: {args.db}", file=sys.stderr)
        return 2

    with duckdb.connect(str(args.db), read_only=True) as db:
        cases_by_task = {name: sample_cases(db, TASKS[name], args.n, args.seed) for name in names}
    if args.limit_cases:
        cases_by_task = {name: cases[: args.limit_cases] for name, cases in cases_by_task.items()}

    out_dir = RESULTS_ROOT / args.name
    existing = load_rows(out_dir / "raw.jsonl")
    total_calls = sum(len(c) for c in cases_by_task.values()) * len(provider_names)
    remaining = total_calls - sum(1 for key in existing if key[0] in cases_by_task and key[2] in provider_names)
    print(f"Benchmark '{args.name}': seed {args.seed}")
    for name, cases in cases_by_task.items():
        by_label: dict[str, int] = {}
        for case in cases:
            by_label[case.label] = by_label.get(case.label, 0) + 1
        print(f"  {name}: {len(cases)} cases {by_label}")
    print(f"  providers: {provider_names} | calls planned: {total_calls} | still to run: {max(remaining, 0)}")

    meta_path = out_dir / "run_meta.json"
    if meta_path.exists():
        saved = json.loads(meta_path.read_text(encoding="utf-8"))
        current = {name: [case.case_id for case in cases] for name, cases in cases_by_task.items()}
        if {name: list(info["cases"]) for name, info in saved["tasks"].items()} != current:
            print(f"Results folder '{args.name}' was created with a different task or case list "
                  "(seed, n, --limit-cases, tasks or database changed). Use a new --name.", file=sys.stderr)
            return 2

    if args.dry_run:
        task = TASKS[names[0]]
        print("\n--- Drex question (choice), state = the narrative ---")
        print(task.instructions)
        for label, description in task.options.items():
            print(f"  {label}: {description}")
        print("\n--- OpenAI system prompt, user message = the narrative ---")
        print(_system_prompt(task, structured=not args.openai_text_mode))
        print("\nDry run: no API calls were made.")
        return 0
    if not args.yes:
        print("\nThis will make billed API calls. Re-run with --yes to start (or --dry-run to preview).")
        return 2

    providers: dict[str, Provider] = {}
    try:
        if "drex" in provider_names:
            from evaluation.benchmark.providers import DrexProvider
            from agents.drex_client import DrexError

            try:
                providers["drex"] = DrexProvider(args.drex_model)
            except DrexError as error:
                print(error.message, file=sys.stderr)
                return 2
        if "openai" in provider_names:
            from evaluation.benchmark.providers import OpenAIProvider

            try:
                providers["openai"] = OpenAIProvider(args.openai_model, args.openai_temperature, structured=not args.openai_text_mode)
            except RuntimeError as error:
                print(error, file=sys.stderr)
                return 2

        out_dir.mkdir(parents=True, exist_ok=True)
        meta = build_meta(cases_by_task, args, {n: getattr(p, "requested_model", None) for n, p in providers.items()})
        if not meta_path.exists():
            meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        counts = run_benchmark(cases_by_task, providers, out_dir, args.workers, args.retry_errors)
    finally:
        for provider in providers.values():
            close = getattr(provider, "close", None)
            if callable(close):
                close()
    print(f"Finished: {counts['done']} calls, {counts['errors']} errors. Raw results: {out_dir / 'raw.jsonl'}")
    print(f"Next: python -m evaluation.benchmark.report --name {args.name} --db {args.db} [price flags]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
