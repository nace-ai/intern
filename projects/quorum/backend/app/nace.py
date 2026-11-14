"""Thin async wrapper over nace-sdk: Drex (system_one) and NDI (upload/parse/extract).

Keys come from the environment (NACE_API_KEY) via the SDK. Every call is
counted in USAGE, which is appended to backend/data/usage.json on change.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import deque
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app import config

USAGE: dict[str, int] = {
    "drex_calls": 0,
    "drex_questions": 0,
    "drex_input_tokens": 0,
    "ndi_jobs": 0,
    "ndi_pages": 0,
    "search_calls": 0,
    "workspace_files": 0,
}

USAGE_KEYS: tuple[str, ...] = tuple(USAGE)

# One id per process, set at import; tags every usage.json line this process writes.
RUN_ID: str = uuid.uuid4().hex

_client: Any = None


def get_client() -> Any:
    """Return the one shared AsyncNaceClient (created lazily)."""
    global _client
    if _client is None:
        from nace_sdk import AsyncNaceClient, RetryPolicy

        # No SDK retry on 429 (or 403): a refusal goes to the breaker instead.
        kwargs: dict[str, Any] = {
            "timeout": 120.0,
            "retry": RetryPolicy(http_statuses={408, *range(500, 600)}),
        }
        base = os.environ.get("NACE_BASE_URL")
        if base:
            kwargs["base_url"] = base
        _client = AsyncNaceClient(**kwargs)
    return _client


def bump(usage_file: Path | None = None, **deltas: int) -> None:
    """Add deltas to USAGE and append a snapshot line to usage.json.

    usage.json is JSON lines; each line is {"ts", "run_id", **USAGE}, i.e. this
    process's cumulative counts so far. See usage_totals for reading.
    """
    changed = False
    for key, delta in deltas.items():
        if delta:
            USAGE[key] = USAGE.get(key, 0) + int(delta)
            changed = True
    if not changed:
        return
    target = usage_file or config.USAGE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    line = {"ts": datetime.now(timezone.utc).isoformat(), "run_id": RUN_ID, **USAGE}
    with target.open("a") as fh:
        fh.write(json.dumps(line) + "\n")


def _usage_entries(path: Path) -> list[dict[str, Any]]:
    """Dict entries of usage.json: JSON lines, or (old format) one JSON dict or list."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        whole = json.loads(text)
    except json.JSONDecodeError:
        whole = None
    if isinstance(whole, dict):
        return [whole]
    if isinstance(whole, list):
        return [e for e in whole if isinstance(e, dict)]
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _count(x: Any) -> int:
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


def usage_totals(path: Path | None = None) -> dict[str, int]:
    """Nace usage summed over all processes that wrote usage.json.

    Each line is a cumulative snapshot for one process. For each run_id the
    last line is taken, then the counters are summed over runs. Lines without
    run_id (old format, written before RUN_ID existed) are treated as one
    legacy run: only the last such line counts. A missing or unreadable file
    gives all zeros; bad lines are skipped.
    """
    target = path or config.USAGE_FILE
    last: dict[str | None, dict[str, Any]] = {}
    for entry in _usage_entries(Path(target)):
        rid = entry.get("run_id")
        last[str(rid) if rid else None] = entry
    totals = {k: 0 for k in USAGE_KEYS}
    for entry in last.values():
        for k in USAGE_KEYS:
            totals[k] += _count(entry.get(k))
    return totals


def _to_sdk_question(q: dict[str, Any]) -> Any:
    from nace_sdk import Choice, Noul

    if q.get("type") == "choice":
        return Choice(criteria=q["criteria"], instructions=q.get("instructions"))
    return Noul(instructions=q.get("instructions"), criteria=q.get("criteria"))


# ---------------------------------------------------------------- throttle / breaker

DREX_PER_MINUTE: int = 100  # free tier allows 120 per minute; keep a margin
DREX_WINDOW_S: float = 60.0  # rolling window for DREX_PER_MINUTE (tests shrink it)
BREAKER_THRESHOLD: int = 3  # refusals in a row that open the breaker
BREAKER_BLOCK_S: float = 120.0  # how long the breaker stays open

