"""Step 1: pull structured fields out of every CI log with Nace Document Intelligence (NDI).

Real path (NACE_API_KEY set): upload each .log with nace-sdk, then
POST /v1/documents/extract with an inline JSON schema (source = the uploaded
workspace_file). Fallback: a local regex extractor. Every case records which path
produced its fields ("ndi" or "local_regex") and why.

    python ndi_extract.py            # real if NACE_API_KEY is set, else local fallback
    python ndi_extract.py --mock     # force local fallback (labelled mock run)
"""
from __future__ import annotations

import argparse
import re
import time
from concurrent.futures import ThreadPoolExecutor

from drexflaky.common import LOGS, load_env, load_labels, resolve_mode, result_path, write_json

SCHEMA = {
    "type": "object",
    "properties": {
        "test_name": {"type": "string", "description": "The failing test id from the 'failing test:' line."},
        "framework": {"type": "string", "description": "Test framework and version, e.g. 'pytest 8.3.2'."},
        "error_type": {"type": "string", "description": "Exception or error class of the failure, e.g. httpx.ReadTimeout, AssertionError, ImportError, TypeError, TestingLibraryElementError, SnapshotMismatch."},
        "error_message": {"type": "string", "description": "The first error line of the failure, verbatim, without the leading 'E'."},
        "duration_seconds": {"type": "number", "description": "Value of the 'duration:' line in seconds."},
        "retry_attempts": {"type": "integer", "description": "Number of automatic retry attempts listed under 'retry history'. 0 if none."},
        "retry_passed": {"type": "boolean", "description": "True if any retry attempt PASSED."},
        "recent_fail_rate": {"type": "number", "description": "recent_fail_rate value on the 'history:' line (0 to 1)."},
        "changed_files": {"type": "array", "items": {"type": "string"}, "description": "Paths listed under 'changed files in this PR'."},
    },
    "required": ["test_name", "error_type", "duration_seconds", "retry_passed", "recent_fail_rate"],
}
INSTRUCTIONS = "This is a CI test-failure log. Fill each field from the log text only; do not guess."


def local_extract(text: str) -> dict:
    def grab(pat, cast=str, default=None):
        m = re.search(pat, text, re.M)
        return cast(m.group(1)) if m else default
    err_line = grab(r"^E\s+(\S.*)$") or grab(r"^\s{4}(TypeError: .*|TestingLibraryElementError: .*)$") \
        or grab(r"^\s{4}(expect\(received\)\.toMatchSnapshot\(\))$")
    if err_line and "toMatchSnapshot" in err_line:
        etype = "SnapshotMismatch"
    else:
        etype = (re.match(r"([\w.]+(?:Error|Exception|Timeout|Mismatch))", err_line or "") or [None, "AssertionError" if (err_line or "").startswith("assert") else "Unknown"])[1] if err_line else "Unknown"
    attempts = re.findall(r"^\s+attempt \d+: (\w+)", text, re.M)
    changed = re.findall(r"^\s+M (\S+)$", text, re.M)
    return {
        "test_name": grab(r"^failing test: (.+)$"),
        "framework": grab(r"^framework: (.+)$"),
        "error_type": etype,
        "error_message": err_line,
        "duration_seconds": grab(r"^duration: ([\d.]+)s", float, None),
        "retry_attempts": len(attempts),
        "retry_passed": any(a == "PASSED" for a in attempts),
        "recent_fail_rate": grab(r"recent_fail_rate=([\d.]+)", float, None),
        "changed_files": changed,
    }


def ndi_extract(client, path) -> dict:
    uploaded = client.documents.upload(str(path), file_name=path.name, on_conflict="new_version")
    job = client.documents.extract(uploaded.as_source(), schema=SCHEMA, instructions=INSTRUCTIONS, wait_seconds=60)
    if job.status != "succeeded":
        job = client.jobs.wait(job.job_id, timeout=180)
    data = job.result["data"] if isinstance(job.result, dict) else job.result.data
    cost = getattr(job, "cost", None)
    usd = float(cost.total_usd) if cost is not None and getattr(cost, "total_usd", None) is not None else None
    return {"fields": data, "job_id": str(job.job_id), "cost": usd}


def process(case, mode, client):
    path = LOGS / f"{case['case_id']}.log"
    text = path.read_text()
    t0 = time.perf_counter()
    rec = {"case_id": case["case_id"]}
    if mode == "real":
        try:
            out = ndi_extract(client, path)
            rec.update(path="ndi", fields=out["fields"], ndi_job_id=out["job_id"], ndi_cost=out["cost"])
        except Exception as e:  # noqa: BLE001 - fall back on any NDI failure
            rec.update(path="local_regex", fallback_reason=f"{type(e).__name__}: {str(e)[:200]}", fields=local_extract(text))
    else:
        rec.update(path="local_regex", fallback_reason="mock mode (no NACE_API_KEY)", fields=local_extract(text))
    rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    load_env()
    mode = resolve_mode(a.mock, "NACE_API_KEY")
    cases = load_labels()[: a.limit]
    client = None
    if mode == "real":
        from nace_sdk import NaceClient
        client = NaceClient()
    with ThreadPoolExecutor(a.workers if mode == "real" else 1) as ex:
        recs = list(ex.map(lambda c: process(c, mode, client), cases))
    used = {p: sum(r["path"] == p for r in recs) for p in ("ndi", "local_regex")}
    usd = sum(r.get("ndi_cost") or 0 for r in recs)
    # agreement with the local regex extractor (sanity check of NDI output)
    keys = ("error_type", "retry_passed", "recent_fail_rate", "duration_seconds")
    agree = {k: sum(str(r["fields"].get(k)) == str(local_extract((LOGS / f"{r['case_id']}.log").read_text()).get(k)) for r in recs) for k in keys}
    write_json(result_path("ndi", mode), {"mode": mode, "paths_used": used, "ndi_usd_total": usd,
                                          "agreement_with_regex": agree, "schema": SCHEMA, "cases": recs})
    print(f"  NDI cost ${usd:.4f}; agreement with regex: {agree}")
    print(f"[ndi_extract] mode={mode} paths={used} -> {result_path('ndi', mode)}")
    for r in recs:
        if r.get("fallback_reason") and mode == "real":
            print(f"  fallback {r['case_id']}: {r['fallback_reason']}")


if __name__ == "__main__":
    main()
