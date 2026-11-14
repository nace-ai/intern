#!/usr/bin/env python
"""Render every data/synthetic/cases/*.json to PDFs + NDI fixtures; back-fill
Case.docs and Case.citations (the only case fields W4 owns). Idempotent: safe
to re-run after S1 regenerates cases.

Usage: .venv/bin/python scripts/render_pdfs.py [--case SYN-0001] [--no-verify]
       [--cases-dir D] [--pdf-dir D] [--fixtures-dir D] [--no-write-cases]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.config import SYN_DIR  # noqa: E402
from billproof.schema import Case  # noqa: E402
from billproof.synth.render import render_case, verify_render  # noqa: E402


def process(case_file: Path, pdf_dir: Path, fix_dir: Path, write_cases: bool, verify: bool) -> tuple[str, list[str]]:
    raw = json.loads(case_file.read_text())
    case = Case.model_validate(raw)
    res = render_case(case, pdf_dir)
    (fix_dir / f"{case.case_id}.json").write_text(json.dumps(res.fixture, indent=2, ensure_ascii=False) + "\n")
    if write_cases:
        raw["docs"] = [d.model_dump(mode="json") for d in res.docs]
        raw["citations"] = {k: c.model_dump(mode="json") for k, c in res.citations.items()}
        case_file.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")
    problems = verify_render(res, pdf_dir, case.case_id) if verify else []
    return case.case_id, problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases-dir", type=Path, default=SYN_DIR / "cases")
    ap.add_argument("--pdf-dir", type=Path, default=SYN_DIR / "pdf")
    ap.add_argument("--fixtures-dir", type=Path, default=SYN_DIR / "ndi_fixtures")
    ap.add_argument("--case", dest="only", default=None, help="render only this case_id or file stem")
    ap.add_argument("--no-write-cases", action="store_true", help="do not back-fill docs/citations into case JSON")
    ap.add_argument("--no-verify", action="store_true", help="skip pypdfium2 polygon verification")
    args = ap.parse_args()

    args.pdf_dir.mkdir(parents=True, exist_ok=True)
    args.fixtures_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(args.cases_dir.glob("*.json"))
    if args.only:
        files = [f for f in files if f.stem == args.only or f.name == args.only]
        if not files:
            print(f"no case matching {args.only!r} in {args.cases_dir}", file=sys.stderr)
            return 2
    if not files:
        print(f"no cases in {args.cases_dir}", file=sys.stderr)
        return 0

    n_docs = 0
    all_problems: list[str] = []
    for f in files:
        try:
            case_id, problems = process(f, args.pdf_dir, args.fixtures_dir, not args.no_write_cases, not args.no_verify)
            n_docs += len(json.loads((args.fixtures_dir / f"{case_id}.json").read_text())["docs"])
            for p in problems:
                all_problems.append(f"{case_id}: {p}")
        except Exception as e:  # report every case, fail at end
            all_problems.append(f"{f.stem}: RENDER FAILED {e!r}")
    print(f"rendered {len(files)} cases -> {args.pdf_dir} ({n_docs} docs); fixtures -> {args.fixtures_dir}")
    if all_problems:
        print(f"{len(all_problems)} problem(s):", file=sys.stderr)
        for p in all_problems[:40]:
            print("  " + p, file=sys.stderr)
        return 1
    print("polygon verification: all tracked values found inside their boxes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
