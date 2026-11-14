"""Paired Drex/GPT question rows from recorded run files. No model calls.

Every loader returns one dict per case that both lanes answered:

    {
      "case_id": str,
      "questions": [{"id", "family", "conf", "drex_ok", "gpt_ok", "drex_choice", "gpt_choice", "gold"}],
      "drex_cost_usd": float, "drex_ms": float,
      "gpt_input_tokens": int, "gpt_output_tokens": int, "gpt_ms": float,
      "case": {...}   # bench-specific case-level flags (PA decision, ...)
    }

`conf` is Drex's recorded native confidence for its chosen answer.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Iterator

from billproof.config import DATA_DIR

RUNS = DATA_DIR / "runs"
DREX_MODEL = "drex-max-v1.1"
GPT_MODEL = "gpt-6.1-sol"


def _iter_jsonl(path: Path) -> Iterator[dict]:
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _family(question_id: str) -> str:
    if question_id == "bill_balance":
        return "bill_balance"
    head, _, tail = question_id.partition("_L")
    return head if tail else question_id.split("::")[0]


# ------------------------------------------------------------------ hard


def _hard_gpt_rows() -> list[dict]:
    """Packed GPT on the frozen 100, in run order ( gpt_subset_rows)."""
    from billproof.bench.hard.outcomes import gpt_subset_rows

    return gpt_subset_rows()


def _hard_choice(row: dict, name: str) -> str | None:
    """Same rule as bench.hard.outcome_score._choice: invalid -> None."""
    if (row.get("invalid") or {}).get(name):
        return None
    payload = (row.get("answers") or {}).get(name) or {}
    if payload.get("invalid"):
        return None
    choice = payload.get("choice")
    return choice if choice in ("yes", "no") else None


def load_hard() -> list[dict]:
    gpt_rows = _hard_gpt_rows()
    order = [row["case_id"] for row in gpt_rows]
    wanted = set(order)
    drex: dict[str, dict] = {}
    for row in _iter_jsonl(RUNS / "hard_test_drex-v1.answers.jsonl.gz"):
        case_id = row["case_id"]
        if case_id not in wanted:
            continue
        box = drex.setdefault(case_id, {"answers": {}, "cost": row.get("cost_usd"), "ms": row.get("latency_ms")})
        box["answers"][row["question_id"]] = row
    cases = []
    for gpt in gpt_rows:
        case_id = gpt["case_id"]
        box = drex.get(case_id)
        if box is None:
            continue
        gold = gpt.get("gold") or {}
        questions = []
        for name, gold_choice in gold.items():
            answer = box["answers"].get(name)
            if answer is None:
                continue
            drex_choice = answer.get("answer") if answer.get("answer") in ("yes", "no") else None
            gpt_choice = _hard_choice(gpt, name)
            questions.append({
                "id": name,
                "family": _family(name),
                "conf": float(answer.get("confidence") or 0.0),
                "drex_choice": drex_choice,
                "gpt_choice": gpt_choice,
                "gold": gold_choice,
                "drex_ok": drex_choice == gold_choice,
                "gpt_ok": gpt_choice == gold_choice,
            })
        cases.append({
            "case_id": case_id,
            "questions": questions,
            "drex_cost_usd": float(box["cost"] or 0.0),
            "drex_ms": float(box["ms"] or 0.0),
            "gpt_input_tokens": int(gpt["input_tokens"]),
            "gpt_output_tokens": int(gpt["output_tokens"]),
            "gpt_ms": float(gpt["latency_ms"]),
            "case": {},
        })
    return cases


def load_hard_measured() -> list[dict]:
    """The recorded family-hybrid rows on the frozen 100 (the measured checkpoint)."""
    from billproof.bench.hard.outcomes import frozen_subset_rows

    try:
        return frozen_subset_rows("family_hybrid")
    except Exception:
        order = [row["case_id"] for row in _hard_gpt_rows()]
        found = {row["case_id"]: row for row in _iter_jsonl(RUNS / "hard_test_family_hybrid.jsonl")}
        return [found[case_id] for case_id in order if case_id in found]


def hard_family_taus() -> dict:
    return json.loads((RUNS / "hard_family_tau.json").read_text())


# ------------------------------------------------------------------ PA


def load_pa() -> list[dict]:
    """The 370 shared held-out PA requests (the ids of the recorded hybrid column)."""
    ids = [row["case_id"] for row in _iter_jsonl(RUNS / "pa_hybrid_development.jsonl")]
    drex = {row["case_id"]: row for row in _iter_jsonl(RUNS / "pa_drex_development.jsonl")}
    gpt = {row["case_id"]: row for row in _iter_jsonl(RUNS / "pa_gpt_development.jsonl")}
    cases = []
    for case_id in ids:
        d, g = drex.get(case_id), gpt.get(case_id)
        if d is None or g is None:
            continue
        d_score, g_score = d.get("score") or {}, g.get("score") or {}
        questions = []
        for name, qa in (d.get("qa") or {}).items():
            kind, _, field = name.partition("::")
            if kind == "fact":
                drex_ok = bool((d_score.get("per_fact") or {}).get(field))
                gpt_ok = bool((g_score.get("per_fact") or {}).get(field))
                gpt_choice = ((g.get("prediction") or {}).get("facts") or {}).get(field)
            else:
                drex_ok = bool(d_score.get("scope"))
                gpt_ok = bool(g_score.get("scope"))
                gpt_choice = (g.get("prediction") or {}).get("scope")
            questions.append({
                "id": name,
                "family": kind,
                "conf": float(qa.get("confidence") or 0.0),
                "drex_choice": qa.get("choice"),
                "gpt_choice": gpt_choice,
                "gold": None,
                "drex_ok": drex_ok,
                "gpt_ok": gpt_ok,
            })
        d_parts, g_parts = d_score.get("parts") or {}, g_score.get("parts") or {}
        cases.append({
            "case_id": case_id,
            "questions": questions,
            "drex_cost_usd": float(d.get("cost_usd") or 0.0),
            "drex_ms": float(d.get("latency_ms") or 0.0),
            "gpt_input_tokens": int(g.get("input_tokens") or 0),
            "gpt_output_tokens": int(g.get("output_tokens") or 0),
            "gpt_ms": float(g.get("latency_ms") or 0.0),
            "case": {
                # Case-level parts other than the per-question facts and scope.
                "drex_rest": all(v for k, v in d_parts.items() if k not in ("facts_all", "scope")) and d.get("error") is None,
                "gpt_rest": all(v for k, v in g_parts.items() if k not in ("facts_all", "scope")) and g.get("error") is None,
                "drex_decision": bool(d_score.get("decision")) and d.get("error") is None,
                "gpt_decision": bool(g_score.get("decision")) and g.get("error") is None,
            },
        })
    return cases


# ------------------------------------------------------------------ Persius


def load_persius() -> list[dict]:
    drex: dict[str, dict] = {}
    gpt: dict[str, dict] = {}
    order: list[str] = []
    for row in _iter_jsonl(RUNS / "c_persius_test.jsonl"):
        case_id = row.get("case_id")
        if row.get("lane") == "drex":
            if case_id not in drex:
                order.append(case_id)
            drex[case_id] = row
        elif row.get("lane") == "frontier":
            gpt[case_id] = row
    cases = []
    for case_id in order:
        d, g = drex[case_id], gpt.get(case_id)
        if g is None:
            continue
        # Invalid or errored answers count wrong (the published rule).
        drex_ok = bool(d.get("correct")) and d.get("error") is None
        gpt_ok = bool(g.get("correct")) and g.get("error") is None
        cases.append({
            "case_id": case_id,
            "questions": [{
                "id": "overturn",
                "family": "overturn",
                "conf": float(d.get("confidence") or 0.0),
                "drex_choice": d.get("choice"),
                "gpt_choice": g.get("choice"),
                "gold": d.get("gold"),
                "drex_ok": drex_ok,
                "gpt_ok": gpt_ok,
            }],
            "drex_cost_usd": float(d.get("cost_usd") or 0.0),
            "drex_ms": float(d.get("latency_ms") or 0.0),
            "gpt_input_tokens": int(g.get("input_tokens") or 0),
            "gpt_output_tokens": int(g.get("output_tokens") or 0),
            "gpt_ms": float(g.get("latency_ms") or 0.0),
            "case": {},
        })
    return cases


# ------------------------------------------------------------------ synthetic


def load_synthetic() -> list[dict]:
    drex: dict[str, dict] = {}
    gpt: dict[str, dict] = {}
    order: list[str] = []
    for row in _iter_jsonl(RUNS / "real_synthetic_test.jsonl"):
        case_id = row.get("case_id")
        if row.get("lane") == "drex":
            if case_id not in drex:
                order.append(case_id)
            drex[case_id] = row
        elif row.get("lane") == "frontier":
            gpt[case_id] = row
    cases = []
    for case_id in order:
        d, g = drex[case_id], gpt.get(case_id)
        if g is None:
            continue
        d_ok, g_ok = d.get("correct") or {}, g.get("correct") or {}
        d_ans, g_ans = d.get("answers") or {}, g.get("answers") or {}
        gold = d.get("gold") or {}
        questions = []
        for name in d_ok:
            answer = d_ans.get(name) or {}
            questions.append({
                "id": name,
                "family": _family(name),
                "conf": float(answer.get("confidence") or 0.0),
                "drex_choice": answer.get("choice"),
                "gpt_choice": (g_ans.get(name) or {}).get("choice"),
                "gold": gold.get(name),
                "drex_ok": bool(d_ok.get(name)),
                "gpt_ok": bool(g_ok.get(name)),
            })
        cases.append({
            "case_id": case_id,
            "questions": questions,
            "drex_cost_usd": float(d.get("cost_usd") or 0.0),
            "drex_ms": float(d.get("latency_ms") or 0.0),
            "gpt_input_tokens": int(g.get("input_tokens") or 0),
            "gpt_output_tokens": int(g.get("output_tokens") or 0),
            "gpt_ms": float(g.get("latency_ms") or 0.0),
            "case": {},
        })
    return cases


LOADERS = {
    "hard": load_hard,
    "pa_criteria_gym": load_pa,
    "persius": load_persius,
    "synthetic_v1": load_synthetic,
}
