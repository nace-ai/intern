"""One-time NDI ingestion of public guidance PDFs (plan B2).

Pipeline per document, all through documented nace-sdk calls:
  1. Parse   client.documents.parse(UrlSource(...))              -> parse.json
  2. Extract client.documents.extract(ParseResultSource(job_id),  -> extract.json
             schema=...)  verbatim procedural statements + citations
  3. Ground  client.documents.ground(ParseResultSource(job_id),   -> ground.json
             targets=[each extracted statement])  exact page location

Then passages.json is built ONLY from NDI output:
  - grounded: Ground found the quote; passage_text = Ground's matched_text, page from its location
  - parsed:   Ground did not find it, but Extract returned a citation with source_text
  - otherwise the statement is rejected (no fabricated or unlocated citations)

Results are cached under evidence/cache/ and reused; nothing is reprocessed per incident.

Usage:
  python -m evidence.ingest                 # all enabled documents, reusing cached jobs
  python -m evidence.ingest --only faa-ac-120-74b
  python -m evidence.ingest --force         # re-run NDI jobs even if cached
  python -m evidence.ingest --parse-only    # B1 connectivity check: one Parse job only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.taxonomy import GUIDANCE_TOPICS, PATTERN_FAMILIES
from service.config import get_settings

MAX_STATEMENTS = 10  # per page window
EXTRACT_WINDOW_PAGES = 4
GROUND_BATCH = 30  # documented limit: 1 to 30 targets per Ground job
JOB_TIMEOUT_SECONDS = 600

EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "statements": {
            "type": "array",
            "maxItems": MAX_STATEMENTS,
            "description": (
                "Procedural statements, recommendations or definitions from the document that guide "
                "flightcrew behaviour during ground and taxi operations."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": (
                            "Exactly one sentence copied verbatim from the document. Do not paraphrase, "
                            "summarize or merge sentences."
                        ),
                    },
                    "topic": {
                        "type": "string",
                        "enum": GUIDANCE_TOPICS,
                        "description": "The safety topic the sentence addresses. "
                        + " ".join(f"{t}: {PATTERN_FAMILIES[t]}" for t in GUIDANCE_TOPICS),
                    },
                },
                "required": ["text", "topic"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["statements"],
}

EXTRACT_INSTRUCTIONS = (
    "Select sentences that state what pilots or crews should do, or define a term, regarding: ATC "
    "clearances and readbacks, communication and coordination between crew members and controllers, "
    "hold-short instructions, runway incursion prevention, taxi route awareness, and standard operating "
    "procedures or checklists during taxi. Copy each sentence verbatim."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _job_record(job: Any) -> dict[str, Any]:
    """Serialize a DocumentJob, keeping only what we need for provenance and reuse."""
    data = job.model_dump(mode="json")
    return {
        "job_id": data.get("job_id"),
        "kind": data.get("kind"),
        "status": data.get("status"),
        "created_at": data.get("created_at"),
        "finished_at": data.get("finished_at"),
        "error": data.get("error"),
        "result": data.get("result"),
        "saved_at": _now(),
    }


def _run_job(client: Any, start_job, label: str) -> dict[str, Any]:
    from nace_sdk import NaceJobFailedError, NaceJobTimeoutError

    job = start_job()
    print(f"  {label}: job {job.job_id} status={job.status}")
    if job.status != "succeeded":
        try:
            job = client.jobs.wait(job.job_id, timeout=JOB_TIMEOUT_SECONDS)
        except NaceJobFailedError as error:
            raise RuntimeError(f"{label} job failed: {error.job.error}") from error
        except NaceJobTimeoutError as error:
            raise RuntimeError(f"{label} job still {error.job.status} after {JOB_TIMEOUT_SECONDS}s") from error
    print(f"  {label}: finished status={job.status}")
    return _job_record(job)


def _cached_ok(path: Path, force: bool) -> dict[str, Any] | None:
    if force:
        return None
    record = _read_json(path)
    if record and record.get("status") == "succeeded" and record.get("result") is not None:
        return record
    return None


def _page_of(location: dict[str, Any] | None) -> int | None:
    if not isinstance(location, dict):
        return None
    page = location.get("page")
    return page if isinstance(page, int) else None


def _all_statements(extract_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Statements from every page window, each with a target id unique across windows; exact duplicates dropped."""
    statements, seen = [], set()
    for window, record in enumerate(extract_records):
        for st in _extracted_statements(record["result"]):
            key = " ".join(st["text"].lower().split())
            if key in seen:
                continue
            seen.add(key)
            st["target_id"] = f"w{window}s{st['index']}"
            st["extract_job_id"] = record.get("job_id")
            statements.append(st)
    return statements


