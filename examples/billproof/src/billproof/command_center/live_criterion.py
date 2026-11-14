"""Ask Drex one new criterion, then re-decide. Real calls, capped."""

from __future__ import annotations

import hashlib
from typing import Any, Iterator

from billproof.bench.pa.questions import _FACT_INSTRUCTIONS
from billproof.command_center.batch import (
    WorkItem,
    budget_add,
    budget_ok,
    make_adapter,
    pack,
    run_pack,
)
from billproof.command_center.catalog import load_cases_by_ids
from billproof.command_center.common import (
    NEW_CRITERION_CAP_USD,
    NEW_CRITERION_PATH,
    add_full_spend,
    append_jsonl,
    estimate_nace_usd,
    full_spend_ok,
    load_jsonl,
    now_iso,
    scrub,
)
from billproof.command_center.store import get_store
from billproof.command_center.whatif import matches, run_edits
from billproof.schema import Question

CASE_CAP = 500
_VALUES = {
    "documented_satisfied": True,
    "documented_not_satisfied": False,
    "not_documented": None,
}


def text_sha(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()[:12]


def fact_name(text: str) -> str:
    return "added_" + text_sha(text)


def _question(text: str, fact: str) -> Question:
    return Question(
        name=f"fact::{fact}",
        type="choice",
        instructions=_FACT_INSTRUCTIONS.format(clause=text.strip()),
        criteria={
            "documented_satisfied": "a current record explicitly documents this clause as satisfied",
            "documented_not_satisfied": "a current record explicitly documents this clause as NOT satisfied",
            "not_documented": "no current record addresses this clause",
        },
        family="pa_fact",
        evidence=[],
    )


def _cached_answers(sha: str) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for row in load_jsonl(NEW_CRITERION_PATH):
        if row.get("text_sha") == sha and row.get("case_id") and not row.get("error"):
            found[row["case_id"]] = row
    return found


def _install_budget() -> None:
    budget_ok.set(lambda estimate: full_spend_ok(estimate, lane="criterion"))
    budget_add.set(lambda amount: add_full_spend(amount, lane="criterion"))


def _estimate(items: list[WorkItem]) -> float:
    state = {"packets": [{"packet_id": item.packet_id, "state": item.state} for item in items]}
    return estimate_nace_usd(state, sum(len(item.questions) for item in items))


def iter_new_criterion(text: str, apply_to: dict[str, Any], *,
                       cap: int = CASE_CAP) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(event, payload)`` including the final hypothetical re-decision.

    Drex is asked only for cached cases that do not already have this text.
    At most ``cap`` cases (hard max 500). Spend is capped at $0.05.
    """
    text = (text or "").strip()
    if not text:
        yield ("error", {"error": "text is empty"})
        return
    if len(text) > 800:
        yield ("error", {"error": "text is longer than 800 characters"})
        return
    cap = max(1, min(int(cap or CASE_CAP), CASE_CAP))
    store = get_store()
    matched = [card for card in store.cards if card.cached and matches(card, apply_to)]
    matched.sort(key=lambda card: card.case_id)
    n_in_scope = len(matched)
    chosen = matched[:cap]
    sha = text_sha(text)
    fact = fact_name(text)
    already = _cached_answers(sha)
    yield ("start", {
        "hypothetical": True,
        "text": text,
        "text_sha": sha,
        "fact": fact,
        "n_in_scope": n_in_scope,
        "n_selected": len(chosen),
        "truncated": n_in_scope > len(chosen),
        "cap": cap,
        "cap_usd": NEW_CRITERION_CAP_USD,
        "n_already_cached": sum(1 for card in chosen if card.case_id in already),
        "note": (
            "Hypothetical. The new criterion is required on top of the published "
            "rule. Drex is asked only this one question."
        ),
    })
    if not chosen:
        yield ("error", {
            "error": "no cached cases match apply_to",
            "hypothetical": True,
        })
        return
    need = [card for card in chosen if card.case_id not in already]
    spent = 0.0
    stopped: str | None = None
    if need:
        adapter, simulated = make_adapter()
        if simulated:
            yield ("error", {"error": "Drex is not live", "hypothetical": True})
            return
        _install_budget()
        loaded = load_cases_by_ids({card.case_id for card in need})
        question = _question(text, fact)
        items = []
        for card in need:
            case = loaded.get(card.case_id)
            if case is None:
                continue
            items.append(WorkItem(
                packet_id=case.case_id, case=case, state=case.state(),
                questions=[question], kind="added",
            ))
        groups = pack(items, max_questions=200)
        done = 0
        for group in groups:
            while len(group) > 1 and not full_spend_ok(_estimate(group), lane="criterion"):
                group = group[: max(1, len(group) // 2)]
            if not full_spend_ok(_estimate(group), lane="criterion"):
                stopped = "new-criterion cap reached"
                break
            try:
                results = run_pack(group, adapter=adapter, simulated=False)
            except Exception as exc:
                stopped = scrub(str(exc)) or type(exc).__name__
                for item in group:
                    append_jsonl(NEW_CRITERION_PATH, {
                        "case_id": item.packet_id, "text_sha": sha, "error": stopped,
                        "cost_usd": 0.0, "ts": now_iso(),
                    })
                break
            for item, res in zip(group, results):
                done += 1
                choice = (res.choices or {}).get(f"fact::{fact}")
                row = {
                    "case_id": item.packet_id,
                    "text_sha": sha,
                    "fact": fact,
                    "choice": choice,
                    "value": _VALUES.get(choice) if choice in _VALUES else None,
                    "confidence": (res.confidence or {}).get(f"fact::{fact}"),
                    "cost_usd": res.cost_usd,
                    "error": res.error,
                    "model": res.model,
                    "simulated": False,
                    "ts": now_iso(),
                }
                if choice not in _VALUES:
                    row["error"] = res.error or f"invalid choice {choice}"
                append_jsonl(NEW_CRITERION_PATH, row)
                if not row["error"]:
                    already[item.packet_id] = row
                spent += float(res.cost_usd or 0.0)
            yield ("progress", {
                "done": done,
                "n": len(items),
                "cost_usd": round(spent, 8),
                "hypothetical": True,
            })
    # value null is a real not_documented answer and must be kept.
    extras = {
        case_id: {fact: row.get("value")}
        for case_id, row in already.items()
        if row.get("choice") in _VALUES and not row.get("error")
    }
    result = run_edits(
        chosen,
        edit={"extra_label": text[:160]},
        apply_to={"list_id": sorted({card.list_id for card in chosen})},
        extras=extras,
    )
    result["hypothetical"] = True
    result["new_criterion"] = {"text": text, "text_sha": sha, "fact": fact}
    result["cost_usd"] = round(spent, 8)
    result["truncated"] = n_in_scope > len(chosen)
    result["n_in_scope"] = n_in_scope
    result["n_selected"] = len(chosen)
    result["stopped"] = stopped
    if stopped:
        yield ("error", {"error": stopped, "hypothetical": True, "partial": True})
    for flip in result["flips"]:
        yield ("flip", flip)
    summary = dict(result)
    summary.pop("flips", None)
    summary["n_flips"] = result["n_flips"]
    yield ("summary", summary)
