#!/usr/bin/env python3
"""Score Drex + DeepInfra combinations from recorded answers and rebuild the results hub.

No model calls. Safe to re-run while di_*.jsonl files are still growing.

    cd billproof && PYTHONPATH=src .venv/bin/python scripts/qcombo.py
    cd billproof && PYTHONPATH=src .venv/bin/python scripts/qcombo.py --no-hub
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from billproof.dial.qcombo import build_all, format_table  # noqa: E402


def rebuild_hub() -> None:
    from billproof.results.catalog import RESULTS_MD, SUMMARY_PATH, build_summary, render_markdown

    document = build_summary()
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(document, indent=2) + "\n")
    RESULTS_MD.write_text(render_markdown(document))
    print(f"wrote {SUMMARY_PATH} benchmarks={len(document['benchmarks'])}")
    print(f"wrote {RESULTS_MD}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-hub", action="store_true", help="write qcombo and dial files only")
    args = parser.parse_args()
    documents = build_all()
    for document in documents:
        flag = "partial" if document.get("partial") else "complete"
        print(
            f"{document['bench']} {document['model']}: "
            f"prefix {document.get('n_cases')}/{document.get('n_expected')} {flag} "
            f"qwen_ok={document.get('n_qwen_ok')} both={document.get('n_both_anywhere')}",
            flush=True,
        )
    print(format_table(documents), flush=True)
    if not args.no_hub:
        rebuild_hub()


if __name__ == "__main__":
    main()
