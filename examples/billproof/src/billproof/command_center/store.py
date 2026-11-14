"""In-memory catalog plus cached Drex facts. Reloads when the fact file changes."""

from __future__ import annotations

import threading
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from billproof.bench.pa.questions import DATE_Q, FACT_OPTIONS, PAYER_Q
from billproof.bench.pa.score import facts_from_answers
from billproof.command_center.catalog import index_policies, load_case_rows, load_policies
from billproof.command_center.common import (
    CASES_PATH,
    FACTS_PATH,
    FULL_NACE_CAP_USD,
    FULL_REPLAY_PATH,
    FULL_SUMMARY_PATH,
    NEW_CRITERION_CAP_USD,
    POLICIES_PATH,
    PRESETS_PATH,
    atomic_write,
    latest_success,
    load_full_spend,
    load_jsonl,
    now_iso,
    write_json,
    write_metric,
)
from billproof.command_center.whatif import (
    CaseCard,
    preset_specs,
    run_preset,
    stamp_baseline,
)

_FACT_OK = set(FACT_OPTIONS)
_SCOPE_OK = {"yes", "no"}
_LOCK = threading.Lock()
_CACHE: "Store | None" = None


def answers_complete(policy: dict[str, Any], choices: dict[str, str]) -> bool:
    for fact in policy.get("facts") or []:
        if choices.get(f"fact::{fact}") not in _FACT_OK:
            return False
    if choices.get(PAYER_Q) not in _SCOPE_OK or choices.get(DATE_Q) not in _SCOPE_OK:
        return False
    return True


def strong_ids(rows: list[dict[str, Any]]) -> set[str]:
    """Same keep-rule as the 2,211 queue: a batch under 95% fact-perfect is retried.

    A case that itself got every fact right, and the right decision, is kept
    even inside a weaker batch.
    """
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("case_id") and not row.get("error"):
            grouped[str(row.get("batch_id"))].append(row)
    strong: set[str] = set()
    for bid, group in grouped.items():
        rate = sum(1 for row in group if row.get("facts_all_correct")) / len(group)
        if rate >= 0.95:
            strong.add(bid)
    done: set[str] = set()
    for row in rows:
        if not row.get("case_id") or row.get("error"):
            continue
        if str(row.get("batch_id")) in strong or (row.get("facts_all_correct") and row.get("correct")):
            done.add(row["case_id"])
    return done


def done_case_ids() -> set[str]:
    """Reused 2,211 answers are already kept. New answers use the 95% rule."""
    reused: set[str] = set()
    fresh: list[dict] = []
    for row in load_jsonl(FACTS_PATH):
        if not row.get("case_id") or row.get("error"):
            continue
        if row.get("reused"):
            reused.add(row["case_id"])
        else:
            fresh.append(row)
    return reused | strong_ids(fresh)


def attempt_counts() -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in load_jsonl(FACTS_PATH):
        if row.get("case_id") and not row.get("error") and not row.get("reused"):
            counts[row["case_id"]] += 1
    return counts


@dataclass
class Store:
    policies: list[dict[str, Any]]
    index: dict[str, dict[str, Any]]
    cards: list[CaseCard]
    counts: dict[str, Any]
    n_cached: int
    n_mismatch: int
    facts_mtime: float
    catalog_mtime: float

    @property
    def partial(self) -> bool:
        return self.n_cached != int(self.counts.get("n_cases") or 0)

    def policy(self, key: str) -> dict[str, Any] | None:
        return self.index.get(key)

    def cards_for(self, list_id: str) -> list[CaseCard]:
        return [card for card in self.cards if card.list_id == list_id]


