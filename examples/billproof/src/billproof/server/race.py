"""SSE race stream (CONTRACT §4 `GET /api/race/stream`).

source=replay: interleave recorded rows from data/runs/<run_id>.jsonl so each lane
advances on its own recorded latency clock (scaled by 1/speed, compressed so a
full race fits ~30 s at speed 1).
source=sim|live: drive bench.runner.run_bench in a thread via its on_event
callback and normalize whatever it emits into the contract event names.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import json
import os
import queue
import re
import threading
import time
from typing import Any, AsyncIterator

from billproof.config import REAL_DIR, RUNS_DIR, SYN_DIR, settings

LANES = ("frontier", "drex", "hybrid")
RACE_CAP_MS = 30_000.0  # compress a replay so a full race takes ~<=30 s at speed 1
HEARTBEAT_S = 10.0
MIN_DELAY_S = 0.005
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")

HB = ": hb\n\n"


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


# ---------------------------------------------------------------- runs / labels


def label_sha256(bench: str) -> str:
    sha = (SYN_DIR / "labels.sha256") if bench == "synthetic" else (REAL_DIR / "imr_labels.sha256")
    if sha.exists():
        return sha.read_text().split()[0].strip()
    js = (SYN_DIR / "labels.jsonl") if bench == "synthetic" else (REAL_DIR / "imr_labels.jsonl")
    if js.exists():
        return hashlib.sha256(js.read_bytes()).hexdigest()
    return ""


_BANNED_RUN = re.compile(r"^(?:dryrun_|strays-)")


def _runs_index(bench: str | None = None, split: str | None = None) -> list[tuple[str, dict]]:
    """(run_id, meta) for non-banned runs, optionally filtered by bench/split."""
    out: list[tuple[str, dict]] = []
    if not RUNS_DIR.exists():
        return out
    for p in RUNS_DIR.glob("*.meta.json"):
        rid = p.name[: -len(".meta.json")]
        if _BANNED_RUN.match(rid):
            continue
        try:
            m = json.loads(p.read_text())
        except Exception:
            continue
        if not isinstance(m, dict):
            continue
        m.setdefault("run_id", rid)
        if bench and m.get("bench") != bench:
            continue
        if split and m.get("split") not in (None, split):
            continue
        if not (RUNS_DIR / f"{rid}.jsonl").exists():
            continue
        out.append((rid, m))
    return out


def list_runs(limit: int = 100) -> list[dict]:
    out = [m for _, m in _runs_index()]
    out.sort(key=lambda m: str(m.get("created", "")), reverse=True)
    return out[:limit]


def _meta_of(rid: str) -> dict:
    mp = RUNS_DIR / f"{rid}.meta.json"
    if mp.exists():
        try:
            m = json.loads(mp.read_text())
            if isinstance(m, dict):
                m.setdefault("run_id", rid)
                return m
        except Exception:
            pass
    return {"run_id": rid}


def _stage_override(bench: str) -> str | None:
    """data/runs/STAGE.json {"synthetic": run_id, "imr": run_id} — the stage pin."""
    try:
        j = json.loads((RUNS_DIR / "STAGE.json").read_text())
    except Exception:
        return None
    rid = j.get(bench) if isinstance(j, dict) else None
    if isinstance(rid, str) and rid and (RUNS_DIR / f"{rid}.jsonl").exists():
        return rid
    return None


def pick_default_run(bench: str, split: str | None = None) -> tuple[str | None, dict]:
    """Deterministic stage run: STAGE.json pin > LIVE run > canonical
    sim_<bench>_<split> / sim_<bench>_test > newest. dryrun_*/strays-* never win."""
    stage = _stage_override(bench)
    metas = _runs_index(bench, split)
    by_id = {rid: m for rid, m in metas}
    if stage:
        return stage, by_id.get(stage) or _meta_of(stage)
    live = [(rid, m) for rid, m in metas if str(m.get("mode", "")).upper() == "LIVE"]
    if live:
        return max(live, key=lambda t: str(t[1].get("created", "")))
    cands = [f"sim_{bench}_{split}"] if split else []
    cands += [f"sim_{bench}_test", f"sim_{bench}_dev"]
    for c in cands:
        if c in by_id:
            return c, by_id[c]
        if not _BANNED_RUN.match(c) and (RUNS_DIR / f"{c}.jsonl").exists():
            return c, _meta_of(c)
    if metas:
        return max(metas, key=lambda t: str(t[1].get("created", "")))
    return None, {}


def newest_run_id(bench: str, split: str | None = None) -> str | None:
    return pick_default_run(bench, split)[0]


def load_rows(run_id: str) -> tuple[list[dict], dict]:
    """(jsonl rows, meta dict). Raises FileNotFoundError if the run does not exist."""
    if not _RUN_ID_RE.match(run_id or ""):
        raise FileNotFoundError(f"bad run id {run_id!r}")
    jl = RUNS_DIR / f"{run_id}.jsonl"
    rows = [json.loads(l) for l in jl.read_text().splitlines() if l.strip()]
    mp = RUNS_DIR / f"{run_id}.meta.json"
    meta = json.loads(mp.read_text()) if mp.exists() else {"run_id": run_id}
    return rows, meta


# ---------------------------------------------------------------- cumulative stats


class _Cum:
    __slots__ = ("done", "correct_q", "total_q", "case_correct", "time_ms", "cost")

    def __init__(self) -> None:
        self.done = 0
        self.correct_q = 0
        self.total_q = 0
        self.case_correct = 0
        self.time_ms = 0.0
        self.cost = 0.0

    def add(self, row: dict) -> None:
        self.done += 1
        corr = row.get("correct") or {}
        self.total_q += len(corr)
        self.correct_q += sum(1 for v in corr.values() if v)
        self.case_correct += 1 if row.get("case_correct") else 0
        self.time_ms += float(row.get("latency_ms") or 0.0)
        self.cost += float(row.get("cost_usd") or 0.0)

    def snap(self) -> dict:
        return {
            "done": self.done,
            "correct_q": self.correct_q,
            "total_q": self.total_q,
            "case_correct": self.case_correct,
            "cases": self.done,
            "accuracy": round(self.correct_q / self.total_q, 4) if self.total_q else None,
            "time_ms": round(self.time_ms, 1),
            "cost_usd": round(self.cost, 6),
        }


def _routes(row: dict) -> dict:
    out = {"drex_kept": 0, "frontier_verified": 0, "human": 0}
    lane = row.get("lane")
    for a in (row.get("answers") or {}).values():
        r = (a or {}).get("route") if isinstance(a, dict) else None
        if r in ("human", "hard_cap"):
            out["human"] += 1
        elif r == "drex_kept":
            out["drex_kept"] += 1
        elif r in ("frontier_verified", "frontier_only"):
            out["frontier_verified"] += 1
        elif lane == "frontier":
            out["frontier_verified"] += 1
        else:
            out["drex_kept"] += 1
    return out


def _highlight(row: dict) -> dict | None:
    """The row's most interesting answer for the Race ticker: an escalated route
    or a 'yes' finding first, else the lowest-confidence answer."""
    answers = row.get("answers") or {}
    correct = row.get("correct") or {}
    best: tuple | None = None
    best_key = (-1, 0.0)
    for qn, a in answers.items():
        if not isinstance(a, dict):
            continue
        choice, route = a.get("choice"), a.get("route")
        conf = a.get("confidence")
        score = 0
        if choice == "yes" or choice in ("DISPUTE", "APPEAL", "ASK_PATIENT"):
            score = 2
        if route == "frontier_verified":
            score = max(score, 2)
        if route in ("human", "hard_cap"):
            score = 3
        c = conf if isinstance(conf, (int, float)) and not isinstance(conf, bool) else 1.0
        if (score, -c) > best_key:
            best_key, best = (score, -c), (qn, choice, route, conf)
    if best is None:
        return None
    qn, choice, route, conf = best
    return {"q": qn, "choice": choice, "p": conf, "route": route, "correct": correct.get(qn)}


def _decision(lane: str, i: int, row: dict, cums: dict[str, _Cum]) -> dict:
    ev = {
        "lane": lane,
        "case_id": row.get("case_id"),
        "i": i,
        "correct": row.get("correct") or {},
        "case_correct": bool(row.get("case_correct")),
        "latency_ms": row.get("latency_ms"),
        "cost_usd": row.get("cost_usd"),
        "routes": _routes(row),
        "cum": {l: c.snap() for l, c in cums.items()},
    }
    hl = _highlight(row)
    if hl:
        ev["highlight"] = hl
        # top-level aliases the current race.js ticker already reads
        ev["question"] = hl["q"]
        ev["p"] = hl["p"]
    return ev


def _finish(cums: dict[str, _Cum]) -> dict:
    f, h = cums.get("frontier"), cums.get("hybrid")
    speedup = round(f.time_ms / h.time_ms, 2) if f and h and h.time_ms else None
    ratio = round(h.cost / f.cost, 4) if f and h and f.cost else None
    return {
        "summary": {l: c.snap() for l, c in cums.items()},
        "speedup_vs_frontier": speedup,
        "cost_ratio_vs_frontier": ratio,
    }


# ---------------------------------------------------------------- replay


async def _replay(
    request: Any, bench: str, split: str, run_id: str | None, speed: float
) -> AsyncIterator[str]:
    rid = run_id or newest_run_id(bench, split)
    if not rid or not (RUNS_DIR / f"{rid}.jsonl").exists():
        yield sse("error", {"error": f"no recorded run for bench={bench} split={split}", "run_id": rid})
        return
    try:
        rows, meta = await asyncio.to_thread(load_rows, rid)
    except Exception as e:
        yield sse("error", {"error": f"{type(e).__name__}: {e}", "run_id": rid})
        return
    if not rows:
        yield sse("error", {"error": f"run {rid} is empty", "run_id": rid})
        return

    per: dict[str, list[tuple[float, dict]]] = {}
    for r in rows:
        q = per.setdefault(r.get("lane", "?"), [])
        q.append(((q[-1][0] if q else 0.0) + float(r.get("latency_ms") or 0.0), r))
    total = max((q[-1][0] for q in per.values() if q), default=0.0)
    scale = min(1.0, RACE_CAP_MS / total) if total > 0 else 1.0
    lanes = [l for l in LANES if l in per] + sorted(l for l in per if l not in LANES)
    n_cases = len({r.get("case_id") for r in rows})

    yield sse(
        "start",
        {
            "run_id": rid,
            "bench": bench,
            "split": split,
            "n_cases": n_cases,
            "lanes": lanes,
            "label_sha256": label_sha256(bench),
            "mode": meta.get("mode", "REPLAY"),
            "speed": speed,
        },
    )

    merged = sorted((t, lane, i, r) for lane, q in per.items() for i, (t, r) in enumerate(q))
    cums = {l: _Cum() for l in lanes}
    remaining = {l: len(q) for l, q in per.items()}
    prev = 0.0
    for t, lane, i, row in merged:
        if await request.is_disconnected():
            return
        wait = (t - prev) * scale / speed / 1000.0  # recorded latencies are ms; asyncio.sleep takes seconds
        prev = t
        while wait > HEARTBEAT_S:
            await asyncio.sleep(HEARTBEAT_S)
            yield HB
            if await request.is_disconnected():
                return
            wait -= HEARTBEAT_S
        if wait > MIN_DELAY_S:
            await asyncio.sleep(wait)
        c = cums.setdefault(lane, _Cum())
        c.add(row)
        yield sse("decision", _decision(lane, i, row, cums))
        remaining[lane] -= 1
        if remaining[lane] == 0:
            yield sse("lane_done", {"lane": lane, "cum": c.snap()})
    yield sse("finish", _finish(cums))


# ---------------------------------------------------------------- sim / live


def _count_items(bench: str, split: str) -> int:
    try:
        if bench == "imr":
            m = importlib.import_module("billproof.bench.real_imr")
            return len(m.list_items(split))
        m = importlib.import_module("billproof.data")
        return len(m.list_cases(split))
    except Exception:
        return 0


def _live_mode() -> str:
    try:
        s = settings()
        return "LIVE" if (s.use_real("drex") or s.use_real("frontier")) else "SIMULATED"
    except Exception:
        return "LIVE"


def _norm(ev: Any) -> tuple[str, Any]:
    """Normalize whatever on_event hands us into (kind, payload)."""
    if not isinstance(ev, dict):
        return ("event", {"type": "meta", "value": ev})
    t = ev.get("type") or ev.get("event")
    if t == "__end__":
        return ("end", ev)
    if t == "__error__":
        return ("error", ev.get("error", "runner failed"))
    if "lane" in ev and "case_id" in ev:
        return ("row", ev)
    if isinstance(ev.get("row"), dict):
        return ("row", ev["row"])
    if t == "decision" and isinstance(ev.get("data"), dict):
        return ("row", ev["data"])
    if t == "lane_done":
        return ("lane_done", ev)
    return ("event", ev)


def _start_runner(
    fn: Any, bench: str, split: str, run_id: str, source: str, q: "queue.Queue", stop: threading.Event
) -> None:
    def work() -> None:
        old_mock = os.environ.get("MOCK_MODE")
        try:
            if source == "sim":
                os.environ["MOCK_MODE"] = "1"
                try:
                    settings.cache_clear()
                except Exception:
                    pass
            kw: dict[str, Any] = {
                "record": source == "live",  # sim streams are throwaway; never pollute data/runs
                "on_event": lambda ev: None if stop.is_set() else q.put(ev),
            }
            try:
                if "run_id" in inspect.signature(fn).parameters:
                    kw["run_id"] = run_id
            except (TypeError, ValueError):
                pass
            rid = fn(bench, split, list(LANES), **kw)
            q.put({"type": "__end__", "run_id": rid or run_id})
        except Exception as e:
            q.put({"type": "__error__", "error": f"{type(e).__name__}: {e}"})
        finally:
            if source == "sim":
                if old_mock is None:
                    os.environ.pop("MOCK_MODE", None)
                else:
                    os.environ["MOCK_MODE"] = old_mock
                try:
                    settings.cache_clear()
                except Exception:
                    pass

    threading.Thread(target=work, daemon=True, name=f"race-{run_id}").start()


async def _live(
    request: Any, bench: str, split: str, source: str, speed: float
) -> AsyncIterator[str]:
    try:
        runner = importlib.import_module("billproof.bench.runner")
        fn = runner.run_bench
    except Exception:
        yield sse("error", {"error": "bench.runner not available yet", "missing": "bench.runner"})
        return

    run_id = f"{source}_{bench}_{split}_{int(time.time())}"
    n_cases = await asyncio.to_thread(_count_items, bench, split)
    mode = "SIMULATED" if source == "sim" else _live_mode()
    yield sse(
        "start",
        {
            "run_id": run_id,
            "bench": bench,
            "split": split,
            "n_cases": n_cases,
            "lanes": list(LANES),
            "label_sha256": label_sha256(bench),
            "mode": mode,
            "speed": speed,
        },
    )

    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    _start_runner(fn, bench, split, run_id, source, q, stop)

    cums = {l: _Cum() for l in LANES}
    per_lane_i = {l: 0 for l in LANES}
    lanes_done: set[str] = set()
    idle = 0.0
    ended = False
    try:
        while not ended:
            if await request.is_disconnected():
                return
            try:
                ev = q.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.05)
                idle += 0.05
                if idle >= HEARTBEAT_S:
                    yield HB
                    idle = 0.0
                continue
            idle = 0.0
            kind, payload = _norm(ev)
            if kind == "end":
                ended = True
            elif kind == "error":
                yield sse("error", {"error": payload})
                ended = True
            elif kind == "row":
                lane = payload.get("lane", "?")
                c = cums.setdefault(lane, _Cum())
                c.add(payload)
                i = per_lane_i.get(lane, 0)
                per_lane_i[lane] = i + 1
                yield sse("decision", _decision(lane, i, payload, cums))
                if n_cases and c.done >= n_cases and lane not in lanes_done:
                    lanes_done.add(lane)
                    yield sse("lane_done", {"lane": lane, "cum": c.snap()})
            elif kind == "lane_done":
                lane = payload.get("lane")
                if lane in cums and lane not in lanes_done:
                    lanes_done.add(lane)
                    yield sse("lane_done", {"lane": lane, "cum": cums[lane].snap()})
            else:
                yield sse(str(payload.get("type") or "meta"), payload)
    finally:
        stop.set()
    yield sse("finish", _finish(cums))


async def stream(
    request: Any, *, bench: str, split: str, source: str, run_id: str | None, speed: float
) -> AsyncIterator[str]:
    if source == "replay":
        async for s in _replay(request, bench, split, run_id, speed):
            yield s
    else:
        async for s in _live(request, bench, split, source, speed):
            yield s
