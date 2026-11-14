"""NDI upload, parse, extract, ground, then a separate Drex decision."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from clients import DocumentClient, DrexClient, MockDecisionClient, OpenRouterClient, settled_document_cost, utc_now
from config import NDI_BUDGET_USD, ROOT, nace_api_key, openrouter_api_key
from evaluation import summarize
from prompts import RULEBOOK, input_hash
from schemas import DecisionInput
from storage import ResultStore

RESERVE = Decimal("1.50")


def load_showcase(limit: int) -> list[dict[str, Any]]:
    showcase = json.loads((ROOT / "data" / "showcase.json").read_text(encoding="utf-8"))
    cases = {case["case_id"]: case for case in json.loads((ROOT / "data" / "test_100.json").read_text(encoding="utf-8"))}
    return [cases[case_id] for case_id in showcase["case_ids"][:limit]]


def schema() -> dict[str, Any]:
    return json.loads((ROOT / "schemas" / "ndi_fields.json").read_text(encoding="utf-8"))


def evidence_from_fields(fields: list[dict[str, Any]], note: str) -> str:
    lines = [note]
    for field in fields:
        lines.append(
            f"path {field.get('path')} status {field.get('status')} ambiguous {field.get('ambiguous')} value {field.get('value')}"
        )
        for citation in field.get("citations") or []:
            quote = citation.get("source_text")
            if isinstance(quote, str) and quote.strip():
                lines.append("citation " + quote.strip())
    return "\n".join(lines)


def mock_fields(case: dict[str, Any]) -> list[dict[str, Any]]:
    fields = []
    for name, meta in case["source_annotations"]["fields"].items():
        if name == "conflicted_fields":
            continue
        status = "not_found" if meta["status"] in ("unknown", "explicitly_absent") else "found"
        fields.append(
            {
                "path": "/" + name,
                "value": meta["expected"] if status == "found" else None,
                "status": status,
                "ambiguous": meta["status"] == "conflicted",
                "citations": [{"source_text": quote["quote"]} for quote in meta["quotes"][:1]],
            }
        )
    return fields


async def decide_on_evidence(mock: bool, http: httpx.AsyncClient, case_id: str, evidence: str) -> dict[str, Any]:
    shared = f"{RULEBOOK.strip()}\n\nEVIDENCE\n\n{evidence}\n"
    request = DecisionInput(case_id=case_id, shared_input=shared, input_hash=input_hash(shared), evidence_mode="extracted")
    if mock:
        drex = MockDecisionClient("drex")
        router = MockDecisionClient("openrouter")
    else:
        drex = DrexClient(http)
        router = OpenRouterClient(http)
    drex_result, router_result = await asyncio.gather(drex.decide(request), router.decide(request))
    return {"drex": drex_result.dump_public(), "openrouter": router_result.dump_public(), "input_hash": request.input_hash}


def score_fields(case: dict[str, Any], fields: list[dict[str, Any]]) -> dict[str, Any]:
    gold = case["facts"]
    comparable = 0
    matched = 0
    missing = 0
    ambiguous = 0
    by_path = {str(field.get("path", "")).lstrip("/"): field for field in fields}
    for name, expected in gold.items():
        field = by_path.get(name)
        if field is None:
            missing += 1
            continue
        if field.get("ambiguous") is True:
            ambiguous += 1
        if field.get("status") == "not_found":
            missing += 1
        comparable += 1
        value = field.get("value")
        if name == "conflicted_fields":
            same = set(value or []) == set(expected or [])
        elif expected is None:
            same = value is None
        else:
            same = str(value) == str(expected)
        if same and field.get("status") != "not_found":
            matched += 1
    return {
        "field_matches": matched,
        "fields_scored": comparable,
        "missing_or_not_found": missing,
        "ambiguous": ambiguous,
        "packet_perfect": matched == comparable and ambiguous == 0 and missing == 0,
    }


async def run_live_case(http: httpx.AsyncClient, case: dict[str, Any], spent: Decimal) -> dict[str, Any]:
    if spent + RESERVE > NDI_BUDGET_USD:
        return {"case_id": case["case_id"], "status": "skipped", "error": "NDI local budget reservation would be exceeded"}
    pdf_path = ROOT / "data" / "pdfs" / f"{case['case_id']}.pdf"
    data = pdf_path.read_bytes()
    client = DocumentClient(http)
    started = time.perf_counter()
    path = f"cosmic-customs/{case['case_id']}.pdf"
    grant = await client.create_upload_grant(path, len(data))
    record: dict[str, Any] = {
        "case_id": case["case_id"],
        "mode": "live",
        "measurements": "measured",
        "workspace_id": grant.get("workspace_id"),
        "grant_expires_at": grant.get("expires_at"),
        "jobs": [],
    }
    upload = await client.redeem_upload(grant, f"{case['case_id']}.pdf", data, path)
    if upload.get("job_id") and not (upload.get("result") or {}).get("file"):
        upload = await client.wait_job(upload)
        record["jobs"].append(job_view(upload))
        if upload.get("poll_timeout"):
            record["status"] = "poll_timeout"
            record["error"] = "upload poll timed out; no duplicate job was created"
            return record
    file_info = (upload.get("result") or {}).get("file") or {}
    file_id = file_info.get("file_id")
    if not file_id:
        record["status"] = "error"
        record["error"] = "upload response did not include result.file.file_id"
        return record
    record["file_id"] = file_id
    parse_job = await client.wait_job(await client.parse(grant["workspace_id"], file_id, f"cosmic-parse-{case['case_id']}-{uuid.uuid4().hex[:8]}"))
    record["jobs"].append(job_view(parse_job))
    if parse_job.get("poll_timeout") or parse_job.get("status") != "succeeded":
        record["status"] = "poll_timeout" if parse_job.get("poll_timeout") else parse_job.get("status")
        record["error"] = "parse did not succeed; extract and ground were not resubmitted"
        return record
    extract_job = await client.wait_job(
        await client.extract(parse_job["job_id"], schema(), f"cosmic-extract-{case['case_id']}-{uuid.uuid4().hex[:8]}")
    )
    record["jobs"].append(job_view(extract_job))
    fields = []
    parse_markdown = ((parse_job.get("result") or {}).get("document") or {}).get("markdown")
    if extract_job.get("status") == "succeeded":
        fields = (extract_job.get("result") or {}).get("fields") or []
        note = "Fields and citations below are the extractor output."
        evidence = evidence_from_fields(fields, note)
    else:
        evidence = "Extraction did not succeed. Generator facts were not substituted.\n"
        if isinstance(parse_markdown, str):
            evidence += parse_markdown[:8000]
    targets = []
    for field in fields:
        for citation in field.get("citations") or []:
            quote = citation.get("source_text")
            if isinstance(quote, str) and quote.strip():
                targets.append({"id": str(field.get("path", "quote")).strip("/").replace("/", "_")[:40], "text": quote.strip()[:500]})
                break
        if len(targets) == 30:
            break
    ground_job = None
    if targets and not extract_job.get("poll_timeout"):
        ground_job = await client.wait_job(
            await client.ground(parse_job["job_id"], targets, f"cosmic-ground-{case['case_id']}-{uuid.uuid4().hex[:8]}")
        )
        record["jobs"].append(job_view(ground_job))
    decisions = await decide_on_evidence(False, http, case["case_id"], evidence)
    record["elapsed_ms"] = (time.perf_counter() - started) * 1000
    record["ocr_applied"] = (parse_job.get("result") or {}).get("ocr_applied")
    record["fields"] = fields
    record["field_score"] = score_fields(case, fields)
    record["grounding"] = ground_summary(ground_job)
    record["decisions"] = decisions
    charges = charge_accounting(record["jobs"])
    record["cost_usd"] = charges["cost_usd"]
    record["charge_accounting"] = charges
    record["status"] = "ok"
    return record


def job_view(job: dict[str, Any]) -> dict[str, Any]:
    cost = job.get("cost") if isinstance(job.get("cost"), dict) else None
    return {
        "job_id": job.get("job_id"),
        "kind": job.get("kind"),
        "status": job.get("status"),
        "poll_timeout": job.get("poll_timeout"),
        "usage_final": job.get("usage_final"),
        "timing": job.get("timing"),
        "cost": None
        if cost is None
        else {"total_usd": cost.get("total_usd"), "pricing_version": cost.get("pricing_version"), "lines": cost.get("lines")},
        "error": job.get("error"),
    }


def charge_accounting(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """A missing job charge stays unknown. A recorded zero is a known charge."""
    known: list[Decimal] = []
    unknown = 0
    for job in jobs:
        amount = settled_document_cost(job)
        if amount is None:
            unknown += 1
        else:
            known.append(amount)
    known_sum = sum(known, Decimal("0")) if known else None
    complete = unknown == 0 and known_sum is not None
    return {
        "cost_usd": format(known_sum, "f") if complete else None,
        "known_partial_usd": None if complete or known_sum is None else format(known_sum, "f"),
        "known_count": len(known),
        "unknown_count": unknown,
    }


def ground_summary(job: dict[str, Any] | None) -> dict[str, Any]:
    if not job or job.get("status") != "succeeded":
        return {"targets": 0, "found": 0, "coverage": None}
    targets = (job.get("result") or {}).get("targets") or []
    found = sum(1 for target in targets if target.get("status") == "found")
    return {"targets": len(targets), "found": found, "coverage": None if not targets else found / len(targets)}


async def run(mock: bool, limit: int) -> None:
    cases = load_showcase(limit)
    store = ResultStore()
    mode = "mock" if mock else "live"
    run_id = utc_now().replace(":", "") + "-" + uuid.uuid4().hex[:8]
    rows = []
    spent = Decimal("0")
    async with httpx.AsyncClient() as http:
        if not mock and not nace_api_key():
            raise SystemExit("missing configuration: NACE_API_KEY")
        for case in cases:
            if mock:
                fields = mock_fields(case)
                evidence = evidence_from_fields(fields, "MOCK fixture extraction. Not an NDI result.")
                decisions = await decide_on_evidence(True, http, case["case_id"], evidence)
                row = {
                    "case_id": case["case_id"],
                    "mode": "mock",
                    "measurements": "simulated",
                    "status": "ok",
                    "fields": fields,
                    "field_score": score_fields(case, fields),
                    "grounding": {"targets": len(fields), "found": len(fields), "coverage": 1, "simulated": True},
                    "decisions": decisions,
                    "cost_usd": None,
                    "note": "Annotation fixture. Do not read this as document-model performance.",
                }
            else:
                if not openrouter_api_key():
                    raise SystemExit("missing configuration: OPENROUTER_API_KEY")
                row = await run_live_case(http, case, spent)
                if row.get("cost_usd") is not None:
                    spent += Decimal(row["cost_usd"])
                elif row.get("status") == "ok":
                    spent += RESERVE
            row["run_id"] = run_id
            store.append(mode, "documents", row)
            rows.append(row)
            if row.get("status") in ("poll_timeout", "skipped"):
                break
    gold = {case["case_id"]: case["expected_verdict"] for case in cases[: len(rows)]}
    drex_results = []
    from schemas import DecisionResult

    for row in rows:
        if row.get("decisions"):
            drex_results.append(DecisionResult.model_validate(row["decisions"]["drex"]))
    summary = {
        "run_id": run_id,
        "mode": mode,
        "measurements": "simulated" if mock else "measured",
        "planned": limit,
        "attempted": len(rows),
        "field_scores": [row.get("field_score") for row in rows],
        "drex_on_extracted_evidence": summarize(drex_results, gold, limit) if drex_results else None,
        "known_cost_usd": format(spent, "f") if not mock else None,
    }
    out = store.root / mode / "documents"
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.json").write_text(json.dumps({"run_id": run_id, "summary": summary, "rows": rows}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--limit", type=int, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.mock, args.limit))


if __name__ == "__main__":
    main()
