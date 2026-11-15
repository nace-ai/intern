"""MCP server (stdio) that exposes the jury engine as tools, so Intern, or any MCP client, can run it.

    python3 -m engine.mcp_server            # speaks JSON-RPC 2.0, one message per line, on stdin and stdout

Tools:
    list_cases      the invented cases a trial can run on
    list_roles      the practice-area roles and what each one changes
    find_statutes   LawWiki sections for a question: a role's curated sections, then a search inside its codes
    get_statute     one section's text
    estimate_trial  requests, decisions, input tokens and cost of a trial, without any call
    run_trial       run the 12-juror trial: verdict, vote, what moves it, suggested next step, cost

Intern connects with a `[[capabilities.mcp]]` entry (intern/config.example.toml). Any other MCP client
starts it with `python3 -m engine.mcp_server` from the repository folder.

`run_trial` calls Drex (and NDI for a document) when NACE_API_KEY is set and `simulate` is false; a live trial
costs about $0.005 to $0.01 of Drex input tokens. Results are legal information, not legal advice.
Standard library only. Nothing but protocol messages is written to stdout.

When TAD_SESSION_DIR names a folder (engine/intern_runner.py sets it), every tool call is appended to
<folder>/mcp_calls.jsonl and the first trial's run id is written to <folder>/trial_run_id as soon as the trial
starts, so the caller can follow the trial's events while Intern is still waiting for the result.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from . import cases, config, lawstore, lawwiki, roles, trial

PROTOCOL = "2025-06-18"
TEXT_LIMIT = 6000  # characters of statute text returned by get_statute


def _schema(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


CASE = {"type": "string", "enum": [c["id"] for c in cases.all_cases()], "description": "Case id from list_cases"}
ROLE = {"type": "string", "enum": list(roles.ROLES), "description": "Practice-area role id from list_roles"}
JUR = {"type": "string", "enum": ["CA", "FL", "NY", "TX", "US"], "description": "Jurisdiction"}
TOOLS = [
    {"name": "list_cases", "description": "List the invented cases a mock jury trial can run on, with jurisdiction, role and the question asked.",
     "inputSchema": _schema({})},
    {"name": "list_roles", "description": "List the practice-area roles (corporate, criminal defense, prosecution, personal injury, ...): "
     "what the jury is asked, the standard of proof and the next-step options of each.", "inputSchema": _schema({})},
    {"name": "find_statutes", "description": "Find LawWiki statute sections for a legal question in one jurisdiction. Returns the role's "
     "curated sections, then full-text search hits limited to that role's codes. Reads a local copy of LawWiki: no network.",
     "inputSchema": _schema({"question": {"type": "string", "description": "The dispute or question in plain words"}, "jurisdiction": JUR,
                             "role": ROLE, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, ["question", "jurisdiction"])},
    {"name": "get_statute", "description": "Text of one LawWiki section. Key format JUR/CODE/SECTION, for example CA/LAB/203 or US/USC-17/504.",
     "inputSchema": _schema({"key": {"type": "string"}}, ["key"])},
    {"name": "estimate_trial", "description": "Count the requests, decisions, input tokens and Drex cost of a trial without making any call.",
     "inputSchema": _schema({"case": CASE, "role": ROLE}, ["case"])},
    {"name": "run_trial", "description": "Run the 12-juror mock trial for a case. Every decision is a Drex probability. Returns the verdict, "
     "the vote, which fact moves the vote most, the suggested next step and the cost. `document` is the path of an uploaded file to read "
     "with NDI. `flip` lists fact ids to assume the other way. `simulate` true makes no paid call.",
     "inputSchema": _schema({"case": CASE, "role": ROLE, "flip": {"type": "array", "items": {"type": "string"}},
                             "document": {"type": "string"}, "simulate": {"type": "boolean"}}, ["case"])},
]


def list_cases(_: dict) -> dict:
    return {"cases": [{"id": c["id"], "title": c["title"], "jurisdiction": c["jurisdiction"], "forum": c["forum"], "role": c.get("practice"),
                       "asker": c["asker"]["who"], "question": c["ask"], "claim": c["claim"], "upload": c["upload"]["name"],
                       "facts": [{"id": f["id"], "label": f["label"], "true_now": f["on"]} for f in c["facts"]]} for c in cases.all_cases()]}


def list_roles(_: dict) -> dict:
    return {"groups": roles.GROUPS, "roles": [{"id": rid, "label": r["label"], "group": roles.GROUPS[r["group"]], "team": r["team"],
                                               "needs": r["lens"], "jurors_decide": r["finding"], "standard_of_proof": r["burden"],
                                               "next_steps": r["next_steps"], "jurisdictions": sorted(r["scope"])} for rid, r in roles.ROLES.items()]}


def _brief(rec: dict, why: str) -> dict:
    return {"key": rec["key"] if "key" in rec else f"{rec['jurisdiction']}/{rec['code']}/{rec['section']}", "citation": rec["citation"],
            "title": rec.get("title") or "", "url": lawwiki.url_for(rec), "why": why}


def find_statutes(a: dict) -> dict:
    jur, rid, limit = a["jurisdiction"], a.get("role"), int(a.get("limit") or 8)
    out, seen = [], set()
    if rid:
        for key in roles.anchors(rid, jur):
            rec = lawwiki.statute(key)
            if rec:
                seen.add(key)
                out.append(_brief({**rec, "key": key}, f"curated for {roles.ROLES[rid]['label']}"))
    scope = roles.scope(rid, jur) if rid else None
    for h in lawstore.search(a["question"], jur, scope, limit):
        if h["key"] not in seen:
            out.append({**_brief(h, "search" + (f" inside {', '.join(scope)}" if scope else "")), "rank": h["rank"]})
    return {"jurisdiction": jur, "role": rid, "store": "local LawWiki store" if lawstore.available() else "fixtures only (store not built)",
            "sections": out}


def get_statute(a: dict) -> dict:
    rec = lawwiki.statute(a["key"])
    if not rec:
        raise ValueError(f"no section {a['key']}")
    text = rec.get("text") or ""
    return {"key": a["key"], "citation": rec["citation"], "title": rec.get("title"), "url": lawwiki.url_for(rec),
            "source_version": rec.get("source_version"), "chars": len(text), "text": text[:TEXT_LIMIT], "truncated": len(text) > TEXT_LIMIT}


def estimate_trial(a: dict) -> dict:
    e = trial.estimate(a["case"], role=a.get("role"))
    return {**e, "note": f"cost = input tokens x ${config.usd_per_m(config.MODEL_TEXT):.2f} per million; a document read by NDI is extra"}


def _session() -> Path | None:
    path = os.environ.get("TAD_SESSION_DIR", "").strip()
    return Path(path) if path and Path(path).is_dir() else None


def _link(run: "trial.Trial") -> None:
    """Tell the harness which run folder this trial writes to (first trial of a session only)."""
    session = _session()
    if not session or not run.run_dir:
        return
    try:
        with open(session / "trial_run_id", "x", encoding="utf-8") as fh:
            fh.write(run.run_id + "\n")
        with open(run.run_dir / "session", "x", encoding="utf-8") as fh:
            fh.write(session.name + "\n")
    except FileExistsError:
        pass


_FINISHED: dict[str, dict] = {}   # inside one Intern session: arguments -> the result of the trial already run


DOCUMENT_TYPES = {".pdf", ".png", ".jpg", ".jpeg"}


def _document(doc) -> str | None:
    """The caller is a model and the file is sent to NDI: accept only a PDF or an image inside cases/ or the runs folder."""
    if not doc:
        return None
    path = Path(str(doc)).expanduser().resolve()
    roots = [(config.ROOT / "cases").resolve(), config.RUNS_DIR.resolve()]
    if path.suffix.lower() not in DOCUMENT_TYPES or not any(path.is_relative_to(r) for r in roots):
        raise ValueError("document must be a PDF or an image inside the cases or runs folder")
    if not path.is_file():
        raise ValueError(f"document not found: {doc}")
    return str(path)


def run_trial(a: dict) -> dict:
    doc = _document(a.get("document"))
    same = json.dumps({k: a.get(k) for k in ("case", "flip", "document", "role", "simulate")}, sort_keys=True, default=str)
    if _session() and same in _FINISHED:   # the harness asked again (its call timed out, or the model repeated itself)
        return {**_FINISHED[same], "note": "This trial already ran in this session. This is its result, not a second run."}
    run = trial.Trial(a["case"], a.get("flip") or None, True if a.get("simulate") else None, document=doc, role=a.get("role"),
                      intake={"who": "a legal team", "channel": "Slack, through Intern"} if _session() else None)
    _link(run)
    for _ in run.events():
        pass
    res = run.result
    s, t = res["summary"], res["summary"]["totals"]
    out = {"run_id": res["run_id"], "simulated": s["simulated"], "case": s["title"], "role": s["role"], "verdict": s["verdict"]["label"],
            "vote": s["vote"], "mean_probability": round(s["mean"], 3), "spread": round(s["spread"], 3), "outcome": s["verdict"]["tier"],
            "statutes_applied": s["statutes_applied"],
            "what_moves_the_vote": [{"fact": w["label"], "if_flipped": f"{w['liable_if']} of 12", "mean_if": round(w["mean_if"], 3),
                                     "delta": round(w["delta"], 3)} for w in s["what_if"]],
            "next_step": s["next_steps"] and {k: s["next_steps"][k] for k in ("step", "p_step", "for_side", "proof_gap", "needs_person")},
            "decisions": t["decisions"], "requests": t["requests"], "input_tokens": t["tokens"], "drex_usd": round(t["usd_billed"], 5),
            "documents_usd": round(t["ndi_usd"], 4), "sent_to_a_person": t["flagged"], "notes": s["notes"],
           "courtroom": f"{config.public_url()}/{config.COURTROOM_PAGE}#case={s['case']}", "disclaimer": trial.DISCLAIMER}
    if _session():
        _FINISHED[same] = out
    return out


HANDLERS = {"list_cases": list_cases, "list_roles": list_roles, "find_statutes": find_statutes, "get_statute": get_statute,
            "estimate_trial": estimate_trial, "run_trial": run_trial}


def handle(msg: dict) -> dict | None:
    """Answer one JSON-RPC message; None for a notification."""
    mid, method, params = msg.get("id"), msg.get("method", ""), msg.get("params") or {}
    if mid is None:  # notifications (initialized, cancelled) need no answer
        return None
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion") or PROTOCOL, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "twelve-angry-drexes", "version": "0.2.0"},
                  "instructions": "Mock jury trials on LawWiki statutes, decided by Drex. Legal information, not legal advice."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in HANDLERS:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {name!r}"}}
        t0 = time.monotonic()
        try:
            body, failed = HANDLERS[name](args), False
        except Exception as e:  # a tool failure is a result the model can read, not a protocol error
            body, failed = {"error": f"{type(e).__name__}: {e}"}, True
        session = _session()
        if session:   # the harness reads this file to show which tools the agent really called
            call = {"ts": int(time.time() * 1000), "tool": name, "args": {k: v for k, v in args.items() if isinstance(v, (str, int, float, bool))},
                    "ms": int((time.monotonic() - t0) * 1000), "ok": not failed}
            if failed:
                call["error"] = body["error"]
            with open(session / "mcp_calls.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(call, ensure_ascii=False) + "\n")
        result = {"content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}], "isError": failed}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            reply = handle(json.loads(line))
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