def _mtime(path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _card(case: dict[str, Any], policy: dict[str, Any], fact: dict[str, Any] | None) -> tuple[CaseCard, bool]:
    choices = dict((fact or {}).get("choices") or {})
    cached = bool(fact) and not fact.get("error") and answers_complete(policy, choices)
    facts: dict[str, Any] = {}
    scope_payer = ""
    scope_date = ""
    if cached:
        facts = facts_from_answers(choices, list(policy.get("facts") or []))
        scope_payer = choices[PAYER_Q]
        scope_date = choices[DATE_Q]
    window = policy.get("window") or {}
    card = CaseCard(
        case_id=case["case_id"],
        list_id=policy["id"],
        policy_id=str(policy.get("policy_id") or ""),
        family=policy.get("family") or "",
        state=policy.get("state") or "",
        payer=policy.get("payer") or "",
        request_payer=case.get("request_payer") or "",
        service_date=case.get("service_date") or "",
        window_from=window.get("from") or "",
        window_through=window.get("through") or "",
        connective=policy.get("connective") or "",
        split=case.get("split") or "",
        stage=case.get("stage") or "",
        title=policy.get("title") or "",
        drug=policy.get("drug"),
        deps={key: list(values) for key, values in (policy.get("rule") or {}).items()},
        criteria_text={item["name"]: item.get("text") or "" for item in policy.get("criteria") or []},
        fact_text={item["fact"]: item.get("text") or "" for item in policy.get("clauses") or []},
        facts=facts,
        scope_payer=scope_payer,
        scope_date=scope_date,
        gold=case.get("gold"),
        cached=cached,
    )
    mismatch = False
    if cached:
        stamp_baseline(card)
        recorded = (fact or {}).get("decision")
        if recorded and recorded != card.base_decision:
            mismatch = True
    return card, mismatch


def _load(facts_mtime: float, catalog_mtime: float) -> Store:
    payload = load_policies()
    policies = list(payload.get("policies") or [])
    index = index_policies(policies)
    facts = latest_success(FACTS_PATH)
    cards: list[CaseCard] = []
    mismatch = 0
    for case in load_case_rows():
        policy = index.get(case["list_id"])
        if policy is None:
            continue
        card, bad = _card(case, policy, facts.get(case["case_id"]))
        cards.append(card)
        if bad:
            mismatch += 1
    cached = sum(1 for card in cards if card.cached)
    return Store(
        policies=policies,
        index=index,
        cards=cards,
        counts=dict(payload.get("counts") or {}),
        n_cached=cached,
        n_mismatch=mismatch,
        facts_mtime=facts_mtime,
        catalog_mtime=catalog_mtime,
    )


def get_store(*, force: bool = False) -> Store:
    global _CACHE
    facts_mtime = _mtime(FACTS_PATH)
    catalog_mtime = _mtime(POLICIES_PATH) + _mtime(CASES_PATH)
    with _LOCK:
        if (
            not force
            and _CACHE is not None
            and _CACHE.facts_mtime == facts_mtime
            and _CACHE.catalog_mtime == catalog_mtime
        ):
            return _CACHE
        _CACHE = _load(facts_mtime, catalog_mtime)
        return _CACHE


def _batch_seconds(rows: list[dict]) -> float:
    seen: dict[str, float] = {}
    for i, row in enumerate(rows):
        key = str(row.get("batch_id") or f"row-{i}")
        seen[key] = float(row.get("batch_latency_ms") or row.get("latency_ms") or 0.0)
    return sum(seen.values()) / 1000.0


def _fact_rows() -> tuple[list[dict], list[dict]]:
    """Latest success per case, and error-only cases."""
    last_ok: dict[str, dict] = {}
    last_any: dict[str, dict] = {}
    order: list[str] = []
    for row in load_jsonl(FACTS_PATH):
        cid = row.get("case_id")
        if not cid:
            continue
        if cid not in last_any:
            order.append(cid)
        last_any[cid] = row
        if not row.get("error"):
            last_ok[cid] = row
    ok = [last_ok[cid] for cid in order if cid in last_ok]
    bad = [cid for cid in order if cid not in last_ok]
    return ok, [{"case_id": cid} for cid in bad]


def build_presets(store: Store, *, example_limit: int = 12) -> dict[str, Any]:
    scenarios = []
    for spec in preset_specs():
        try:
            scenarios.append(run_preset(
                spec, store.policies, store.cards, example_limit=example_limit,
            ))
        except ValueError as exc:
            scenarios.append({
                "id": spec["id"],
                "title": spec["title"],
                "hypothetical": True,
                "error": str(exc),
                "n_flips": 0,
                "flip_counts": {},
            })
    return {
        "hypothetical": True,
        "simulated": False,
        "partial": store.partial,
        "n_cached": store.n_cached,
        "n_cases": store.counts.get("n_cases"),
        "computed_at": now_iso(),
        "note": (
            "Each scenario is a hypothetical what-if. Counts use cached Drex "
            "facts and the deterministic rule. They are not a published policy change."
        ),
        "scenarios": scenarios,
    }


def write_presets(store: Store) -> dict[str, Any]:
    payload = build_presets(store)
    write_json(PRESETS_PATH, payload)
    return payload


def publish(status: str, *, note: str = "", with_presets: bool = True,
            with_doc: bool = True) -> dict[str, Any]:
    """Write the summary, the compact replay, and (by default) presets and the doc."""
    store = get_store(force=True)
    ok, bad = _fact_rows()
    n = len(ok)
    correct = sum(1 for row in ok if row.get("correct"))
    criteria_ok = sum(1 for row in ok if row.get("criteria_all_correct"))
    facts_ok = sum(1 for row in ok if row.get("facts_all_correct"))
    labels = Counter(row.get("label") for row in ok)
    colours = Counter(row.get("colour") for row in ok)
    row_cost = sum(float(row.get("cost_usd") or 0.0) for row in ok)
    reused = [row for row in ok if row.get("reused")]
    reused_cost = sum(float(row.get("cost_usd") or 0.0) for row in reused)
    seconds = _batch_seconds(ok)
    spend = load_full_spend()
    simulated = any(bool(row.get("simulated")) for row in ok)
    model = next((row.get("model") for row in ok if row.get("model")), "drex-max-v1.1")
    drex = {
        "n": n,
        "n_error": len(bad),
        "n_correct": correct,
        "accuracy": (correct / n) if n else None,
        "criteria_accuracy": (criteria_ok / n) if n else None,
        "facts_accuracy": (facts_ok / n) if n else None,
        "by_label": dict(labels),
        "by_colour": dict(colours),
        "total_cost_usd": round(row_cost, 8),
        "reused_n": len(reused),
        "reused_cost_usd": round(reused_cost, 8),
        "new_row_cost_usd": round(row_cost - reused_cost, 8),
        "cost_per_1000": (row_cost / n * 1000.0) if n else None,
        "service_seconds": round(seconds, 3),
        "cases_per_minute": (n / seconds * 60.0) if seconds > 0 else None,
        "simulated": simulated,
        "model": model,
        "n_rederive_mismatch": store.n_mismatch,
    }
    summary = {
        "status": status,
        "note": note,
        "hypothetical_edits": True,
        "n_planned": store.counts.get("n_cases"),
        "n_cases": store.counts.get("n_cases"),
        "n_initial": store.counts.get("n_initial"),
        "n_followup": store.counts.get("n_followup"),
        "n_policies": store.counts.get("n_policies"),
        "n_cached": store.n_cached,
        "by_state": store.counts.get("by_state"),
        "partial": status != "complete",
        "computed_at": now_iso(),
        "drex": drex,
        "spend": {
            "nace_usd": spend["new_usd"],
            "nace_cap": FULL_NACE_CAP_USD,
            "scale_usd": spend["scale_usd"],
            "criterion_usd": spend["criterion_usd"],
            "criterion_cap": NEW_CRITERION_CAP_USD,
            "reused_cost_usd": drex["reused_cost_usd"],
            "row_cost_usd": drex["total_cost_usd"],
            "note": (
                "nace_usd is new Drex spend for this full-queue run, cap $1.00. "
                "reused_cost_usd was already paid on the earlier queue and is not "
                "charged again. row_cost_usd sums the cost stored on each decided row."
            ),
        },
        "model": model,
        "simulated": simulated,
    }
    write_json(FULL_SUMMARY_PATH, summary)
    lines = []
    for row in ok:
        lines.append({
            "case_id": row.get("case_id"),
            "decision": row.get("decision"),
            "colour": row.get("colour"),
            "label": row.get("label"),
            "split": row.get("split"),
            "stage": row.get("stage"),
            "latency_ms": row.get("latency_ms"),
            "cost_usd": row.get("cost_usd"),
            "correct": bool(row.get("correct")),
            "gold_decision": row.get("gold_decision"),
        })
    atomic_write(
        FULL_REPLAY_PATH,
        "".join(__import__("json").dumps(item, separators=(",", ":")) + "\n" for item in lines),
    )
    if with_presets:
        presets = write_presets(store)
        summary["presets"] = [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "hypothetical": True,
                "n_affected": item.get("n_affected"),
                "n_decided": item.get("n_decided"),
                "n_flips": item.get("n_flips"),
                "flip_counts": item.get("flip_counts"),
                "partial": item.get("partial"),
                "policy": item.get("policy"),
            }
            for item in presets.get("scenarios") or []
        ]
        write_json(FULL_SUMMARY_PATH, summary)
    src = "command_center/full_replay.jsonl"
    extra = {"partial": status != "complete", "n_planned": summary["n_planned"]}
    if n:
        write_metric("cc_full_decision_accuracy.json", drex["accuracy"], n, src,
                     simulated=simulated, extra=extra)
        write_metric("cc_full_criteria_accuracy.json", drex["criteria_accuracy"], n, src,
                     simulated=simulated, extra=extra)
        write_metric("cc_full_facts_accuracy.json", drex["facts_accuracy"], n, src,
                     simulated=simulated, extra=extra)
        write_metric("cc_full_cases_per_minute.json", drex["cases_per_minute"], n, src,
                     simulated=simulated, extra=extra)
        write_metric("cc_full_cost_per_1000.json", drex["cost_per_1000"], n, src,
                     simulated=simulated, extra=extra)
        write_metric("cc_full_n.json", n, n, src, simulated=simulated, extra=extra)
        write_metric("cc_full_row_cost_usd.json", drex["total_cost_usd"], n, src,
                     simulated=simulated, extra=extra)
    write_metric("cc_full_nace_spend_usd.json", spend["new_usd"], n, src,
                 simulated=False, extra={"cap_usd": FULL_NACE_CAP_USD})
    if with_doc:
        from billproof.command_center.report import write_doc
        write_doc()
    return summary


def replay_full() -> list[dict]:
    if not FULL_REPLAY_PATH.is_file():
        return []
    return load_jsonl(FULL_REPLAY_PATH)