DREX_INFLIGHT: int = 0
DREX_INFLIGHT_MAX: int = 0

_sems: dict[str, tuple[Any, asyncio.Semaphore]] = {}
_drex_starts: deque[float] = deque()
_refusals: int = 0
_blocked_until: float = 0.0


def _now() -> float:
    return time.monotonic()


class NaceBlocked(Exception):
    """Raised without sending anything while the refusal breaker is open."""


def _semaphore(kind: str) -> asyncio.Semaphore:
    """One semaphore per kind ('drex' / 'ndi'), recreated when the event loop changes."""
    loop = asyncio.get_running_loop()
    held = _sems.get(kind)
    if held is None or held[0] is not loop:
        held = (loop, asyncio.Semaphore(config.CONCURRENCY))
        _sems[kind] = held
    return held[1]


def drex_inflight_max() -> int:
    return DREX_INFLIGHT_MAX


def reset_throttle() -> None:
    """Reset counters, the rate window and the breaker (for tests)."""
    global DREX_INFLIGHT, DREX_INFLIGHT_MAX, _refusals, _blocked_until
    DREX_INFLIGHT = DREX_INFLIGHT_MAX = 0
    _refusals = 0
    _blocked_until = 0.0
    _drex_starts.clear()
    _sems.clear()


def is_refusal(exc: BaseException) -> bool:
    """403/429, or an HTML page (e.g. a security checkpoint) as the body."""
    if getattr(exc, "status", None) in (403, 429):
        return True
    body = getattr(exc, "body", None)
    if isinstance(body, str):
        head = body.lstrip()[:20].lower()
        return head.startswith("<!doctype html") or head.startswith("<html")
    return False


def _check_breaker() -> None:
    left = _blocked_until - _now()
    if left > 0:
        raise NaceBlocked(f"Nace refusing requests; breaker open for {left:.0f}s more")


def _record_refusal() -> None:
    global _refusals, _blocked_until
    _refusals += 1
    if _refusals >= BREAKER_THRESHOLD:
        _blocked_until = _now() + BREAKER_BLOCK_S
        _refusals = 0


def _record_success() -> None:
    global _refusals
    _refusals = 0


async def _rate_slot() -> None:
    """Wait until fewer than DREX_PER_MINUTE requests started in the last window."""
    while True:
        now = _now()
        while _drex_starts and now - _drex_starts[0] >= DREX_WINDOW_S:
            _drex_starts.popleft()
        if len(_drex_starts) < DREX_PER_MINUTE:
            _drex_starts.append(now)
            return
        await asyncio.sleep(max(0.001, _drex_starts[0] + DREX_WINDOW_S - now))


async def drex(
    state: dict[str, Any], questions: dict[str, dict[str, Any]], model: str | None = None
) -> dict[str, Any]:
    """One Drex call. Returns raw answers, usage, evaluation_time_ms, request_id, wall_ms.

    At most config.CONCURRENCY in flight and DREX_PER_MINUTE per window; raises
    NaceBlocked without sending while the refusal breaker is open.
    """
    global DREX_INFLIGHT, DREX_INFLIGHT_MAX
    _check_breaker()
    client = get_client()
    sdk_questions = {qid: _to_sdk_question(q) for qid, q in questions.items()}
    async with _semaphore("drex"):
        _check_breaker()
        await _rate_slot()
        _check_breaker()
        DREX_INFLIGHT += 1
        DREX_INFLIGHT_MAX = max(DREX_INFLIGHT_MAX, DREX_INFLIGHT)
        t0 = time.perf_counter()
        try:
            resp = await client.system_one(
                state, sdk_questions, model=model or config.DREX_MODEL
            )
        except Exception as exc:
            if is_refusal(exc):
                _record_refusal()
            raise
        else:
            _record_success()
        finally:
            DREX_INFLIGHT -= 1
            bump(drex_calls=1, drex_questions=len(questions))
    wall_ms = int((time.perf_counter() - t0) * 1000)
    usage = resp.usage.model_dump() if resp.usage is not None else {}
    bump(drex_input_tokens=int(usage.get("input_tokens") or 0))
    return {
        "model": resp.model,
        "answers": {k: v.model_dump() for k, v in resp.answers.items()},
        "usage": usage,
        "evaluation_time_ms": resp.evaluation_time_ms,
        "request_id": getattr(resp, "request_id", None),
        "wall_ms": wall_ms,
    }


