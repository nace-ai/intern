#!/usr/bin/env python3
"""Prepare PA demo cases: fetch source PDFs, slice criteria pages, render
synthetic packet PDFs + page PNGs. Offline after --fetch; idempotent.

Usage:
  PYTHONPATH=src .venv/bin/python scripts/pa_demo_prepare.py [--fetch] [--case PA-D1]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.pa_demo.chain import prepare_case
from billproof.pa_demo.showcase import SHOWCASE, SRC_DIR, load_row

POLICY_URLS = {  # provenance.policy_source.url per source_pdf
    "MIRx_clinical_criteria.pdf": "https://michigan.magellanrx.com/provider/external/medicaid/mi/doc/en-us/MIRx_clinical_criteria.pdf",
    "PAD_Criteria.pdf": "https://ar.magellanrx.com/provider/external/medicaid/ar/doc/en-us/PAD_Criteria.pdf",
    "PACriteria.pdf": "https://ar.primetherapeutics.com/content/dam/corporate/primetherapeutics/documents/en-us/documents/2025-q1/arkansas/PACriteria.pdf",
}


def fetch() -> None:
    import httpx
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in POLICY_URLS.items():
        out = SRC_DIR / name
        if out.is_file() and out.stat().st_size > 10_000:
            print(f"have {name} ({out.stat().st_size}B)")
            continue
        resp = httpx.get(url, timeout=60.0, follow_redirects=True)
        if resp.status_code != 200 or len(resp.content) < 10_000:
            print(f"FAIL {url} -> {resp.status_code} ({len(resp.content)}B)")
            continue
        out.write_bytes(resp.content)
        print(f"fetched {name} {len(resp.content)}B <- {url}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="download source PDFs first")
    ap.add_argument("--case", default=None, help="single slug, default all")
    args = ap.parse_args()
    if args.fetch:
        fetch()
    slugs = [args.case] if args.case else list(SHOWCASE)
    for slug in slugs:
        meta = prepare_case(slug)
        spec = SHOWCASE[slug]
        row = load_row(spec["row_id"])
        exp = spec.get("expected_pages")
        flag = "" if set(exp or []) <= set(meta["criteria_pages"]) else f"  EXPECTED {exp}"
        print(f"{slug}: {row.target.get('decision'):18s} pages={meta['criteria_pages']}"
              f" slice={Path(meta['policy_slice']).name} packet={Path(meta['packet']).name}{flag}")


if __name__ == "__main__":
    main()
