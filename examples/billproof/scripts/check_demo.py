#!/usr/bin/env python3
"""BillProof demo-readiness checker (W3b).

NOT a pytest suite: boots the real server (or attaches with --url), exercises
every CONTRACT.md §4 endpoint with real data, validates response shapes
(keys, types, int cents, probabilities in [0,1], answer choices within
question options), checks the UI's static assets, and prints a green/red
table with timings and the first error line per failure.

Status per check:
  PASS  valid response per the contract
  SKIP  contract-acceptable absence — another agent's data/module hasn't
        landed yet (404/503 JSON, file missing on disk, SSE `error` event)
  FAIL  something is broken (500, malformed shape, contract violation,
        missing file that exists on disk, timeout, crash)

Usage:
  cd billproof && .venv/bin/python scripts/check_demo.py [--url http://127.0.0.1:8704]
       [--port 8704] [--secs 3] [--require-all] [--no-color] [--json out.json]

Exit code: 0 = no FAILs. --require-all also fails on SKIP.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import httpx

BP_DIR = Path(__file__).resolve().parents[1]          # billproof/
SRC = BP_DIR / "src"
STATIC = SRC / "billproof" / "server" / "static"
VENV_UVICORN = BP_DIR / ".venv" / "bin" / "uvicorn"
DEMO_IDS = [f"SYN-D{i:02d}" for i in range(1, 6)]
TAMPER_OPS = [
    "duplicate_line", "inflate_units", "add_unbundled",
    "change_eob_resp", "shift_dos_after_death",
]
ACTIONS = {"OK", "DISPUTE", "APPEAL", "ASK_PATIENT", "INSUFFICIENT_EVIDENCE"}
Q_TYPES = {"noul", "choice", "score"}
ROUTES = {"drex_kept", "frontier_verified", "frontier_only", "human", "hard_cap"}
MODES = {"LIVE", "SIMULATED"}
LANES = {"frontier", "drex", "hybrid"}
LANE_CUM_KEYS = {"done", "correct_q", "total_q", "case_correct", "cases",
                 "accuracy", "time_ms", "cost_usd"}
IMR_FIELDS = {"case_id", "split", "year", "diagnosis_category",
              "treatment_category", "treatment_requested", "denial_type",
              "age_range", "plan_reason"}

PASS, SKIP, FAIL = "PASS", "SKIP", "FAIL"


class Skip(Exception):
    """Contract-acceptable absence: an upstream deliverable hasn't landed."""


@dataclass
class Result:
    name: str
    status: str = PASS
    ms: float = 0.0
    detail: str = ""


class Ctx:
    def __init__(self, client: httpx.Client, static: Path):
        self.http = client
        self.static = static
        self.results: list[Result] = []
        self.cases_by_split: dict[str, list[dict]] = {}
        self.demo_details: dict[str, dict] = {}   # case_id -> /api/cases/{id} body
        self.analyses: dict[str, dict] = {}       # case_id -> CaseAnalysis JSON
        self.live = False                          # print each result as it lands

    def check(self, name: str, fn) -> bool:
        r = Result(name)
        t0 = time.monotonic()
        try:
            fn()
            r.status = PASS
        except Skip as e:
            r.status = SKIP
            r.detail = str(e)
        except Exception as e:
            r.status = FAIL
            msg = f"{type(e).__name__}: {e}"
            r.detail = msg.strip().splitlines()[0] if msg.strip() else type(e).__name__
        r.ms = (time.monotonic() - t0) * 1000
        self.results.append(r)
        if self.live:
            det = f"  {r.detail[:100]}" if r.detail else ""
            print(f"  [{r.status:4}] {name} ({r.ms:.0f} ms){det}", flush=True)
        return r.status == PASS

    def get(self, path: str, **kw) -> httpx.Response:
        return self.http.get(path, **kw)

    def jget(self, path: str, **kw):
        """GET expecting JSON; Skip on 404/503 JSON error bodies."""
        resp = self.get(path, **kw)
        if resp.status_code in (404, 503):
            try:
                body = resp.json()
            except Exception:
                body = {}
            if isinstance(body, dict) and ("error" in body or "missing" in body):
                raise Skip(f"{resp.status_code} {path}: {body.get('error', body)}")
        need(resp.status_code == 200, f"GET {path} -> {resp.status_code}: {resp.text[:160]}")
        try:
            return resp.json()
        except Exception:
            need(False, f"GET {path}: non-JSON body ({resp.text[:120]!r})")

    def get_bytes(self, path: str) -> bytes:
        resp = self.get(path)
        if resp.status_code in (404, 503):
            try:
                body = resp.json()
            except Exception:
                body = {}
            if isinstance(body, dict) and ("error" in body or "missing" in body):
                raise Skip(f"{resp.status_code} {path}: {body.get('error', body)}")
        need(resp.status_code == 200, f"GET {path} -> {resp.status_code}")
        need(len(resp.content) > 16, f"GET {path}: empty body")
        return resp.content


# ----------------------------------------------------------------- helpers


def need(cond, msg):
    if not cond:
        raise AssertionError(msg)


