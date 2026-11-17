"""Tool-call summaries from session journals, for the error analysis of eval runs: per run, how many calls each tool
had, how many succeeded, and where each of the others failed.

    uv run evals/tool_calls.py                                     every journal in ~/.intern/sessions
    uv run evals/tool_calls.py <journal or folder> ... [--out runs.json] [--failures failures.jsonl] [--top 20]

Prints the calls by tool and by kind of failure over every run, then the failures seen most often. `--out` writes each
run's `ToolCallSummary` (`intern.extensions.tool_summary`) with its journal; `--failures` writes one JSON line per
call that did not succeed, with its journal and run, to filter with jq or load as a table.

A failure's kind is where the call failed: `invalid_json` (its arguments were not JSON, so it never ran),
`unknown_tool`, `bad_arguments`, `refused` (by an extension: `refused_by`), `stopped`, `failed` (the tool ran and
reported it), `crashed` (the tool raised) or `interrupted`. Errors journaled before 0.43.0 have no kind; theirs is told
from their text. A run started with `--extension tool-summary` emits the same summary as an event at its end.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import get_args

from pydantic import TypeAdapter, ValidationError

from intern.extensions.tool_summary import FailureKind, ToolCallSummary, summaries
from intern.journal import Entry
from intern.messages import ToolErrorKind

SESSIONS = Path.home() / ".intern" / "sessions"
KINDS: tuple[FailureKind, ...] = ("invalid_json", *get_args(ToolErrorKind))

_entry: TypeAdapter[Entry] = TypeAdapter(Entry)


def read_entries(path: Path) -> list[Entry]:
    """The journal's entries in append order, read only: lines that do not parse (a torn tail, an older format) are
    skipped and counted, never repaired as opening the session would."""
    entries, skipped = [], 0
    for line in path.read_bytes().splitlines():
        if line.strip():
            try:
                entries.append(_entry.validate_json(line))
            except ValidationError:
                skipped += 1
    if skipped:
        print(f"{path.name}: skipped {skipped} unreadable lines", file=sys.stderr)
    return entries


def journals(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        found += sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    return found


def share(part: int, whole: int) -> str:
    return f"{part / whole:.1%}" if whole else "-"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("paths", nargs="*", type=Path, default=[SESSIONS], help="Journals, or folders of them.")
    parser.add_argument("--out", type=Path, help="Write every run's summary here as JSON.")
    parser.add_argument("--failures", type=Path, help="Write every failed call here, one JSON object a line.")
    parser.add_argument("--top", type=int, default=20, help="How many of the most frequent failures to print.")
    args = parser.parse_args(argv)

    runs: list[tuple[Path, ToolCallSummary]] = [
        (path, summary) for path in journals(args.paths) for summary in summaries(read_entries(path)) if summary.calls
    ]
    if not runs:
        print("no tool calls in these journals", file=sys.stderr)
        return 2

    calls = sum(s.calls for _, s in runs)
    ok = sum(s.ok for _, s in runs)
    by_kind: Counter[FailureKind] = Counter()
    for _, summary in runs:
        by_kind.update(summary.failures_by_kind)
    kinds: list[FailureKind] = [k for k in KINDS if by_kind[k]]
    files = len({path for path, _ in runs})
    print(f"{len(runs)} runs in {files} journals, {calls:,} calls, {share(calls - ok, calls)} not ok")

    print("\n| tool | calls | ok | not ok | " + " | ".join(kinds) + " |")
    print("|---|---|---|---|" + "---|" * len(kinds))
    tools: dict[str, Counter[str]] = {}
    for _, summary in runs:
        for name, stats in summary.by_tool.items():
            row = tools.setdefault(name, Counter())
            row.update({"calls": stats.calls, "ok": stats.ok, **stats.failures})
    for name, row in sorted(tools.items(), key=lambda kv: -kv[1]["calls"]):
        not_ok = row["calls"] - row["ok"]
        counts = " | ".join(str(row[k] or "") for k in kinds)
        print(f"| {name} | {row['calls']:,} | {row['ok']:,} | {not_ok:,} ({share(not_ok, row['calls'])}) | {counts} |")

    print("\n| kind | calls | share of calls | runs with one |")
    print("|---|---|---|---|")
    for kind in kinds:
        with_one = sum(1 for _, s in runs if s.failures_by_kind.get(kind))
        print(f"| {kind} | {by_kind[kind]:,} | {share(by_kind[kind], calls)} | {with_one} of {len(runs)} |")

    # The same failure in many runs reads as one row: by tool, kind and the first line of what failed.
    seen: Counter[tuple[str, str, str]] = Counter(
        (f.tool, f.kind, f.what_failed.splitlines()[0][:160] if f.what_failed else "")
        for _, s in runs
        for f in s.failures
    )
    if seen:
        print("\n| count | tool | kind | what failed |\n|---|---|---|---|")
        for (tool, failed_as, what), count in seen.most_common(args.top):
            print(f"| {count} | {tool} | {failed_as} | {what.replace('|', '\\|')} |")

    if args.out:
        rows = [{"journal": str(path), **summary.model_dump(mode="json")} for path, summary in runs]
        args.out.write_text(json.dumps(rows, indent=1))
    if args.failures:
        with args.failures.open("w") as out:
            for path, summary in runs:
                for failure in summary.failures:
                    row = {"journal": str(path), "run_id": summary.run_id, **failure.model_dump(mode="json")}
                    out.write(json.dumps(row) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
