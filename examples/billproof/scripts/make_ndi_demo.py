#!/usr/bin/env python3
"""Build small "mixed packet" PDFs: several whole synthetic documents merged into one file.

One packet = one PDF an NDI-style classifier sees as a single upload, with several
documents concatenated inside it. Outputs land in data/ndi_demo/ next to a manifest
that records the segment layout (kind + 1-based page ranges).

Run: cd billproof && .venv/bin/python scripts/make_ndi_demo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "data" / "synthetic" / "pdf"
OUT_DIR = ROOT / "data" / "ndi_demo"

# First-page text that identifies each document kind in the rendered synthetic PDFs.
MARKERS: dict[str, str] = {
    "bill": "PATIENT STATEMENT",
    "eob": "EXPLANATION OF BENEFITS",
    "plan": "SUMMARY OF BENEFITS",
}

# (output filename, [(case_id, doc_kind), ...]) in packet page order.
PACKETS: list[tuple[str, list[tuple[str, str]]]] = [
    ("SYN-D01_bill_eob.pdf", [("SYN-D01", "bill"), ("SYN-D01", "eob")]),
    ("SYN-D02_eob_bill.pdf", [("SYN-D02", "eob"), ("SYN-D02", "bill")]),
    ("SYN-D03_bill_eob_plan.pdf", [("SYN-D03", "bill"), ("SYN-D03", "eob"), ("SYN-D03", "plan")]),
]


def source_path(case_id: str, kind: str) -> Path:
    path = SRC_DIR / f"{case_id}_{kind}.pdf"
    if not path.is_file():
        raise FileNotFoundError(f"missing source PDF: {path}")
    return path


def build_packet(out_path: Path, segments: list[tuple[str, str]]) -> dict:
    writer = PdfWriter()
    sources: list[str] = []
    expect: list[dict] = []
    page_no = 0
    for case_id, kind in segments:
        src = source_path(case_id, kind)
        n_pages = len(PdfReader(str(src)).pages)
        writer.append(str(src))
        sources.append(src.relative_to(ROOT).as_posix())
        expect.append({"kind": kind, "pages": list(range(page_no + 1, page_no + n_pages + 1))})
        page_no += n_pages

    tmp_path = out_path.with_name(out_path.name + ".tmp")
    with tmp_path.open("wb") as fh:
        writer.write(fh)
    tmp_path.replace(out_path)
    return {"sources": sources, "pages": page_no, "expect": expect}


def verify_packet(out_path: Path, entry: dict) -> list[str]:
    problems: list[str] = []
    pdf = pdfium.PdfDocument(str(out_path))
    try:
        n_pages = len(pdf)
        if n_pages != entry["pages"]:
            problems.append(f"{out_path.name}: {n_pages} pages, expected {entry['pages']}")
        for seg in entry["expect"]:
            first = seg["pages"][0]
            marker = MARKERS[seg["kind"]]
            if first > n_pages:
                problems.append(f"{out_path.name}: {seg['kind']} starts at page {first}, only {n_pages} pages")
                continue
            text = pdf[first - 1].get_textpage().get_text_range()
            if marker not in text:
                problems.append(
                    f"{out_path.name}: page {first} ({seg['kind']}) lacks marker {marker!r}"
                )
    finally:
        pdf.close()
    return problems


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    manifest: dict = {"files": {}}
    for name, segments in PACKETS:
        manifest["files"][name] = build_packet(OUT_DIR / name, segments)

    manifest_path = OUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    problems: list[str] = []
    rows: list[tuple[str, str, str, str]] = []
    for name, entry in manifest["files"].items():
        out_path = OUT_DIR / name
        problems.extend(verify_packet(out_path, entry))
        segs = " ".join(
            f"{seg['kind']}[{seg['pages'][0]}-{seg['pages'][-1]}]" for seg in entry["expect"]
        )
        size_kb = out_path.stat().st_size / 1024
        rows.append((name, str(entry["pages"]), f"{size_kb:.1f} KB", segs))

    width = max(len(r[0]) for r in rows)
    print(f"{'packet'.ljust(width)}  pages  size     segments")
    for name, pages, size, segs in rows:
        print(f"{name.ljust(width)}  {pages:>5}  {size:<8} {segs}")
    print(f"\nmanifest.json: {len(manifest['files'])} packets -> {manifest_path}")

    if problems:
        print("\nVERIFY FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"\nverified: {len(rows)}/{len(rows)} packets OK (page counts + segment markers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
