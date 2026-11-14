"""Build Drex Search supplier history from untouched past invoices.

Run from the repo root: python backend/scripts/build_history.py
Safe to run again: reuses backend/data/workspace.json, skips uploaded files,
reuses the saved ingestion job, and skips the search once a result is saved
(pass --force-search to search again).
"""

from __future__ import annotations

import asyncio
import collections
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pyarrow.parquet as pq  # noqa: E402

from backend.app import config, forge, nace  # noqa: E402
from backend.app.checks import normalise_iban  # noqa: E402

PARQUET = config.DATASET / "invoice" / "test-00000.parquet"
WORKSPACE_NAME = "quorum-ledger"
WORKSPACE_FILE = config.DATA / "workspace.json"
HISTORY_FILE = config.DATA / "history" / "cobalt-wholesale-ltd.json"
COBALT = "cobalt-wholesale-ltd"
EMBER = "ember-bikes-sarl"
N_COBALT = 60
N_EMBER = 20
BATCH = 8
PREFIX = "invoices/"
WAIT_LIMIT_S = 360
POLL_S = 10
AGENT_TIMEOUT_S = 90
QUERY = ("Which bank account (IBAN and BIC) does Cobalt Wholesale Ltd ask to be paid into "
         "on its past invoices? List every distinct IBAN.")
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ibans": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "iban": {"type": "string"},
                    "bic": {"type": "string"},
                    "bank_name": {"type": "string"},
                    "documents_seen": {"type": "integer"},
                },
                "required": ["iban"],
            },
        }
    },
    "required": ["ibans"],
}
META = ["doc_id", "company", "pages", "words", "extracted_data"]


def file_path(company: str, doc_id: str) -> str:
    return f"{PREFIX}{company}/{doc_id}.pdf"


def eligible(rows: list[dict[str, Any]], company: str, exclude: set[str]) -> list[dict[str, Any]]:
    """One-page rows of `company` printing `IBAN:`, not excluded, sorted by doc_id."""
    out = []
    for r in rows:
        if r["company"] != company or r["pages"] != 1 or r["doc_id"] in exclude:
            continue
        if not forge._has_iban_label(r["words"]):
            continue
        bank = json.loads(r["extracted_data"])["parties"]["issuer"].get("bank") or {}
        out.append({"doc_id": r["doc_id"], "company": company, "iban": str(bank.get("iban") or "")})
    out.sort(key=lambda x: x["doc_id"])
    return out


def select() -> tuple[list[dict[str, Any]], str | None]:
    """The documents to upload, and the dataset IBAN (normalised) for Cobalt."""
    arena = {s["doc_id"] for s in forge.list_sources()[-120:]}
    rows = pq.read_table(PARQUET, columns=META).to_pylist()
    cobalt = eligible(rows, COBALT, arena)
    ember = eligible(rows, EMBER, arena)
    print(f"eligible: cobalt={len(cobalt)} ember={len(ember)}")
    ibans = collections.Counter(normalise_iban(c["iban"]) for c in cobalt if c["iban"])
    dataset_iban = ibans.most_common(1)[0][0] if ibans else None
    return cobalt[:N_COBALT] + ember[:N_EMBER], dataset_iban


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


async def workspace_id(state: dict[str, Any]) -> str:
    if state.get("workspace_id"):
        return str(state["workspace_id"])
    ws = await nace.find_workspace(WORKSPACE_NAME) or await nace.create_workspace(WORKSPACE_NAME)
    state.update(workspace_id=ws, name=WORKSPACE_NAME)
    write_json(WORKSPACE_FILE, state)
    return ws


async def upload_missing(ws: str, docs: list[dict[str, Any]]) -> int:
    existing = await nace.list_files(ws, path_prefix=PREFIX)
    todo = [d for d in docs if file_path(d["company"], d["doc_id"]) not in existing]
    print(f"files present: {len(existing)}, to upload: {len(todo)}")
    failed = 0
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        results = await asyncio.gather(
            *(nace.upload_file(ws, original_pdf(d), file_path(d["company"], d["doc_id"])) for d in batch),
            return_exceptions=True,
        )
        for d, r in zip(batch, results):
            if isinstance(r, Exception):
                failed += 1
                print(f"  upload failed {d['doc_id']}: {type(r).__name__}: {r}")
        print(f"  uploaded {min(i + BATCH, len(todo))}/{len(todo)}")
    return failed


def original_pdf(d: dict[str, Any]) -> bytes:
    """Untouched original PDF bytes from test-00000 (no forging)."""
    table = pq.read_table(PARQUET, columns=["doc_id", "pdf"], filters=[("doc_id", "=", d["doc_id"])])
    return table.column("pdf").to_pylist()[0]["bytes"]