def jwalk_probs(obj, path="$", errs=None):
    """Collect contract violations: *_cents non-int, probabilities/confidence
    outside [0,1]. Returns list of error strings (bounded)."""
    errs = errs if errs is not None else []
    if len(errs) > 12:
        return errs
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}"
            if isinstance(k, str) and k.endswith("_cents") and v is not None:
                # citation dicts are keyed by field path (e.g. "bill.lines.1.charge_cents")
                # whose value is a Citation object, not money — only check scalars.
                if not isinstance(v, (dict, list)) and (not isinstance(v, int) or isinstance(v, bool)):
                    errs.append(f"{p}={v!r} not int cents")
            elif k in ("confidence", "probability") and v is not None:
                if not isinstance(v, (int, float)) or isinstance(v, bool) or not (0.0 <= v <= 1.0):
                    errs.append(f"{p}={v!r} outside [0,1]")
            elif k == "probabilities" and v is not None:
                if not isinstance(v, dict):
                    errs.append(f"{p} not an object")
                else:
                    for opt, pv in v.items():
                        if not isinstance(pv, (int, float)) or isinstance(pv, bool) or not (0.0 <= pv <= 1.0):
                            errs.append(f"{p}.{opt}={pv!r} outside [0,1]")
            jwalk_probs(v, p, errs)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:200]):
            jwalk_probs(v, f"{path}[{i}]", errs)
    return errs


def check_money_probs(name: str, body):
    errs = jwalk_probs(body)
    if errs:
        need(False, f"{name}: {errs[0]}"
             + (f" (+{len(errs) - 1} more)" if len(errs) > 1 else ""))


def keys(body: dict, required: list[str], where: str):
    missing = [k for k in required if k not in body]
    need(not missing, f"{where}: missing keys {missing}")


def question_options(q: dict) -> list[str]:
    t = q.get("type")
    if t == "noul":
        return ["yes", "no"]
    crit = q.get("criteria")
    if t == "choice":
        need(isinstance(crit, dict), f"question {q.get('name')}: choice criteria not dict")
        return list(crit)
    if t == "score":
        need(isinstance(crit, list), f"question {q.get('name')}: score criteria not list")
        return list(crit)
    need(False, f"question {q.get('name')}: bad type {t!r}")
    return []


def validate_analysis(body: dict, where: str):
    keys(body, ["case_id", "flags", "questions", "lanes", "verdicts", "letter", "gold"], where)
    need(isinstance(body["flags"], list), f"{where}: flags not a list")
    need(isinstance(body["questions"], list), f"{where}: questions not a list")
    need(isinstance(body["lanes"], dict), f"{where}: lanes not an object")
    need(isinstance(body["verdicts"], dict), f"{where}: verdicts not an object")

    opts = {}
    for q in body["questions"]:
        keys(q, ["name", "type", "instructions", "family"], f"{where}.questions")
        need(q["type"] in Q_TYPES, f"{where}: question {q['name']} bad type {q['type']!r}")
        opts[q["name"]] = question_options(q)

    for f in body["flags"]:
        keys(f, ["check", "fired", "detail", "rule_cite", "amount_cents"], f"{where}.flags")
        need(isinstance(f["fired"], bool), f"{where}: flag.fired not bool ({f.get('check')})")

    for lane, res in body["lanes"].items():
        need(lane in LANES, f"{where}: unknown lane {lane!r}")
        keys(res, ["lane", "model", "answers", "latency_ms"], f"{where}.lanes.{lane}")
        need(isinstance(res["answers"], dict), f"{where}.lanes.{lane}: answers not object")
        need(isinstance(res["latency_ms"], (int, float)), f"{where}.lanes.{lane}: latency_ms not number")
        for qn, a in res["answers"].items():
            keys(a, ["choice", "confidence"], f"{where}.lanes.{lane}.answers.{qn}")
            if qn in opts:
                need(a["choice"] in opts[qn],
                     f"{where}.{lane}.{qn}: choice {a['choice']!r} not in options {opts[qn]}")
            if a.get("route") is not None:
                need(a["route"] in ROUTES, f"{where}.{lane}.{qn}: bad route {a['route']!r}")

    for lane, v in body["verdicts"].items():
        need(lane in LANES, f"{where}: verdict for unknown lane {lane!r}")
        keys(v, ["case_action", "lines", "billed_balance_cents", "owed_cents",
                 "disputed_cents", "needs_human"], f"{where}.verdicts.{lane}")
        need(v["case_action"] in ACTIONS,
             f"{where}.verdicts.{lane}: bad case_action {v['case_action']!r}")
        need(isinstance(v["needs_human"], bool), f"{where}.verdicts.{lane}: needs_human not bool")
        for lv in v["lines"]:
            keys(lv, ["line_no", "action", "disputed_cents"], f"{where}.verdicts.{lane}.lines")
            need(lv["action"] in ACTIONS,
                 f"{where}.verdicts.{lane}.lines: bad action {lv['action']!r}")

    if body["letter"] is not None:
        lt = body["letter"]
        keys(lt, ["letter_type", "recipient", "subject", "clauses", "amount_cents"],
             f"{where}.letter")
        need(lt["letter_type"] in {"dispute", "appeal", "itemized_request", "none"},
             f"{where}.letter: bad letter_type {lt['letter_type']!r}")
        need(isinstance(lt["clauses"], list), f"{where}.letter: clauses not list")

    check_money_probs(where, body)


