#!/usr/bin/env python
"""Drop-a-bill ingest CLI: PDF files -> BillProof case.

Usage:
    .venv/bin/python scripts/ingest_pdf.py bill.pdf [eob.pdf ...]
    .venv/bin/python scripts/ingest_pdf.py bill=mystery1.pdf eob=mystery2.pdf
    .venv/bin/python scripts/ingest_pdf.py split=merged_packet.pdf
    .venv/bin/python scripts/ingest_pdf.py mixed=merged_packet.pdf
    .venv/bin/python scripts/ingest_pdf.py --kind bill weird_name.pdf

Prints the ingest result as JSON ({case_id, mode, fields_found, warnings}).
With no kind hint the doc kind is auto-detected from the PDF text.
`split=` (or `mixed=`) forces the NDI packet-split path in LIVE mode.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.ingest import DOC_KINDS, ingest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdfs", nargs="+", help="PDF paths, optionally kind=path or split=path")
    ap.add_argument("--kind", choices=DOC_KINDS, default=None, help="kind for all bare paths")
    args = ap.parse_args()

    files: dict[str, bytes] = {}
    for spec in args.pdfs:
        head, _, path = spec.partition("=")
        if head in ("split", "mixed"):
            key_prefix = "split:"
        elif head in DOC_KINDS:
            key_prefix = f"{head}:"
        else:
            path = spec
            key_prefix = f"{args.kind}:" if args.kind else ""
        p = Path(path)
        if not p.is_file():
            print(f"ingest_pdf: no such file {p}", file=sys.stderr)
            return 2
        files[f"{key_prefix}{p.name}"] = p.read_bytes()

    try:
        result = ingest(files)
    except ValueError as exc:
        print(f"ingest_pdf: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