async def ingest(ws: str, state: dict[str, Any]) -> tuple[str, float]:
    """Start (or reuse) the ingestion job and wait up to WAIT_LIMIT_S. Returns (status, seconds)."""
    job_id = state.get("ingest_job_id")
    if job_id:
        st = await nace.job_status(job_id)
        if st["status"] in ("failed", "cancelled"):
            print(f"previous ingest job {st['status']}: {st['error']}; starting a new one")
            job_id = None
    if not job_id:
        job_id = await nace.start_ingest(ws, path_prefix=PREFIX)
        state.update(ingest_job_id=job_id, ingest_started_at=time.time())
        write_json(WORKSPACE_FILE, state)
        print(f"ingest job started: {job_id}")
    t0 = time.monotonic()
    while True:
        st = await nace.job_status(job_id)
        if st["status"] in ("succeeded", "failed", "cancelled"):
            break
        if time.monotonic() - t0 > WAIT_LIMIT_S:
            break
        await asyncio.sleep(POLL_S)
    total = time.time() - float(state.get("ingest_started_at") or time.time())
    if st["status"] == "succeeded":
        state["ingest_seconds"] = state.get("ingest_seconds") or round(total, 1)
        write_json(WORKSPACE_FILE, state)
    elif st["status"] in ("failed", "cancelled"):
        print(f"ingest job {st['status']}: {st['error']}")
    return str(st["status"]), float(state.get("ingest_seconds") or total)


async def run_search(ws: str) -> dict[str, Any]:
    res = await nace.search(ws, QUERY, mode="agent", path_prefix=PREFIX,
                            output_schema=OUTPUT_SCHEMA, timeout=AGENT_TIMEOUT_S)
    print(f"agent search: status={res['status']} wall_ms={res['wall_ms']} error={res['error']}")
    if res["status"] == "succeeded" and res["wall_ms"] <= AGENT_TIMEOUT_S * 1000:
        return res
    res = await nace.search(ws, QUERY, mode="fact", path_prefix=PREFIX,
                            output_schema=OUTPUT_SCHEMA, top_k=20)
    print(f"fact search: status={res['status']} wall_ms={res['wall_ms']} error={res['error']}")
    return res


def top_iban(structured: Any) -> str | None:
    rows = structured.get("ibans") if isinstance(structured, dict) else None
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("iban"):
            return normalise_iban(str(row["iban"]))
    return None


async def main() -> int:
    force = "--force-search" in sys.argv
    docs, dataset_iban = select()
    print(f"selected {len(docs)} documents; dataset IBAN {dataset_iban}")
    state = read_json(WORKSPACE_FILE)
    ws = await workspace_id(state)
    print(f"workspace {WORKSPACE_NAME}: {ws}")
    failed = await upload_missing(ws, docs)
    if failed:
        print(f"{failed} uploads failed; run again to retry")
    status, seconds = await ingest(ws, state)
    files = await nace.list_files(ws, path_prefix=PREFIX)
    ingested = sum(1 for f in files.values() if f["ingestion_status"] == "ingested")
    print(f"ingestion {status}: {ingested}/{len(files)} files ingested in {seconds:.0f} s")
    record: dict[str, Any] = {
        "supplier_id": COBALT,
        "workspace_id": ws,
        "query": QUERY,
        "ingested_files": ingested,
        "ingest_seconds": round(seconds, 1),
        "dataset_iban": dataset_iban,
    }
    if status != "succeeded":
        record["status"] = "ingesting" if status not in ("failed", "cancelled") else "ingest_failed"
        write_json(HISTORY_FILE, {**read_json(HISTORY_FILE), **record})
        print(f"stopped: ingestion {status}; saved status {record['status']} to {HISTORY_FILE}")
        return 1
    saved = read_json(HISTORY_FILE)
    if saved.get("status") == "done" and not force:
        print("search result already saved; pass --force-search to search again")
        print(f"matches_dataset: {saved.get('matches_dataset')}")
        return 0
    res = await run_search(ws)
    found = top_iban(res.get("structured_answer"))
    matches = bool(found and dataset_iban and found == dataset_iban)
    record.update(
        status="done" if res["status"] == "succeeded" else "search_failed",
        mode=res["mode"],
        route=res.get("route"),
        answer=res.get("answer"),
        structured_answer=res.get("structured_answer"),
        evidences=res.get("evidences"),
        confidence=res.get("confidence"),
        job_id=res.get("job_id"),
        wall_ms=res.get("wall_ms"),
        error=res.get("error"),
        top_iban=found,
        matches_dataset=matches,
    )
    write_json(HISTORY_FILE, record)
    print(f"top IBAN found: {found}; dataset IBAN: {dataset_iban}")
    print("MATCHES DATASET" if matches else "DOES NOT MATCH DATASET")
    print(f"saved {HISTORY_FILE}")
    return 0 if res["status"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