def validate_case_summary(row: dict, where: str):
    keys(row, ["case_id", "split", "title", "n_lines", "balance_cents",
               "setting", "has_eob", "has_denial"], where)
    need(isinstance(row["n_lines"], int), f"{where}: n_lines not int")
    need(isinstance(row["has_eob"], bool) and isinstance(row["has_denial"], bool),
         f"{where}: has_eob/has_denial not bool")


def is_clean(analysis: dict) -> bool:
    if not analysis:
        return False
    if any(f.get("fired") for f in analysis.get("flags", [])):
        return False
    vs = analysis.get("verdicts") or {}
    return bool(vs) and all(v.get("case_action") == "OK" for v in vs.values())


def parse_sse(raw: str):
    """Yield (event, data_dict) from an SSE text chunk accumulator."""
    for block in raw.split("\n\n"):
        ev, data = None, []
        for line in block.splitlines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if ev or data:
            payload = None
            if data:
                try:
                    payload = json.loads("\n".join(data))
                except Exception:
                    payload = "\n".join(data)
            yield ev or "message", payload


def collect_sse(ctx: Ctx, path: str, secs: float):
    """Read an SSE stream for ~secs seconds. Returns list of (event, data)."""
    events: list[tuple[str, object]] = []
    t0 = time.monotonic()
    try:
        with ctx.http.stream("GET", path) as resp:
            if resp.status_code != 200:
                need(False, f"GET {path} -> {resp.status_code}: "
                            f"{resp.read().decode(errors='replace')[:160]}")
            ct = resp.headers.get("content-type", "")
            need("event-stream" in ct, f"GET {path}: content-type {ct!r}, not SSE")
            buf = ""
            for chunk in resp.iter_text():
                buf += chunk
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    for ev, data in parse_sse(block + "\n\n"):
                        events.append((ev, data))
                if time.monotonic() - t0 > secs:
                    break
                if any(e == "finish" for e, _ in events):
                    break
    except (httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError):
        pass
    return events


# ------------------------------------------------------------------- checks


def c_root(ctx: Ctx):
    resp = ctx.get("/")
    need(resp.status_code == 200, f"GET / -> {resp.status_code}")
    need("<html" in resp.text.lower(), "GET /: not HTML")


def c_status(ctx: Ctx):
    body = ctx.jget("/api/status")
    keys(body, ["modes", "drex_model", "frontier_model", "label_sha256", "runs", "version"],
         "/api/status")
    need(isinstance(body["modes"], dict), "/api/status: modes not object")
    for svc in ("drex", "frontier", "ndi"):
        m = body["modes"].get(svc)
        need(m in MODES or m in ("LIVE", "SIMULATED", "REPLAY"),
             f"/api/status: modes.{svc}={m!r}")
    need(isinstance(body["label_sha256"], dict)
         and "synthetic" in body["label_sha256"] and "imr" in body["label_sha256"],
         "/api/status: label_sha256 missing synthetic/imr")
    need(isinstance(body["runs"], list), "/api/status: runs not list")
    for r in body["runs"]:
        rid = r.get("run_id", "")
        need(not rid.startswith(("dryrun_", "strays-")),
             f"/api/status: banned run {rid!r} listed")
    dr = body.get("default_runs")
    if dr is not None:
        for b in ("synthetic", "imr"):
            d = dr.get(b) or {}
            rid = d.get("run_id")
            if rid is not None:
                need(not rid.startswith(("dryrun_", "strays-")),
                     f"/api/status: default_runs.{b} picked banned run {rid!r}")


def c_cases(ctx: Ctx, split: str):
    body = ctx.jget(f"/api/cases?split={split}")
    need(isinstance(body, list), f"/api/cases?split={split}: not a list")
    ctx.cases_by_split[split] = body
    for row in body[:60]:
        validate_case_summary(row, f"/api/cases?split={split}")
    check_money_probs(f"/api/cases?split={split}", body)
    if split == "demo" and body:
        ids = {r["case_id"] for r in body}
        missing = [d for d in DEMO_IDS if d not in ids]
        if missing:
            raise Skip(f"demo split landed but {missing} still absent")


def _case_detail(ctx: Ctx, cid: str) -> dict:
    body = ctx.jget(f"/api/cases/{cid}")
    keys(body, ["case", "ndi", "pages"], f"/api/cases/{cid}")
    case = body["case"]
    keys(case, ["case_id", "split", "title", "patient", "provider", "bill",
                "plan", "service_setting"], f"/api/cases/{cid}.case")
    need(case["case_id"] == cid, f"/api/cases/{cid}: case_id mismatch")
    need(isinstance(body["pages"], dict), f"/api/cases/{cid}: pages not object")
    for doc, n in body["pages"].items():
        need(isinstance(n, int) and n >= 1, f"/api/cases/{cid}: pages.{doc}={n!r}")
    check_money_probs(f"/api/cases/{cid}", body)
    return body


