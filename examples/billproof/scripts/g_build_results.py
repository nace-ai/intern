#!/usr/bin/env python3
"""Write data/metrics/all_benchmarks.json and docs/RESULTS.md from recorded files."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from billproof.results.catalog import RESULTS_MD, SUMMARY_PATH, build_summary, render_markdown  # noqa: E402


def main() -> None:
    document = build_summary()
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(document, indent=2) + "\n")
    RESULTS_MD.write_text(render_markdown(document))
    print(f"wrote {SUMMARY_PATH} benchmarks={len(document['benchmarks'])}")
    print(f"wrote {RESULTS_MD}")


if __name__ == "__main__":
    main()