async def _finish(job: Any) -> Any:
    if job.status != "succeeded":
        job = await get_client().jobs.wait(job.job_id)
    return job


def _error_text(err: Any) -> str | None:
    """Job error as text (code and message when present), or None."""
    if not err:
        return None
    if isinstance(err, dict):
        parts = [str(err[k]) for k in ("code", "message") if err.get(k)]
        return ": ".join(parts) or str(err)
    code, message = getattr(err, "code", None), getattr(err, "message", None)
    if code or message:
        return ": ".join(str(x) for x in (code, message) if x)
    return str(err)


def _pages(result: Any) -> int:
    if isinstance(result, dict):
        for key in ("page_count", "num_pages", "pages"):
            val = result.get(key)
            if isinstance(val, int):
                return val
            if isinstance(val, list):
                return len(val)
    return 1


async def ndi_extract(
    path: str | Path, schema: dict[str, Any], instructions: str | None = None
) -> dict[str, Any]:
    """Upload -> parse -> extract (parse_result source). Returns raw result + parse_ms."""
    async with _semaphore("ndi"):
        return await _ndi_extract(path, schema, instructions)


async def _ndi_extract(
    path: str | Path, schema: dict[str, Any], instructions: str | None = None
) -> dict[str, Any]:
    client = get_client()
    p = Path(path)
    t0 = time.perf_counter()
    uploaded = await client.documents.upload(
        p, path=f"quorum/{p.name}", on_conflict="new_version"
    )
    parse_job = await client.documents.parse(uploaded.as_source(), wait_seconds=60)
    bump(ndi_jobs=1)
    parse_job = await _finish(parse_job)
    bump(ndi_pages=_pages(parse_job.result))
    extract_job = await client.documents.extract(
        {"type": "parse_result", "job_id": parse_job.job_id},
        schema=schema,
        instructions=instructions,
        citations={"enabled": True, "include_source_text": True},
        wait_seconds=60,
    )
    bump(ndi_jobs=1)
    extract_job = await _finish(extract_job)
    parse_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "parse_job_id": parse_job.job_id,
        "extract_job_id": extract_job.job_id,
        "status": extract_job.status,
        "error": _error_text(getattr(extract_job, "error", None)),
        "result": extract_job.result,
        "parse_ms": parse_ms,
    }


# ---------------------------------------------------------------- workspaces / search


async def create_workspace(name: str) -> str:
    """Create a workspace and return its id (not retried by the SDK)."""
    ws = await get_client().workspaces.create(name=name)
    return str(ws.workspace_id)


async def find_workspace(name: str) -> str | None:
    """Id of an active workspace with this name, or None."""
    page = await get_client().workspaces.list()
    for ws in getattr(page, "items", None) or []:
        if getattr(ws, "name", None) == name and getattr(ws, "status", "active") == "active":
            return str(ws.workspace_id)
    return None


async def upload_file(workspace_id: str, content: bytes, path: str) -> str:
    """Upload bytes to `path` in the workspace. Returns file_id.

    on_conflict="reject": an existing path is an error, so callers skip known paths.
    """
    file_name = path.rsplit("/", 1)[-1]
    uploaded = await get_client().documents.upload(
        content,
        path=path,
        file_name=file_name,
        on_conflict="reject",
        workspace_id=workspace_id,
    )
    bump(workspace_files=1)
    return str(uploaded.file_id)