def _extracted_statements(extract_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Join `data.statements[i]` with `fields['/statements/i']` citations."""
    items = (extract_result.get("data") or {}).get("statements") or []
    fields = {f.get("path"): f for f in extract_result.get("fields") or [] if isinstance(f, dict)}
    statements = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        text = (item.get("text") or "").strip()
        topic = item.get("topic")
        if not text or topic not in GUIDANCE_TOPICS:
            continue
        field = fields.get(f"/statements/{index}") or {}
        citations = [c for c in field.get("citations") or [] if isinstance(c, dict)]
        statements.append(
            {
                "index": index,
                "text": text,
                "topic": topic,
                "extract_status": field.get("status"),
                "extract_citations": citations,
            }
        )
    return statements


def build_passages(
    doc: dict[str, Any],
    parse_record: dict[str, Any],
    extract_records: list[dict[str, Any]],
    ground_records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (accepted passages, rejected statements) using only NDI-produced text and locations."""
    statements = _all_statements(extract_records)
    ground_by_id: dict[str, dict[str, Any]] = {}
    ground_job_by_id: dict[str, str] = {}
    for record in ground_records:
        for target in (record.get("result") or {}).get("targets") or []:
            ground_by_id[target.get("id")] = target
            ground_job_by_id[target.get("id")] = record.get("job_id")

    accepted, rejected = [], []
    for st in statements:
        target_id = st["target_id"]
        target = ground_by_id.get(target_id) or {}
        matches = target.get("matches") or []
        best = matches[0] if matches else None
        provenance = {
            "parse_job_id": parse_record.get("job_id"),
            "extract_job_id": st.get("extract_job_id"),
            "ground_job_id": ground_job_by_id.get(target_id),
            "ground_status": target.get("status"),
        }
        if target.get("status") == "found" and best and (best.get("matched_text") or "").strip():
            page = _page_of(best.get("location"))
            accepted.append(
                {
                    "passage_id": f"{doc['document_id']}#{target_id}",
                    "document_id": doc["document_id"],
                    "title": doc["title"],
                    "source": doc["url"],
                    "topic": st["topic"],
                    "passage_text": best["matched_text"].strip(),
                    "page": page,
                    "status": "grounded",
                    "match_method": best.get("match_method"),
                    "ndi": provenance,
                }
            )
            continue
        cited = next((c for c in st["extract_citations"] if (c.get("source_text") or "").strip()), None)
        if cited is not None:
            accepted.append(
                {
                    "passage_id": f"{doc['document_id']}#{target_id}",
                    "document_id": doc["document_id"],
                    "title": doc["title"],
                    "source": doc["url"],
                    "topic": st["topic"],
                    "passage_text": cited["source_text"].strip(),
                    "page": _page_of(cited.get("location")),
                    "status": "parsed",
                    "match_method": None,
                    "ndi": provenance,
                }
            )
            continue
        rejected.append({"statement": st["text"], "reason": "not located by Ground and no Extract citation text"})
    return accepted, rejected


def ingest_document(client: Any, doc: dict[str, Any], cache_dir: Path, force: bool, parse_only: bool) -> dict[str, Any]:
    from nace_sdk import ParseResultSource, UrlSource

    doc_dir = cache_dir / doc["document_id"]
    print(f"[{doc['document_id']}] {doc['title']}")

    parse_path = doc_dir / "parse.json"
    parse_record = _cached_ok(parse_path, force)
    if parse_record:
        print(f"  parse: reusing cached job {parse_record['job_id']}")
    else:
        parse_record = _run_job(
            client,
            lambda: client.documents.parse(
                UrlSource(url=doc["url"], file_name=doc["file_name"]), wait_seconds=60
            ),
            "parse",
        )
        _write_json(parse_path, parse_record)
    page_count = ((parse_record["result"] or {}).get("document") or {}).get("page_count")
    print(f"  parse: page_count={page_count}")
    if parse_only:
        return {"document_id": doc["document_id"], "parse_job_id": parse_record["job_id"], "page_count": page_count}

    parsed_source = ParseResultSource(job_id=parse_record["job_id"])

    # Extract per page window so statements come from the whole document, not just its opening pages.
    windows = [
        {"start": start, "end": min(start + EXTRACT_WINDOW_PAGES - 1, page_count or start)}
        for start in range(1, (page_count or 1) + 1, EXTRACT_WINDOW_PAGES)
    ]
    extract_path = doc_dir / "extract.json"
    cached = None if force else _read_json(extract_path)
    if (
        isinstance(cached, list)
        and [r.get("page_range") for r in cached] == windows
        and all(r.get("status") == "succeeded" and r.get("result") is not None for r in cached)
    ):
        extract_records = cached
        print(f"  extract: reusing {len(extract_records)} cached job(s)")
    else:
        extract_records = []
        for window in windows:
            record = _run_job(
                client,
                lambda window=window: client.documents.extract(
                    parsed_source,
                    schema=EXTRACT_SCHEMA,
                    instructions=EXTRACT_INSTRUCTIONS,
                    page_ranges=[window],
                    citations={"enabled": True, "include_source_text": True},
                    wait_seconds=60,
                ),
                f"extract[p{window['start']}-{window['end']}]",
            )
            record["page_range"] = window
            extract_records.append(record)
        _write_json(extract_path, extract_records)

    statements = _all_statements(extract_records)
    print(f"  extract: {len(statements)} statements across {len(extract_records)} page window(s)")

    ground_path = doc_dir / "ground.json"
    ground_records = None if force else _read_json(ground_path)
    expected_ids = {st["target_id"] for st in statements}
    cached_ids = {
        t.get("id") for r in (ground_records or []) for t in ((r.get("result") or {}).get("targets") or [])
    }
    if ground_records and all(r.get("status") == "succeeded" for r in ground_records) and expected_ids <= cached_ids:
        print(f"  ground: reusing {len(ground_records)} cached job(s)")
    else:
        ground_records = []
        targets = [{"id": st["target_id"], "text": st["text"]} for st in statements]
        for start in range(0, len(targets), GROUND_BATCH):
            batch = targets[start : start + GROUND_BATCH]
            ground_records.append(
                _run_job(
                    client,
                    lambda batch=batch: client.documents.ground(
                        parsed_source, targets=batch, options={"max_matches": 1}, wait_seconds=60
                    ),
                    f"ground[{start}:{start + len(batch)}]",
                )
            )
        _write_json(ground_path, ground_records)

    passages, rejected = build_passages(doc, parse_record, extract_records, ground_records)
    grounded = sum(1 for p in passages if p["status"] == "grounded")
    print(f"  passages: {len(passages)} accepted ({grounded} grounded), {len(rejected)} rejected")
    return {
        "document_id": doc["document_id"],
        "title": doc["title"],
        "source": doc["url"],
        "page_count": page_count,
        "parse_job_id": parse_record["job_id"],
        "extract_job_ids": [r["job_id"] for r in extract_records],
        "ground_job_ids": [r["job_id"] for r in ground_records],
        "passages": passages,
        "rejected": rejected,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest guidance PDFs with Nace NDI once.")
    parser.add_argument("--only", action="append", help="document_id to process (repeatable)")
    parser.add_argument("--force", action="store_true", help="re-run NDI jobs even if cached")
    parser.add_argument("--parse-only", action="store_true", help="run only the Parse job (connectivity check)")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.nace_configured:
        print("NACE_API_KEY is not set (expected a nace_sk_ key in the environment or .env).", file=sys.stderr)
        return 2

    from nace_sdk import NaceClient, NaceError

    manifest = _read_json(settings.evidence_manifest)
    docs = [d for d in manifest["documents"] if d.get("enabled")]
    if args.only:
        docs = [d for d in docs if d["document_id"] in set(args.only)]
    if not docs:
        print("No documents selected.", file=sys.stderr)
        return 2

    cache_dir = settings.evidence_cache_dir
    reports, failures = [], []
    with NaceClient(client_info="nearmiss-ai-ingest/0.1") as client:
        for doc in docs:
            try:
                reports.append(ingest_document(client, doc, cache_dir, args.force, args.parse_only))
            except (NaceError, RuntimeError) as error:
                print(f"  FAILED: {type(error).__name__}: {error}", file=sys.stderr)
                failures.append({"document_id": doc["document_id"], "error": f"{type(error).__name__}: {error}"})

    if args.parse_only:
        print(json.dumps(reports, indent=2))
        return 1 if failures else 0

    # Merge with previously ingested documents not selected in this run.
    store_path = cache_dir / "passages.json"
    previous = _read_json(store_path) or {"documents": []}
    by_id = {d["document_id"]: d for d in previous.get("documents", [])}
    for report in reports:
        by_id[report["document_id"]] = report
    store = {
        "generated_at": _now(),
        "pipeline": "Nace NDI Parse -> Extract -> Ground (nace-sdk)",
        "documents": list(by_id.values()),
        "failures": failures,
    }
    _write_json(store_path, store)
    total = sum(len(d["passages"]) for d in store["documents"])
    print(f"Wrote {store_path} with {total} passages from {len(store['documents'])} document(s).")
    return 1 if failures and not reports else 0


if __name__ == "__main__":
    raise SystemExit(main())
