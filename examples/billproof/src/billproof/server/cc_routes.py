"""Prior-auth command center replay routes .

Import line for the server (already wired in ``app.py``)::

    app.include_router(importlib.import_module("billproof.server.cc_routes").router)

  GET /api/cc/summary
  GET /api/cc/queue/stream?speed=
  GET /api/cc/rule_change
  GET /api/cc/what_decides
  GET /api/cc/case/{id}
  GET /api/cc/policies
  GET /api/cc/policy/{id}
  GET /api/cc/presets
  POST /api/cc/what_if
  POST /api/cc/what_if/new_criterion

Reads recorded JSONL only. Missing files return empty payloads, never a crash.
The queue stream replays recorded per-case latency at 1/speed (offline).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from billproof.command_center.common import (
    DECIDE_SUMMARY_PATH,
    FULL_SUMMARY_PATH,
    PRESETS_PATH,
    QUEUE_SUMMARY_PATH,
    RULE_SUMMARY_PATH,
    load_spend,
    read_json,
)
from billproof.command_center.report import cases_index, replay_rows

router = APIRouter(prefix="/api/cc", tags=["command-center"])

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _empty_summary() -> dict[str, Any]:
    spend = load_spend()
    return {
        "status": "empty",
        "n_planned": None,
        "drex": {"n": 0, "accuracy": None, "cases_per_minute": None,
                 "cost_per_1000": None, "by_colour": {}, "by_label": {}},
        "gpt": {"n_measured": 0, "accuracy": None, "projected": None},
        "spend": spend,
        "note": "No queue summary yet. Run scripts/cc_queue.py.",
    }


@router.get("/summary")
def cc_summary() -> JSONResponse:
    legacy = read_json(QUEUE_SUMMARY_PATH, None)
    full = read_json(FULL_SUMMARY_PATH, None)
    if not isinstance(full, dict):
        if not isinstance(legacy, dict):
            return JSONResponse(_empty_summary())
        return JSONResponse(legacy)
    body = dict(full)
    if isinstance(legacy, dict):
        body["legacy_initial_queue"] = {
            "status": legacy.get("status"),
            "n_planned": legacy.get("n_planned"),
            "drex": legacy.get("drex"),
            "gpt": legacy.get("gpt"),
            "spend": legacy.get("spend"),
            "stuck": legacy.get("stuck"),
        }
        # The GPT sample was measured on the earlier queue. It is not rescaled.
        if "gpt" not in body or body.get("gpt") is None:
            body["gpt"] = legacy.get("gpt")
        body["gpt_note"] = (
            "GPT figures are the earlier queue's measured sample and its "
            "projection onto that queue only. GPT was not run on the full "
            "criteria-gym set, and that projection is not scaled up."
        )
        if "stuck" not in body:
            body["stuck"] = legacy.get("stuck")
    else:
        body.setdefault("gpt", {"n_measured": 0, "projected": None})
    return JSONResponse(body)


@router.get("/rule_change")
def cc_rule_change() -> JSONResponse:
    data = read_json(RULE_SUMMARY_PATH, None)
    if not isinstance(data, dict):
        data = {
            "status": "empty",
            "simulated": True,
            "n_flips": 0,
            "flips": [],
            "note": "No rule-change summary yet. Run scripts/cc_rule_change.py.",
        }
    return JSONResponse(data)


@router.get("/what_decides")
def cc_what_decides() -> JSONResponse:
    data = read_json(DECIDE_SUMMARY_PATH, None)
    if not isinstance(data, dict):
        data = {
            "status": "empty",
            "agreement": None,
            "stuck": {"n_stuck": 0, "reasons": []},
            "documents": {"bundles": [], "ranked": []},
            "note": "No what-decides summary yet. Run scripts/cc_what_decides.py.",
        }
    return JSONResponse(data)


@router.get("/case/{case_id:path}")
def cc_case(case_id: str) -> JSONResponse:
    found = cases_index().get(case_id)
    if not found:
        return JSONResponse({"error": "not found", "case_id": case_id}, status_code=404)
    return JSONResponse({"case_id": case_id, **found})


@router.get("/queue/stream")
async def cc_queue_stream(request: Request, speed: float = Query(1.0),
                          full: int = Query(0)) -> StreamingResponse:
    speed = min(max(float(speed or 1.0), 0.05), 500.0)
    return StreamingResponse(
        _replay(request, speed, full=bool(full)),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


async def _replay(request: Request, speed: float, *, full: bool = False) -> AsyncIterator[str]:
    try:
        if full:
            from billproof.command_center.store import replay_full
            rows = await asyncio.to_thread(replay_full)
            source = "command_center/full_replay.jsonl"
        else:
            rows = await asyncio.to_thread(replay_rows)
            source = "command_center/queue_replay.jsonl"
    except Exception as exc:  # missing/corrupt file -> one error event, no crash
        yield _sse("error", {"error": type(exc).__name__})
        return
    yield _sse("start", {
        "n": len(rows),
        "mode": "REPLAY",
        "speed": speed,
        "source": source,
        "full": full,
    })
    correct = 0
    cost = 0.0
    colours = {"green": 0, "red": 0, "amber": 0}
    clock = 0.0
    prev = 0.0
    for i, row in enumerate(rows):
        if await request.is_disconnected():
            return
        clock += float(row.get("latency_ms") or 0.0)
        wait = (clock - prev) / speed / 1000.0
        prev = clock
        while wait > 12.0:
            await asyncio.sleep(12.0)
            yield ": hb\n\n"
            wait -= 12.0
            if await request.is_disconnected():
                return
        if wait > 0.01:
            await asyncio.sleep(wait)
        if row.get("correct"):
            correct += 1
        cost += float(row.get("cost_usd") or 0.0)
        colour = row.get("colour")
        if colour in colours:
            colours[colour] += 1
        done = i + 1
        yield _sse("decision", {
            "i": i,
            "case_id": row.get("case_id"),
            "split": row.get("split"),
            "decision": row.get("decision"),
            "label": row.get("label"),
            "colour": colour,
            "correct": row.get("correct"),
            "gold_decision": row.get("gold_decision"),
            "criteria": row.get("criteria"),
            "latency_ms": row.get("latency_ms"),
            "cost_usd": row.get("cost_usd"),
            "publisher": row.get("publisher"),
            "document_title": row.get("document_title"),
            "resampled": bool(row.get("resampled")),
            "cum": {
                "done": done,
                "correct": correct,
                "accuracy": correct / done,
                "cost_usd": round(cost, 8),
                "time_ms": round(clock, 3),
                "by_colour": dict(colours),
            },
        })
    yield _sse("finish", {
        "n": len(rows),
        "correct": correct,
        "accuracy": (correct / len(rows)) if rows else None,
        "cost_usd": round(cost, 8),
        "time_ms": round(clock, 3),
        "by_colour": colours,
        "mode": "REPLAY",
    })


def _store_or_503():
    from billproof.command_center.store import get_store
    try:
        return get_store(), None
    except FileNotFoundError as exc:
        return None, JSONResponse({"error": str(exc)}, status_code=503)


def _policy_public(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": policy.get("id"),
        "policy_id": policy.get("policy_id"),
        "payer": policy.get("payer"),
        "state": policy.get("state"),
        "family": policy.get("family"),
        "publisher": policy.get("publisher"),
        "title": policy.get("title"),
        "drug": policy.get("drug"),
        "connective": policy.get("connective"),
        "n_cases": policy.get("n_cases"),
        "n_initial": policy.get("n_initial"),
        "n_followup": policy.get("n_followup"),
        "n_criteria": len(policy.get("clauses") or []),
        "window": policy.get("window"),
        "criteria": policy.get("criteria"),
        "clauses": policy.get("clauses"),
    }


@router.get("/policies")
def cc_policies(state: str | None = None, family: str | None = None) -> JSONResponse:
    store, err = _store_or_503()
    if err is not None:
        return err
    policies = []
    for policy in store.policies:
        if state and policy.get("state") != state:
            continue
        if family and policy.get("family") != family:
            continue
        policies.append(_policy_public(policy))
    return JSONResponse({
        "n_policies": len(policies),
        "n_cases": store.counts.get("n_cases"),
        "n_initial": store.counts.get("n_initial"),
        "n_followup": store.counts.get("n_followup"),
        "source": store.counts.get("source"),
        "policies": policies,
    })


@router.get("/policy/{policy_id:path}")
def cc_policy(policy_id: str) -> JSONResponse:
    store, err = _store_or_503()
    if err is not None:
        return err
    policy = store.policy(policy_id)
    if policy is None:
        return JSONResponse({"error": "not found", "id": policy_id}, status_code=404)
    cards = store.cards_for(policy["id"])
    labels: dict[str, int] = {}
    cases = []
    for card in cards:
        label = card.base_label if card.cached else None
        if label:
            labels[label] = labels.get(label, 0) + 1
        cases.append({
            "case_id": card.case_id,
            "split": card.split,
            "stage": card.stage,
            "gold": card.gold,
            "cached": card.cached,
            "decision": card.base_decision or None,
            "label": label,
            "colour": card.base_colour or None,
        })
    return JSONResponse({
        **_policy_public(policy),
        "n_cached": sum(1 for card in cards if card.cached),
        "decisions": labels,
        "cases": cases,
        "partial": store.partial,
    })


@router.get("/presets")
def cc_presets() -> JSONResponse:
    store, err = _store_or_503()
    if err is not None:
        cached = read_json(PRESETS_PATH, None)
        if isinstance(cached, dict):
            return JSONResponse(cached)
        return err
    from billproof.command_center.store import build_presets
    if store.n_cached:
        return JSONResponse(build_presets(store))
    cached = read_json(PRESETS_PATH, None)
    if isinstance(cached, dict):
        return JSONResponse(cached)
    return JSONResponse(build_presets(store))


def _what_if_request(body: dict[str, Any], store) -> tuple[dict, dict, dict | None]:
    from billproof.command_center.whatif import (
        preset_specs, resolve_preset, validate_apply_to, validate_edit,
    )
    preset_id = body.get("preset")
    edit = dict(body.get("edit") or {})
    apply_to = dict(body.get("apply_to") or {})
    meta = None
    if preset_id:
        spec = next((item for item in preset_specs() if item["id"] == preset_id), None)
        if spec is None:
            raise ValueError(f"unknown preset {preset_id}")
        meta = resolve_preset(spec, store.policies, store.cards)
        base_edit = dict(meta["edit"])
        for key, value in edit.items():
            if value is not None:
                base_edit[key] = value
        edit = base_edit
        merged = dict(meta["apply_to"])
        merged.update(apply_to)
        apply_to = merged
    return validate_edit(edit), validate_apply_to(apply_to), meta


@router.post("/what_if", response_model=None)
async def cc_what_if(request: Request, stream: int = Query(0)) -> JSONResponse | StreamingResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body must be JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be an object"}, status_code=400)
    store, err = _store_or_503()
    if err is not None:
        return err
    try:
        edit, apply_to, meta = _what_if_request(body, store)
    except (ValueError, TypeError) as exc:
        return JSONResponse({"error": str(exc), "hypothetical": True}, status_code=400)
    from billproof.command_center.whatif import run_edits
    result = await asyncio.to_thread(run_edits, store.cards, edit=edit, apply_to=apply_to)
    result["partial"] = store.partial
    result["n_cached_queue"] = store.n_cached
    result["n_cases"] = store.counts.get("n_cases")
    if meta is not None:
        result["preset"] = meta.get("id")
        result["title"] = meta.get("title")
        result["policy"] = meta.get("policy")
    want_stream = bool(stream) or str(body.get("stream") or "") in {"1", "true", "True"}
    if not want_stream:
        return JSONResponse(result)

    async def events() -> AsyncIterator[str]:
        yield _sse("start", {
            "hypothetical": True,
            "n_affected": result["n_affected"],
            "n_decided": result["n_decided"],
            "partial": result["partial"],
            "preset": result.get("preset"),
        })
        for flip in result["flips"]:
            if await request.is_disconnected():
                return
            yield _sse("flip", flip)
        summary = dict(result)
        summary.pop("flips", None)
        yield _sse("summary", summary)

    return StreamingResponse(events(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/what_if/new_criterion", response_model=None)
async def cc_new_criterion(request: Request) -> StreamingResponse | JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body must be JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be an object"}, status_code=400)
    store, err = _store_or_503()
    if err is not None:
        return err
    try:
        from billproof.command_center.whatif import validate_apply_to
        apply_to = validate_apply_to(body.get("apply_to"))
    except (ValueError, TypeError) as exc:
        return JSONResponse({"error": str(exc), "hypothetical": True}, status_code=400)
    text = str(body.get("text") or "")
    cap = body.get("cap") or 500
    from billproof.command_center.live_criterion import iter_new_criterion

    def _pull(gen):
        try:
            return next(gen)
        except StopIteration:
            return None

    async def events() -> AsyncIterator[str]:
        gen = iter_new_criterion(text, apply_to, cap=cap)
        while True:
            if await request.is_disconnected():
                return
            item = await asyncio.to_thread(_pull, gen)
            if item is None:
                return
            name, payload = item
            yield _sse(name, payload)

    return StreamingResponse(events(), media_type="text/event-stream", headers=_SSE_HEADERS)