async def list_files(workspace_id: str, path_prefix: str | None = None) -> dict[str, dict[str, Any]]:
    """All files under `path_prefix`: path -> {file_id, ingestion_status}."""
    client = get_client()
    files: dict[str, dict[str, Any]] = {}
    cursor: str | None = None
    while True:
        page = await client.workspaces.files.list(
            workspace_id, path_prefix=path_prefix, limit=100, cursor=cursor
        )
        for f in page.items:
            files[f.path] = {"file_id": f.file_id, "ingestion_status": f.ingestion_status}
        cursor = page.next_cursor
        if not cursor:
            return files


async def start_ingest(workspace_id: str, path_prefix: str | None = None) -> str:
    """Start one ingestion job over `path_prefix` (whole workspace if None). Returns job id."""
    job = await get_client().workspaces.ingest(
        workspace_id, path_prefix=path_prefix, wait_seconds=0
    )
    return str(job.job_id)


async def job_status(job_id: str) -> dict[str, Any]:
    """Read a job once: status, error text and result."""
    job = await get_client().jobs.get(job_id)
    return {
        "job_id": str(job.job_id),
        "status": job.status,
        "error": _error_text(getattr(job, "error", None)),
        "result": job.result,
    }


async def workspace_stats(workspace_id: str) -> dict[str, Any]:
    """Files per ingestion status, plus totals."""
    st = await get_client().workspaces.stats(workspace_id)
    return {
        "files_total": st.files_total,
        "files_by_ingestion_status": dict(st.files_by_ingestion_status or {}),
        "pages_ingested": st.pages_ingested,
    }


def _evidence(e: Any) -> dict[str, Any]:
    if not isinstance(e, dict):
        e = getattr(e, "model_dump", lambda: {})()
    return {k: e.get(k) for k in ("source_path", "page", "quote", "relevance")}


def parse_search_result(result: Any) -> dict[str, Any]:
    """Flatten an automatic-search result (fact or agent route) into one shape."""
    result = result if isinstance(result, dict) else {}
    route = (result.get("routing") or {}).get("final")
    output = result.get("output") or {}
    body = output.get("answer") if isinstance(output.get("answer"), dict) else output
    return {
        "route": route,
        "answer": body.get("answer"),
        "structured_answer": body.get("structured_answer"),
        "confidence": body.get("confidence"),
        "evidences": [_evidence(e) for e in body.get("evidences") or []],
        "exhausted": bool(body.get("exhausted")),
    }


async def search(
    workspace_id: str,
    query: str,
    mode: str = "agent",
    path_prefix: str | None = None,
    output_schema: dict[str, Any] | None = None,
    top_k: int | None = None,
    timeout: float = 600,
) -> dict[str, Any]:
    """One automatic search. Never raises for API/job failures: `error` is set instead."""
    client = get_client()
    t0 = time.perf_counter()
    job_id: str | None = None
    out: dict[str, Any] = {
        "mode": mode,
        "route": None,
        "answer": None,
        "structured_answer": None,
        "confidence": None,
        "evidences": [],
        "job_id": None,
        "status": "failed",
        "error": None,
    }
    try:
        bump(search_calls=1)
        job = await client.search.automatic(
            workspace_id,
            query=query,
            mode=mode,
            path_prefix=path_prefix,
            output_schema=output_schema,
            top_k=top_k,
            wait_seconds=60,
        )
        job_id = str(job.job_id)
        if job.status not in ("succeeded", "failed", "cancelled"):
            job = await client.jobs.wait(job_id, timeout=timeout, raise_on_failure=False)
        out["status"] = job.status
        if job.status == "succeeded":
            out.update(parse_search_result(job.result))
        else:
            out["error"] = _error_text(getattr(job, "error", None)) or f"job {job.status}"
    except Exception as err:  # recorded as a failure, never as an answer
        out["error"] = f"{type(err).__name__}: {err}"
        out["status"] = "failed"
    out["job_id"] = job_id
    out["wall_ms"] = int((time.perf_counter() - t0) * 1000)
    return out
