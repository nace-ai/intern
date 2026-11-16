"""Smoke test: one clean Cobalt invoice through real NDI intake and one real Drex call.

Run from the repo root: python backend/scripts/smoke.py
A second run reads NDI from backend/data/ndi_cache (no new NDI job).
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pyarrow.parquet as pq  # noqa: E402

from backend.app import checks, config, intake, nace, panel  # noqa: E402

PARQUET = config.DATASET / "invoice" / "test-00001.parquet"
PDF_OUT = config.DATA / "pdfs" / "smoke-clean.pdf"
SUPPLIER = "Cobalt Wholesale Ltd"
COLUMNS = ["pdf", "doc_id", "company_name", "pages", "currency", "total", "number", "words", "extracted_data"]


def to_minor(total: str, currency: str) -> int | None:
    """Printed decimal total -> integer minor units, using Decimal."""
    return intake.amount_to_minor(total, currency)


def load_cobalt_rows() -> list[dict]:
    rows = pq.read_table(PARQUET, columns=COLUMNS).to_pylist()
    return [
        r for r in rows
        if r["company_name"] == SUPPLIER and r["pages"] == 1 and "IBAN:" in (r["words"] or "")
    ]


def build_trusted(rows: list[dict], smoke_row: dict) -> dict:
    """Minimal trusted record for Cobalt from extracted_data (smoke row excluded from history)."""
    issuer = json.loads(smoke_row["extracted_data"])["parties"]["issuer"]
    bank = issuer.get("bank") or {}
    others = [r for r in rows if r["doc_id"] != smoke_row["doc_id"]]
    amounts = [
        a for a in (to_minor(r["total"], r["currency"]) for r in others if r["currency"] == smoke_row["currency"])
        if a is not None
    ]
    sid = "cobalt-wholesale-ltd"
    return {
        sid: {
            "supplier_id": sid,
            "names": [issuer["name"]],
            "iban": checks.normalise_iban(bank.get("iban")) or "",
            "bic": bank.get("bic") or "",
            "median_amount_minor": int(statistics.median(amounts)) if amounts else None,
            "paid_invoice_numbers": sorted({r["number"] for r in others if r["number"]}),
            "verifications": [],
        }
    }


async def main() -> int:
    rows = load_cobalt_rows()
    if not rows:
        print("no matching Cobalt row found")
        return 1
    row = rows[0]
    PDF_OUT.parent.mkdir(parents=True, exist_ok=True)
    PDF_OUT.write_bytes(row["pdf"]["bytes"])
    print(f"doc: {row['doc_id']} {row['number']} {row['currency']} {row['total']} -> {PDF_OUT}")

    if not config.TRUSTED_FILE.exists():
        config.TRUSTED_FILE.write_text(json.dumps(build_trusted(rows, row), indent=2))
        print(f"wrote {config.TRUSTED_FILE}")

    ndi_before = nace.USAGE["ndi_jobs"]
    parsed = await intake.parse_document(str(PDF_OUT))
    from_cache = nace.USAGE["ndi_jobs"] == ndi_before
    print(f"ndi: {'cache hit (no NDI job)' if from_cache else 'live NDI call'}; parse_ms={parsed.parse_ms}")

    iban = parsed.fields.get("iban")
    print(f"extracted iban: {iban.value if iban else None!r}")
    print("statuses:")
    for key, field in parsed.fields.items():
        print(f"  {key:14s} {field.status:10s} {field.value!r}")
    if parsed.warnings:
        print(f"warnings: {parsed.warnings}")

    facts = checks.facts(parsed, checks.load_trusted())
    print(f"facts: {facts.model_dump()}")

    decision = await panel.decide(parsed, facts)
    if decision.error:
        print(f"DREX ERROR: {decision.error}")
        return 2
    print(f"raw_action: {decision.raw_action}  final_action: {decision.final_action}  "
          f"risk_score: {decision.risk_score}")
    print(f"guard: {decision.guard.model_dump()}")
    print("probabilities (12 panel p_yes + action p):")
    for a in decision.panel:
        print(f"  {a.id:28s} {a.p_yes:.3f}")
    print(f"  {'action':28s} {json.dumps(decision.p)}")
    print(f"drex_ms: wall_ms={decision.wall_ms} evaluation_time_ms={decision.decision_ms} "
          f"request_id={decision.request_id} usage={decision.usage.model_dump()}")
    print(f"parse_ms: {parsed.parse_ms}")
    print(f"USAGE this run: {nace.USAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
