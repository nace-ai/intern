#!/usr/bin/env python3
"""Write or rebuild the compact Hard Drex logs.

`compact` reads the rich JSONL and writes `hard_test_<lane>.answers.jsonl.gz`.
`expand` rebuilds `data/runs/full/hard_test_<lane>.jsonl` from that gzip when
the rich log is absent. It does not overwrite a log that is already there.
`check` scores the gzip and compares it with the published test figures.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from billproof.bench.hard.drex_archive import (  # noqa: E402
    LANES, assert_published, expand_file, expand_rows, source_path, write_compact,
)


def check() -> None:
    assert_published()
    print("gzip matches the published question, line, dollar, and calibration figures")
    from billproof.bench.hard.metrics import read_jsonl

    for lane in LANES:
        rich = source_path(lane)
        rows = expand_rows(lane)
        if rich is None:
            print(f"{lane} rich log absent; scored from the gzip")
            continue
        full = read_jsonl(rich)
        if [row["case_id"] for row in full] != [row["case_id"] for row in rows]:
            raise SystemExit(f"{lane} case order differs from the rich log")
        rich_cost = sum(float(row.get("cost_usd") or 0) for row in full)
        archive_cost = sum(float(row.get("cost_usd") or 0) for row in rows)
        if rich_cost != archive_cost:
            raise SystemExit(f"{lane} cost sum {archive_cost} != rich {rich_cost}")
        for left, right in zip(full, rows):
            if left.get("latency_ms") != right.get("latency_ms") or left.get("cost_usd") != right.get("cost_usd"):
                raise SystemExit(f"{lane} {left['case_id']} latency or cost differs")
            for name, flag in (left.get("correct") or {}).items():
                if bool(flag) != bool(right["correct"].get(name)):
                    raise SystemExit(f"{lane} {left['case_id']} {name} correct flag differs")
                choice = (left.get("answers") or {}).get(name, {}).get("choice")
                if choice != right["answers"][name]["choice"]:
                    raise SystemExit(f"{lane} {left['case_id']} {name} answer differs")
        print(f"{lane} matches the rich log at {rich}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compact or rebuild the Hard Drex test logs")
    parser.add_argument("command", choices=("compact", "expand", "check"))
    args = parser.parse_args()
    if args.command == "check":
        check()
        return
    for lane in LANES:
        if args.command == "compact":
            path = write_compact(lane)
            print(f"wrote {path} ({path.stat().st_size} bytes) from {source_path(lane)}")
        else:
            path = expand_file(lane)
            print(f"full log {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