def c_case_detail(ctx: Ctx, cid: str):
    all_rows = [r for rows in ctx.cases_by_split.values() for r in rows]
    if not any(r["case_id"] == cid for r in all_rows):
        raise Skip(f"{cid} not in cases list (not landed)")
    ctx.demo_details[cid] = _case_detail(ctx, cid)


def c_page_png(ctx: Ctx, cid: str, doc: str, page: int):
    blob = ctx.get_bytes(f"/api/cases/{cid}/page/{doc}/{page}.png?scale=2")
    need(blob[:8] == b"\x89PNG\r\n\x1a\n", f"page {doc}/{page}: not a PNG")
    need(len(blob) > 2000, f"page {doc}/{page}: suspiciously small ({len(blob)} B)")


def c_crop(ctx: Ctx, cid: str):
    detail = ctx.demo_details.get(cid) or _case_detail(ctx, cid)
    ctx.demo_details[cid] = detail
    case, pages = detail["case"], detail["pages"]
    cits = case.get("citations") or {}
    picked = None
    for k, c in cits.items():
        if c.get("doc") in pages and c.get("polygons") and c["polygons"][0]:
            picked = c
            break
    if picked is None:
        raise Skip(f"{cid}: no citation with polygons on a rendered doc")
    poly = urllib.parse.quote(json.dumps(picked["polygons"][0]))
    path = f"/api/cases/{cid}/crop?doc={picked['doc']}&page={picked.get('page', 1)}&poly={poly}&scale=2"
    blob = ctx.get_bytes(path)
    need(blob[:8] == b"\x89PNG\r\n\x1a\n", f"crop {picked['doc']}: not a PNG")


