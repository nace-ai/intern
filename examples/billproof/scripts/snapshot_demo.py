#!/usr/bin/env python3
"""Freeze the BillProof stage demo's API calls into static/snapshot/.

Calls the live server (default http://127.0.0.1:8800) with the same method,
path, query, and JSON body the UI sends, and writes the response bytes
unchanged. SSE streams become a JSON list of {event, data, t_ms}. The
manifest matches what static/js/snapshot.js reads.

Does not call live Drex (PA /live, whatif/live, or a new new_criterion run).
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE = "http://127.0.0.1:8800"
ROOT = Path(__file__).resolve().parents[1] / "src" / "billproof" / "server" / "static" / "snapshot"
PA_SLUGS = [f"PA-D{i}" for i in range(1, 6)]
RECORD_STATES = ("met", "not_met", "missing", "removed")
DIAL_BENCHES = ("hard", "pa_criteria_gym", "persius", "synthetic_v1")

_lock = threading.Lock()
entries: list[dict] = []
failures: list[str] = []
skipped: list[str] = []


def canon(body: object) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def body_sha(body: object) -> str:
    return hashlib.sha256(canon(body).encode()).hexdigest()


def js_t(step: int) -> str:
    """JS String(Math.round(t*100)/100) for t = (50+step)/100, step 0..50."""
    v = (50 + step) / 100
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def file_for(path: str, query: str, kind: str, sha: str | None) -> str:
    rel = path.removeprefix("/api/").strip("/")
    if kind == "binary":
        return rel
    suffix = ".sse.json" if kind == "sse" else ".json"
    tag = []
    if query:
        tag.append(query.replace("&", "_").replace("=", "-"))
    if sha:
        tag.append(sha[:12])
    name = rel if not tag else f"{rel}__{'__'.join(tag)}"
    return name + suffix


def _request(method: str, path: str, query: str, body: object | None, timeout: float):
    url = BASE + path + (("?" + query) if query else "")
    data = None
    headers = {"accept": "application/json, text/event-stream, */*"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        headers["content-type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.perf_counter()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        resp = exc
    return resp, t0


def _read_sse(resp, t0: float) -> list[dict]:
    events: list[dict] = []
    name = "message"
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal name, data_lines
        data = "\n".join(data_lines)
        if data:
            events.append({
                "event": name or "message",
                "data": data,
                "t_ms": int((time.perf_counter() - t0) * 1000),
            })
        name = "message"
        data_lines = []

    while True:
        raw = resp.readline()
        if not raw:
            flush()
            break
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line == "":
            flush()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            name = line[6:].lstrip()
        elif line.startswith("data:"):
            val = line[5:]
            if val.startswith(" "):
                val = val[1:]
            data_lines.append(val)
    return events


def record(
    method: str,
    path: str,
    query: str = "",
    body: object | None = None,
    *,
    kind: str | None = None,
    timeout: float = 90,
) -> tuple[int, bytes | None, dict | None]:
    """Perform one call and store it. Returns (status, raw_or_sse_bytes, parsed_json)."""
    sha = body_sha(body) if body is not None else None
    last_err = ""
    for attempt in (1, 2):
        try:
            resp, t0 = _request(method, path, query, body, timeout)
            break
        except Exception as exc:  # noqa: BLE001 — network, then one retry
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt == 1:
                time.sleep(0.4)
                continue
            msg = f"{method} {path}?{query} FAILED {last_err}"
            with _lock:
                failures.append(msg)
            print(msg, file=sys.stderr)
            return 0, None, None
    status = getattr(resp, "status", 0) or 0
    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    parsed = None
    content_type = None
    try:
        if "text/event-stream" in ctype:
            events = _read_sse(resp, t0)
            raw = json.dumps(events, ensure_ascii=False, separators=(",", ":")).encode()
            kind = "sse"
        else:
            raw = resp.read()
            if kind == "binary" or ctype.startswith("image/") or path.endswith(".png"):
                kind = "binary"
                content_type = ctype or "application/octet-stream"
            else:
                kind = "json"
                if raw:
                    try:
                        parsed = json.loads(raw)
                    except json.JSONDecodeError:
                        parsed = None
    finally:
        resp.close()

    rel = file_for(path, query, kind or "json", sha)
    dest = ROOT / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    entry: dict = {
        "method": method,
        "path": path,
        "query": query,
        "status": status,
        "kind": kind,
        "file": rel,
    }
    if body is not None:
        entry["body"] = body
        entry["body_sha256"] = sha
    if kind == "binary":
        entry["content_type"] = content_type
    with _lock:
        entries.append(entry)
        if status >= 400 or status == 0:
            failures.append(f"{method} {path}?{query} HTTP {status} -> {rel}")
    flag = "" if status < 400 else " !"
    print(f"  {status} {method} {path}{'?' + query if query else ''}{flag} {len(raw)}", flush=True)
    return status, raw, parsed


def get(path: str, query: str = "", **kw) -> tuple[int, bytes | None, dict | None]:
    return record("GET", path, query, None, **kw)


def post(path: str, body: object, query: str = "", **kw) -> tuple[int, bytes | None, dict | None]:
    return record("POST", path, query, body, **kw)


def qwen_dial(data: dict | None) -> bool:
    if not isinstance(data, dict):
        return False
    esc = data.get("escalate")
    model = str(data.get("escalate_model") or "")
    curve = data.get("curve") or data.get("points") or []
    return bool(esc) and esc != "gpt" and "qwen" in model.lower() and len(curve) > 0


def dial_curve(bench: str) -> str | None:
    """Mirror dial.js: try escalate=qwen, then deepinfra, else the GPT curve.

    Returns the escalate query value the UI keeps ("" for the GPT dial), or
    None when no curve was recorded.
    """
    status, _raw, data = get(f"/api/results/{bench}/dial", "escalate=qwen", timeout=120)
    if status == 200 and qwen_dial(data):
        return "qwen"
    status, _raw, data = get(f"/api/results/{bench}/dial", "escalate=deepinfra", timeout=120)
    if status == 200 and qwen_dial(data):
        return "deepinfra"
    status, _raw, data = get(f"/api/results/{bench}/dial", timeout=120)
    if status == 200 and isinstance(data, dict) and (data.get("curve") or data.get("points")):
        return ""
    return None


def dial_grid(bench: str, winner: str) -> None:
    """t in 0.50..1.00, the range routedAt clamps to, for the dial the UI keeps."""
    query_esc = f"escalate={winner}&" if winner else ""
    jobs = [f"{query_esc}t={js_t(step)}" for step in range(51)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(get, f"/api/results/{bench}/dial", q, timeout=120) for q in jobs]
        for fut in as_completed(futs):
            fut.result()


def pa_case(slug: str, *, edits: bool) -> dict | None:
    status, _raw, ev = get(f"/api/pa/{slug}")
    if status != 200 or not isinstance(ev, dict):
        return None
    for doc, key in (("policy", "policy_pages"), ("packet", "packet_pages")):
        pages = ev.get(key) or [1]
        for n in pages:
            get(f"/api/pa/{slug}/page/{doc}/{int(n)}.png", kind="binary")
    post(f"/api/pa/{slug}/whatif", {})
    if not edits:
        return ev
    records = [r.get("record") for r in (ev.get("records") or []) if r.get("kind") == "record" and r.get("record")]
    for rid in records:
        for state in RECORD_STATES:
            post(f"/api/pa/{slug}/whatif", {"records": {rid: state}})
    for conn in ("AND", "OR"):
        post(f"/api/pa/{slug}/whatif", {"connective": conn})
        policy_id = ev.get("policy_id")
        if policy_id:
            post("/api/cc/what_if", {
                "source": "pa_whatif",
                "case": slug,
                "policy_id": policy_id,
                "connective": conn,
                "off": [],
            })
    return ev


def load_manifest() -> None:
    """Keep every entry already on disk, including ones this run does not refetch."""
    path = ROOT / "manifest.json"
    if not path.is_file():
        return
    data = json.loads(path.read_text())
    prior = list(data.get("entries") or [])
    entries[:0] = prior
    print(f"kept {len(prior)} existing manifest entries")


def write_manifest() -> None:
    # Later entries win, so a refetch replaces its row. Keys this run never
    # touched (for example an earlier export's /case/{id} files) stay.
    by_key: dict[tuple, dict] = {}
    for e in entries:
        key = (e["method"], e["path"], e["query"], e.get("body_sha256") or "")
        by_key[key] = e
    entries[:] = list(by_key.values())
    ordered = sorted(entries, key=lambda e: (e["method"], e["path"], e["query"], e.get("body_sha256") or ""))
    # Stable field order matching the contract the UI reads.
    clean = []
    for e in ordered:
        row = {"method": e["method"], "path": e["path"], "query": e["query"]}
        if "body" in e:
            row["body"] = e["body"]
            row["body_sha256"] = e["body_sha256"]
        row["status"] = e["status"]
        row["kind"] = e["kind"]
        if e["kind"] == "binary":
            row["content_type"] = e.get("content_type") or "application/octet-stream"
        row["file"] = e["file"]
        clean.append(row)
    manifest = {"version": 1, "entries": clean}
    dest = ROOT / "manifest.json"
    dest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    total = sum(p.stat().st_size for p in ROOT.rglob("*") if p.is_file())
    print(f"manifest entries {len(clean)}  snapshot bytes {total}")


def main() -> int:
    global BASE
    args = sys.argv[1:]
    phase = "fast"
    if args and args[0] in ("fast", "rest", "all"):
        phase = args.pop(0)
    if args:
        BASE = args[0].rstrip("/")
    ROOT.mkdir(parents=True, exist_ok=True)
    load_manifest()
    print(f"snapshot {phase} {BASE} -> {ROOT}")

    winners: dict[str, str] = {}
    presets = None
    if phase in ("fast", "all"):
        status, _raw, summary = get("/api/results/summary")
        benches = []
        if status == 200 and isinstance(summary, dict):
            benches = [b.get("id") for b in (summary.get("benchmarks") or []) if b.get("id")]
        get("/api/demo/numbers")
        get("/api/cc/summary")
        get("/api/cc/policies")
        _st, _raw, presets = get("/api/cc/presets")
        get("/api/pa-browser")
        get("/api/pa/cases")
        _st, _raw, sessions = get("/api/intern/sessions")
        if isinstance(sessions, dict):
            for item in sessions.get("sessions") or []:
                sid = item.get("id")
                if sid:
                    get(f"/api/intern/session/{sid}")

        for slug in PA_SLUGS:
            pa_case(slug, edits=phase == "all")

        for bench in benches:
            get(f"/api/results/{bench}")
            get(f"/api/results/{bench}/cases", "offset=0&limit=50")

        for bench in benches:
            print(f"dial {bench}", flush=True)
            winner = dial_curve(bench)
            if winner is not None:
                winners[bench] = winner
        if phase == "fast":
            write_manifest()
            print(f"fast batch written ({len(entries)} entries); rest still to come")
            return 0
    else:
        by_path = {(e["path"], e["query"]): e for e in entries}
        for bench in DIAL_BENCHES:
            qwen = by_path.get((f"/api/results/{bench}/dial", "escalate=qwen"))
            if qwen and qwen.get("status") == 200:
                winners[bench] = "qwen"
                continue
            plain = by_path.get((f"/api/results/{bench}/dial", ""))
            if plain and plain.get("status") == 200:
                winners[bench] = ""

    if phase == "rest":
        for slug in PA_SLUGS:
            pa_case(slug, edits=True)
        _st, _raw, presets = get("/api/cc/presets")

    for bench, winner in winners.items():
        print(f"dial grid {bench} escalate={winner or 'gpt'}", flush=True)
        dial_grid(bench, winner)

    scenarios = (presets or {}).get("scenarios") or [] if isinstance(presets, dict) else []
    for preset in scenarios:
        pid = preset.get("id")
        if pid:
            post("/api/cc/what_if", {"preset": pid}, "stream=1", timeout=180)

    print("queue stream", flush=True)
    get("/api/cc/queue/stream", "full=1&speed=500", timeout=180)

    skipped.append(
        "POST /api/cc/what_if/new_criterion: not called. "
        "out/command_center/new_criteria.jsonl has 19 cached answers for two text hashes "
        "(546a3a0f7fcc, 10f8cd604ae4) and does not store the criterion text, so there is "
        "no complete recorded run to replay. Calling the route would ask Drex live."
    )
    skipped.append(
        "POST /api/pa/{case}/live and POST /api/pa/{case}/whatif/live are live Drex; not called. "
        "The Decide screen only shows them in Live mode."
    )
    skipped.append(
        "PA search is inside GET /api/pa/{case} (field search). The Find screen uses GET /api/pa-browser. "
        "The UI does not call /api/search/{id} or /api/browser/{id} (those routes only accept SYN-/SC- ids)."
    )
    skipped.append(
        "Case explorer: default page only (offset=0&limit=50, no lane, not only-wrong). "
        "Opening one case (GET /api/results/{bench}/case/{id}) is not the default page."
    )
    skipped.append(
        "Decide what-if covers the empty load, each record state (met, not_met, missing, removed), "
        "and connective AND and OR. Criterion-off and payer-flip are separate clicks and were not requested."
    )

    write_manifest()
    print("--- non-200 / failed ---")
    for line in failures:
        print(line)
    print("--- not snapshotted ---")
    for line in skipped:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