def c_analyze(ctx: Ctx, cid: str):
    resp = ctx.http.post("/api/analyze", json={"case_id": cid})
    if resp.status_code in (404, 503):
        try:
            body = resp.json()
        except Exception:
            body = {}
        if isinstance(body, dict) and ("error" in body or "missing" in body):
            raise Skip(f"{resp.status_code} /api/analyze {cid}: {body.get('error', body)}")
    need(resp.status_code == 200, f"POST /api/analyze {cid} -> {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    validate_analysis(body, f"analyze({cid})")
    ctx.analyses[cid] = body


def _analyze_or_skip(ctx: Ctx, cid: str) -> dict | None:
    resp = ctx.http.post("/api/analyze", json={"case_id": cid})
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except Exception:
        return None


def _pick_tamper_case(ctx: Ctx, op: str | None) -> str | None:
    """Prefer a clean case (verdict OK, no fired flags) that supports the op."""
    order = [r["case_id"] for s in ("demo", "test", "dev")
             for r in ctx.cases_by_split.get(s, [])]
    fallback = None
    for cid in order[:14]:
        detail = ctx.demo_details.get(cid)
        if detail is None:
            try:
                detail = _case_detail(ctx, cid)
                ctx.demo_details[cid] = detail
            except Skip:
                continue
        case = detail["case"]
        lines = case.get("bill", {}).get("lines") or []
        if op == "change_eob_resp" and not case.get("eob"):
            continue
        if op == "shift_dos_after_death" and not any(l.get("dos") for l in lines):
            continue
        if not lines:
            continue
        if fallback is None:
            fallback = cid
        an = ctx.analyses.get(cid) or _analyze_or_skip(ctx, cid)
        if an:
            ctx.analyses[cid] = an
            if is_clean(an):
                return cid
    return fallback


def _tamper(ctx: Ctx, cid: str, ops: list[dict]):
    resp = ctx.http.post("/api/tamper", json={"case_id": cid, "ops": ops})
    if resp.status_code in (404, 503):
        try:
            body = resp.json()
        except Exception:
            body = {}
        if isinstance(body, dict) and ("error" in body or "missing" in body):
            raise Skip(f"{resp.status_code} /api/tamper {ops}: {body.get('error', body)}")
    need(resp.status_code == 200,
         f"POST /api/tamper {ops} on {cid} -> {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    keys(body, ["case", "naive", "billproof", "gold", "caught"], "/api/tamper")
    need(isinstance(body["caught"], bool), "/api/tamper: caught not bool")
    need(isinstance(body["naive"], dict), "/api/tamper: naive not object")
    validate_analysis(body["billproof"], "/api/tamper.billproof")
    return body


def c_tamper_control(ctx: Ctx):
    cid = _pick_tamper_case(ctx, None)
    if cid is None:
        raise Skip("no case available for tamper control")
    _tamper(ctx, cid, [])


def c_tamper_op(ctx: Ctx, op: str):
    cid = _pick_tamper_case(ctx, op)
    if cid is None:
        raise Skip(f"no case supports tamper op {op}")
    line_no = 1
    detail = ctx.demo_details.get(cid)
    if detail:
        lines = detail["case"].get("bill", {}).get("lines") or []
        if lines:
            line_no = lines[0]["line_no"]
    _tamper(ctx, cid, [{"op": op, "line_no": line_no}])


def _pick_packet_case(ctx: Ctx) -> str | None:
    demo = ctx.cases_by_split.get("demo", [])
    for r in demo:
        if r.get("has_denial"):
            return r["case_id"]
    for s in ("demo", "test", "dev"):
        for r in ctx.cases_by_split.get(s, []):
            if r.get("has_denial"):
                return r["case_id"]
    return demo[0]["case_id"] if demo else None


def c_packet_json(ctx: Ctx):
    cid = _pick_packet_case(ctx)
    if cid is None:
        raise Skip("no cases for packet")
    body = ctx.jget(f"/api/packet/{cid}")
    keys(body, ["case_id", "verdict", "letter", "evidence", "owed_cents", "deadline"],
         "/api/packet")
    need(body["case_id"] == cid, "/api/packet: case_id mismatch")
    need(isinstance(body["evidence"], list), "/api/packet: evidence not list")
    if body["verdict"] is not None:
        need(body["verdict"].get("case_action") in ACTIONS,
             f"/api/packet: bad case_action {body['verdict'].get('case_action')!r}")
    for ev in body["evidence"][:3]:
        keys(ev, ["citation", "crop_url"], "/api/packet.evidence")
    if body["evidence"]:
        blob = ctx.get_bytes(body["evidence"][0]["crop_url"])
        need(blob[:8] == b"\x89PNG\r\n\x1a\n", "/api/packet: evidence crop not PNG")
    check_money_probs("/api/packet", body)
    ctx.analyses.setdefault("_packet_case", {"case_id": cid})


def c_packet_pdf(ctx: Ctx):
    cid = _pick_packet_case(ctx)
    if cid is None:
        raise Skip("no cases for packet pdf")
    resp = ctx.get(f"/api/packet/{cid}.pdf")
    if resp.status_code in (404, 503):
        try:
            body = resp.json()
        except Exception:
            body = {}
        if isinstance(body, dict) and ("error" in body or "missing" in body):
            raise Skip(f"{resp.status_code} /api/packet/{cid}.pdf: {body.get('error', body)}")
    need(resp.status_code == 200, f"GET /api/packet/{cid}.pdf -> {resp.status_code}")
    need(resp.content[:5] == b"%PDF-", "packet pdf: not a PDF")
    need(len(resp.content) > 2000, f"packet pdf: suspiciously small ({len(resp.content)} B)")


def _report(ctx: Ctx, bench: str) -> dict:
    return ctx.jget(f"/api/report?bench={bench}")


def c_report(ctx: Ctx, bench: str):
    body = _report(ctx, bench)
    keys(body, ["meta", "lanes"], "/api/report")
    need(isinstance(body["lanes"], dict) and body["lanes"], "/api/report: lanes empty")
    for lane, m in body["lanes"].items():
        for k in ("accuracy", "ci", "n"):
            need(k in m, f"/api/report.{bench}.{lane}: missing {k!r}")
        if m.get("accuracy") is not None:
            need(0.0 <= m["accuracy"] <= 1.0, f"/api/report.{bench}.{lane}: accuracy out of range")
        if m.get("ci") is not None:
            lo, hi = m["ci"]
            need(0.0 <= lo <= hi <= 1.0, f"/api/report.{bench}.{lane}: bad ci {m['ci']!r}")
    check_money_probs("/api/report", body)


def c_calibration(ctx: Ctx, bench: str):
    body = ctx.jget(f"/api/calibration?bench={bench}")
    keys(body, ["reliability", "sweep"], "/api/calibration")


def c_race(ctx: Ctx, source: str, secs: float):
    events = collect_sse(ctx, f"/api/race/stream?bench=synthetic&split=test&source={source}&speed=4", secs)
    if not events:
        raise Skip(f"race {source}: no SSE events within {secs}s")
    first_ev, first = events[0]
    if first_ev == "error" or (first_ev == "message" and isinstance(first, dict) and "error" in first):
        raise Skip(f"race {source}: {first.get('error') if isinstance(first, dict) else first}")
    need(first_ev == "start", f"race {source}: first event {first_ev!r}, expected 'start'")
    keys(first, ["run_id", "bench", "n_cases", "lanes", "label_sha256", "mode"], "race.start")
    kinds = [e for e, _ in events]
    need("decision" in kinds or "finish" in kinds,
         f"race {source}: only events {sorted(set(kinds))} (no decision/finish)")
    dec = next((d for e, d in events if e == "decision"), None)
    if dec is not None:
        keys(dec, ["lane", "case_id", "i", "correct", "case_correct", "latency_ms",
                   "cost_usd", "routes", "cum"], "race.decision")
        need(dec["lane"] in LANES, f"race.decision: bad lane {dec['lane']!r}")
        rk = set(dec["routes"])
        need({"drex_kept", "frontier_verified", "human"} <= rk,
             f"race.decision: routes keys {sorted(rk)}")
        for lane, cum in dec["cum"].items():
            missing = LANE_CUM_KEYS - set(cum)
            need(not missing, f"race.decision.cum.{lane}: missing {sorted(missing)}")
        hl = dec.get("highlight")
        if hl is not None:
            keys(hl, ["q", "choice", "p", "route", "correct"], "race.decision.highlight")
            if hl["p"] is not None:
                need(0.0 <= hl["p"] <= 1.0, f"race.decision.highlight: p={hl['p']!r} outside [0,1]")
            if hl["route"] is not None:
                need(hl["route"] in ROUTES, f"race.decision.highlight: bad route {hl['route']!r}")
    fin = next((d for e, d in events if e == "finish"), None)
    if fin is not None:
        keys(fin, ["summary"], "race.finish")


def c_imr_items(ctx: Ctx):
    body = ctx.jget("/api/imr/items?split=test")
    need(isinstance(body, list), "/api/imr/items: not a list")
    if not body:
        raise Skip("/api/imr/items: empty (W2 data not landed)")
    for it in body[:20]:
        missing = IMR_FIELDS - set(it)
        need(not missing, f"/api/imr/items: {it.get('case_id')} missing {sorted(missing)}")
    ctx.demo_details["_imr_first"] = body[0]["case_id"]


def c_imr_item(ctx: Ctx):
    cid = ctx.demo_details.get("_imr_first")
    if not cid:
        raise Skip("no imr items")
    body = ctx.jget(f"/api/imr/items/{cid}")
    need(body.get("case_id") == cid, f"/api/imr/items/{cid}: case_id mismatch")
    need("gold" not in body or body["gold"] is None,
         f"/api/imr/items/{cid}: gold leaked without ?gold=1")


def c_imr_item_gold(ctx: Ctx):
    cid = ctx.demo_details.get("_imr_first")
    if not cid:
        raise Skip("no imr items")
    body = ctx.jget(f"/api/imr/items/{cid}?gold=1")
    need(isinstance(body.get("gold"), dict) and body["gold"],
         f"/api/imr/items/{cid}?gold=1: gold missing")


def c_sibling_route(ctx: Ctx, method: str, path: str):
    """Route from an optional sibling router (audit_routes, ingest_routes).
    <500 proves it is mounted; 404/503 JSON error = upstream still landing."""
    if method == "POST":
        resp = ctx.http.post(path, json={})
    else:
        resp = ctx.get(path)
    if resp.status_code in (404, 503):
        try:
            body = resp.json()
        except Exception:
            body = {}
        if isinstance(body, dict) and ("error" in body or "missing" in body):
            raise Skip(f"{resp.status_code} {method} {path}: {body.get('error', body)}")
        if resp.status_code == 404:
            need(False, f"{method} {path} -> 404 (router file exists but route not mounted?)")
    need(resp.status_code < 500, f"{method} {path} -> {resp.status_code}: {resp.text[:160]}")


def c_ingest(ctx: Ctx):
    """POST a synthetic bill PDF; ingest recognizes it and returns its SYN id."""
    pdfs = sorted((BP_DIR / "data" / "synthetic" / "pdf").glob("SYN-0003_bill.pdf")) \
        or sorted((BP_DIR / "data" / "synthetic" / "pdf").glob("SYN-*_bill.pdf"))
    if not pdfs:
        raise Skip("no data/synthetic/pdf/SYN-*_bill.pdf on disk")
    pdf = pdfs[0]
    want = pdf.name.split("_")[0]
    resp = ctx.http.post(
        "/api/ingest",
        files={"bill": (pdf.name, pdf.read_bytes(), "application/pdf")},
        timeout=60,
    )
    if resp.status_code in (404, 503):
        try:
            body = resp.json()
        except Exception:
            body = {}
        if isinstance(body, dict) and ("error" in body or "missing" in body):
            raise Skip(f"{resp.status_code} POST /api/ingest: {body.get('error', body)}")
        need(False, "POST /api/ingest -> 404 (ingest_routes landed but not mounted?)")
    need(resp.status_code == 200, f"POST /api/ingest -> {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    keys(body, ["case_id", "mode", "fields_found", "warnings"], "/api/ingest")
    need(body["case_id"] == want,
         f"/api/ingest: recognized case_id {body['case_id']!r}, expected {want!r}")
    need(body["mode"] in ("LIVE", "SIMULATED"), f"/api/ingest: bad mode {body['mode']!r}")
    need(isinstance(body["warnings"], list), "/api/ingest: warnings not list")


def _sibling_routes(module_file: Path) -> list[tuple[str, str]]:
    """(method, path) pairs from a router module's decorators + prefix."""
    try:
        src = module_file.read_text()
    except Exception:
        return []
    m = re.search(r'APIRouter\(\s*prefix="([^"]+)"', src)
    prefix = m.group(1) if m else ""
    out = []
    for meth, p in re.findall(r'@router\.(get|post|put|delete)\(\s*"([^"]*)"', src):
        out.append((meth.upper(), prefix + p))
    return out


# ------------------------------------------------------------- UI static


def _ui_assets(ctx: Ctx) -> list[str]:
    resp = ctx.get("/")
    need(resp.status_code == 200, f"GET / -> {resp.status_code}")
    return sorted(set(re.findall(r'(?:href|src)="(/[^"#?]+)"', resp.text)))


def c_ui_asset(ctx: Ctx, path: str):
    disk = ctx.static / path.removeprefix("/static/")
    if path.startswith("/static/") and not disk.exists():
        raise Skip(f"{path}: file not on disk (upstream)")
    resp = ctx.get(path)
    need(resp.status_code == 200, f"GET {path} -> {resp.status_code}")
    need(len(resp.content) > 10, f"GET {path}: empty")
    ct = resp.headers.get("content-type", "")
    if path.endswith(".css"):
        need("css" in ct, f"{path}: content-type {ct!r}")
    elif path.endswith(".js"):
        need("javascript" in ct or "ecmascript" in ct or "text/" in ct,
             f"{path}: content-type {ct!r}")
    elif path.endswith(".svg"):
        need("svg" in ct, f"{path}: content-type {ct!r}")
    elif path.endswith(".png"):
        need(resp.content[:8] == b"\x89PNG\r\n\x1a\n", f"{path}: not a PNG")
    elif path.endswith(".woff2"):
        need(resp.content[:4] == b"wOF2", f"{path}: not woff2")


def c_ui_fonts(ctx: Ctx):
    fcss = ctx.static / "vendor" / "fonts" / "fonts.css"
    if not fcss.exists():
        raise Skip("vendor/fonts/fonts.css not on disk")
    urls = sorted(set(re.findall(r'url\((?:["\']?)([^)"\']+)', fcss.read_text())))
    if not urls:
        raise Skip("fonts.css has no url() refs")
    for u in urls:
        p = "/static/vendor/fonts/" + u.lstrip("./") if not u.startswith("/") else u
        c_ui_asset(ctx, p)


def c_ui_js_refs(ctx: Ctx):
    """Static paths referenced inside served JS files (e.g. /static/tour.json)."""
    refs = set()
    for js in ctx.static.rglob("*.js"):
        try:
            txt = js.read_text()
        except Exception:
            continue
        for m in re.findall(r'["\'](/static/[^"\']+)["\']', txt):
            if "${" in m or "*" in m:  # template/dynamic path, not a real file
                continue
            if not m.endswith((".js", ".css")):
                refs.add(m)
    missing_on_disk = [r for r in sorted(refs) if not (ctx.static / r.removeprefix("/static/")).exists()]
    for r in sorted(refs):
        if r in missing_on_disk:
            continue  # reported once by c_ui_js_refs_disk
        resp = ctx.get(r)
        need(resp.status_code == 200, f"GET {r} (referenced by JS) -> {resp.status_code}")
    if missing_on_disk:
        raise Skip(f"JS-referenced files not on disk: {', '.join(missing_on_disk)}")


def c_ui_js_syntax(ctx: Ctx):
    import shutil
    node = shutil.which("node")
    if not node:
        raise Skip("node not installed; cannot syntax-check JS")
    bad = []
    for js in sorted(ctx.static.rglob("*.js")):
        p = subprocess.run([node, "--check", str(js)], capture_output=True, text=True, timeout=15)
        if p.returncode != 0:
            line = (p.stderr or "").strip().splitlines()
            bad.append(f"{js.name}: {line[1] if len(line) > 1 else (line[0] if line else '?')}")
    if bad:
        need(False, bad[0] + (f" (+{len(bad) - 1} more)" if len(bad) > 1 else ""))


# ------------------------------------------------------------------ runner


def find_port(preferred: int) -> int:
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", preferred)) != 0:
            return preferred
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port: int, log_path: Path) -> subprocess.Popen:
    env = dict(os.environ, MOCK_MODE="1", PYTHONUNBUFFERED="1")
    log = open(log_path, "w")  # server log; read back only if it dies on boot
    proc = subprocess.Popen(
        [str(VENV_UVICORN), "billproof.server.app:app", "--app-dir", str(SRC),
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(BP_DIR), env=env,
        stdout=log, stderr=subprocess.STDOUT, text=True,
    )
    proc._log_file = log  # keep the fd alive for the child's lifetime
    return proc


def wait_ready(base: str, proc: subprocess.Popen | None, timeout: float = 20.0,
               log_path: Path | None = None):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if proc is not None and proc.poll() is not None:
            out = ""
            try:
                out = log_path.read_text()[-2000:] if log_path else ""
            except Exception:
                pass
            raise SystemExit(f"server exited during startup (code {proc.returncode}):\n{out}")
        try:
            r = httpx.get(f"{base}/api/status", timeout=2.0)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise SystemExit(f"server did not become ready on {base} within {timeout}s")


def colorize(status: str, text: str, color: bool) -> str:
    if not color:
        return text
    code = {PASS: "\033[32m", SKIP: "\033[33m", FAIL: "\033[31m"}[status]
    return f"{code}{text}\033[0m"


def main() -> int:
    ap = argparse.ArgumentParser(description="BillProof demo-readiness checker")
    ap.add_argument("--url", help="attach to a running server instead of booting one")
    ap.add_argument("--port", type=int, default=8704)
    ap.add_argument("--secs", type=float, default=3.0, help="SSE listen seconds")
    ap.add_argument("--require-all", action="store_true",
                    help="exit non-zero unless every check is green (no SKIP)")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--json", dest="json_out", help="write results JSON to this path")
    args = ap.parse_args()

    proc = None
    log_path = Path(os.environ.get("CHECK_DEMO_LOG", "/tmp/check_demo_server.log"))
    if args.url:
        base = args.url.rstrip("/")
        print(f"attaching to {base}")
    else:
        port = find_port(args.port)
        if port != args.port:
            print(f"port {args.port} busy; using {port}")
        proc = start_server(port, log_path)
        base = f"http://127.0.0.1:{port}"
        print(f"started server pid {proc.pid} on {base} (MOCK_MODE=1, log {log_path})",
              flush=True)

    ctx = Ctx(httpx.Client(base_url=base, timeout=httpx.Timeout(60.0, read=90.0)), STATIC)
    ctx.live = True
    try:
        wait_ready(base, proc, log_path=log_path)

        ctx.check("server: GET /", lambda: c_root(ctx))
        ctx.check("api: status", lambda: c_status(ctx))
        for split in ("dev", "test", "demo"):
            ctx.check(f"api: cases split={split}", lambda s=split: c_cases(ctx, s))

        demo_present = [r["case_id"] for r in ctx.cases_by_split.get("demo", [])]
        demo_targets = demo_present[:10] or [c for c in DEMO_IDS]
        for cid in demo_targets:
            ctx.check(f"api: case {cid} detail", lambda c=cid: c_case_detail(ctx, c))
            det = ctx.demo_details.get(cid)
            if det:
                for doc, n in list(det["pages"].items())[:4]:
                    for pg in range(1, min(n, 2) + 1):
                        ctx.check(f"api: page {cid} {doc}/p{pg}",
                                  lambda c=cid, d=doc, p=pg: c_page_png(ctx, c, d, p))
                ctx.check(f"api: crop {cid}", lambda c=cid: c_crop(ctx, c))
            ctx.check(f"api: analyze {cid}", lambda c=cid: c_analyze(ctx, c))

        ctx.check("api: tamper control (clean)", lambda: c_tamper_control(ctx))
        for op in TAMPER_OPS:
            ctx.check(f"api: tamper {op}", lambda o=op: c_tamper_op(ctx, o))

        ctx.check("api: packet JSON", lambda: c_packet_json(ctx))
        ctx.check("api: packet PDF", lambda: c_packet_pdf(ctx))
        for bench in ("synthetic", "imr"):
            ctx.check(f"api: report {bench}", lambda b=bench: c_report(ctx, b))
            ctx.check(f"api: calibration {bench}", lambda b=bench: c_calibration(ctx, b))

        ctx.check(f"api: race replay ~{args.secs:g}s",
                  lambda: c_race(ctx, "replay", args.secs))
        ctx.check(f"api: race sim ~{args.secs:g}s",
                  lambda: c_race(ctx, "sim", args.secs))

        ctx.check("api: imr items", lambda: c_imr_items(ctx))
        ctx.check("api: imr item detail", lambda: c_imr_item(ctx))
        ctx.check("api: imr item gold", lambda: c_imr_item_gold(ctx))

        for mod in ("audit_routes", "ingest_routes"):
            mod_file = SRC / "billproof" / "server" / f"{mod}.py"
            if mod_file.exists():
                for meth, path in _sibling_routes(mod_file):
                    if path == "/api/ingest":
                        ctx.check("api: POST /api/ingest (bill pdf)",
                                  lambda: c_ingest(ctx))
                        continue
                    ctx.check(f"api: {meth} {path}",
                              lambda m=meth, p=path: c_sibling_route(ctx, m, p))

        try:
            assets = _ui_assets(ctx)
        except Exception as e:
            ctx.results.append(Result("ui: index assets", FAIL, 0, str(e)))
            assets = []
        for a in assets:
            ctx.check(f"ui: {a}", lambda p=a: c_ui_asset(ctx, p))
        ctx.check("ui: fonts.css url() files", lambda: c_ui_fonts(ctx))
        ctx.check("ui: JS-referenced static", lambda: c_ui_js_refs(ctx))
        ctx.check("ui: JS syntax (node --check)", lambda: c_ui_js_syntax(ctx))
    finally:
        ctx.http.close()
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    # ---------------------------------------------------------------- table
    color = not args.no_color and sys.stdout.isatty()
    w_name = max([len(r.name) for r in ctx.results] + [5])
    print()
    print(f"{'check'.ljust(w_name)}  {'status':6}  {'ms':>7}  detail")
    print("-" * (w_name + 60))
    counts = {PASS: 0, SKIP: 0, FAIL: 0}
    for r in ctx.results:
        counts[r.status] += 1
        mark = {PASS: "PASS", SKIP: "SKIP", FAIL: "FAIL"}[r.status]
        detail = f"  {r.detail[:100]}" if r.detail else ""
        print(f"{r.name.ljust(w_name)}  {colorize(r.status, mark.ljust(6), color)}  "
              f"{r.ms:>7.0f}{detail}")
    print("-" * (w_name + 60))
    print(f"{counts[PASS]} pass, {counts[SKIP]} skip, {counts[FAIL]} fail   ({base})")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(
            [{"name": r.name, "status": r.status, "ms": round(r.ms, 1), "detail": r.detail}
             for r in ctx.results], indent=2))

    if counts[FAIL]:
        return 1
    if args.require_all and counts[SKIP]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
