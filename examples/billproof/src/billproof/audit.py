"""Audit desk: portfolio analytics for claim-audit firms, employers and TPAs.

A batch of claims (a split of the synthetic benchmark, replayed from a recorded
run or computed fresh through the decision lanes) becomes a portfolio:

- total billed vs. code-computed recoverable overpayment (``disputed_cents``
  comes from ``decisions.verdicts`` — models never set dollar figures),
- counts and dollars by error type,
- the exceptions queue: only the cases a human must touch, sorted by dollars
  at stake then confidence — "your team approves only the exceptions",
- automation rate, analyst hours saved (minutes-per-claim is a stated,
  caller-adjustable assumption), and the billed cost of the run.

source="replay" reads data/runs/<run_id>.jsonl (fast; L2's wire format).
source="sim" computes fresh verdicts in-process with mocks forced on.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Literal

from billproof.config import RUNS_DIR, settings
from billproof.schema import ErrorType

LANES = ("frontier", "drex", "hybrid")
SPLITS = ("dev", "test", "demo")
DEFAULT_MINUTES_PER_CLAIM = 12.0  # stated assumption, shown + editable in the UI

# noul answers that mark a case-level error family when answered "yes".
_CASE_ANSWER_ERRORS = {
    "eob_mismatch": ErrorType.EOB_BILL_MISMATCH,
    "nsa": ErrorType.NO_SURPRISES_ACT,
    "after_death": ErrorType.SERVICE_AFTER_DEATH,
    "missing_data": ErrorType.MISSING_DATA,
}

# Verdict line reasons / human_reasons that are not already ErrorType values.
_REASON_ALIASES = {
    "service_after_death": ErrorType.SERVICE_AFTER_DEATH,
    "missing_data": ErrorType.MISSING_DATA,
    "eob_or_balance_billing_mismatch": ErrorType.EOB_BILL_MISMATCH,
    "no_surprises_act_hard_cap": ErrorType.NO_SURPRISES_ACT,
    "service_receipt_needs_patient_confirmation": "ASK_PATIENT",
    "denial_appeal": "DENIAL_APPEAL",
}

ERROR_LABELS = {
    ErrorType.DUPLICATE: "Duplicate charge",
    ErrorType.MUE_UNITS_EXCEEDED: "Units over limit",
    ErrorType.NCCI_UNBUNDLED: "Unbundled codes",
    ErrorType.OVER_FEE_SCHEDULE: "Over fee schedule",
    ErrorType.EOB_BILL_MISMATCH: "EOB ≠ bill",
    ErrorType.SERVICE_AFTER_DEATH: "After date of death",
    ErrorType.MISSING_DATA: "Missing data",
    ErrorType.NO_SURPRISES_ACT: "Surprise billing",
    "ASK_PATIENT": "Ask the patient",
    "DENIAL_APPEAL": "Denial to appeal",
    "OTHER": "Other",
}

_HUMAN_REASON_LABELS = {
    "human_signature_required_for_money_movement_or_appeal": "Money or appeal — a person signs",
    "decision_escalated_to_human": "Low-confidence call, escalated",
    "no_surprises_act_hard_cap": "No Surprises Act — always human",
}

# Process notes, not billing errors: excluded from the error-type breakdown.
_PROCESS_REASONS = {
    "human_signature_required_for_money_movement_or_appeal",
    "decision_escalated_to_human",
}


class AuditError(Exception):
    """Raised when a batch cannot be produced (no runs, no cases, lane absent)."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_reason(reason: Any) -> str:
    text = str(reason or "")
    if not text:
        return "OTHER"
    if text in ErrorType.__members__ or text in {e.value for e in ErrorType}:
        return text
    lowered = text.lower()
    if lowered in _REASON_ALIASES:
        return str(_REASON_ALIASES[lowered])
    if lowered.startswith("denial_appeal"):
        return "DENIAL_APPEAL"
    return "OTHER"


def error_label(error_type: str) -> str:
    try:
        return ERROR_LABELS[ErrorType(error_type)]
    except (ValueError, KeyError):
        return ERROR_LABELS.get(error_type, str(error_type).replace("_", " ").title())


def human_reason_label(reason: str) -> str:
    return _HUMAN_REASON_LABELS.get(reason, reason.replace("_", " ").capitalize())


# ---------------------------------------------------------------- sources


def _newest_run_id(split: str) -> str | None:
    """The same default run the server picks: STAGE.json pin > LIVE run >
    canonical sim_<bench>_<split> > newest. Falls back to a local copy of that
    order when server.race isn't importable."""
    try:  # stay in lock-step with the race/demo picker when it is importable
        from billproof.server import race as _race

        return _race.pick_default_run("synthetic", split)[0]
    except Exception:
        pass
    if not RUNS_DIR.exists():
        return None
    metas: dict[str, dict] = {}
    for p in RUNS_DIR.glob("*.meta.json"):
        rid = p.name[: -len(".meta.json")]
        try:
            m = json.loads(p.read_text())
        except Exception:
            continue
        if m.get("bench") != "synthetic":
            continue
        if m.get("split") not in (None, split):
            continue
        if not (RUNS_DIR / f"{rid}.jsonl").exists():
            continue
        metas[rid] = m
    try:
        stage = json.loads((RUNS_DIR / "STAGE.json").read_text()).get("synthetic")
    except Exception:
        stage = None
    if stage and stage in metas:
        return stage
    live = [rid for rid, m in metas.items() if str(m.get("mode", "")).upper() == "LIVE"]
    if live:
        return max(live, key=lambda r: str(metas[r].get("created", "")))
    for cand in (f"sim_synthetic_{split}", "sim_synthetic_test", "sim_synthetic_dev"):
        if cand in metas:
            return cand
    if metas:
        return max(metas, key=lambda r: str(metas[r].get("created", "")))
    cands = sorted(RUNS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    return cands[-1].stem if cands else None


def _load_run(run_id: str) -> tuple[list[dict], dict]:
    path = RUNS_DIR / f"{run_id}.jsonl"
    if not path.exists():
        raise AuditError(f"run {run_id!r} not found in data/runs")
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    meta_path = RUNS_DIR / f"{run_id}.meta.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            meta = {}
    return rows, meta


def _replay_rows(split: str, lane: str, run_id: str | None) -> tuple[list[dict], dict, str]:
    rid = run_id or _newest_run_id(split)
    if not rid:
        raise AuditError(
            f"no recorded run for the {split} split in data/runs — "
            "run source=sim once (or the Race tab) to record one"
        )
    rows, meta = _load_run(rid)
    lane_rows = [r for r in rows if r.get("lane") == lane and r.get("split") in (None, split)]
    if not lane_rows:
        raise AuditError(f"run {rid} has no {lane!r} rows on split {split!r}")
    return lane_rows, meta, rid


def _sim_rows(split: str, lane: str) -> tuple[list[dict], list[dict]]:
    """Compute fresh verdicts for every case in the split. Mocks are forced on:
    a "sim" audit must never call real APIs (mirrors server/race.py)."""
    from billproof.data import list_cases, load_gold
    from billproof.decisions.lanes import run_lane
    from billproof.decisions.questions import build_questions, build_state
    from billproof.decisions.verdicts import compute_verdict
    from billproof.rules.checks import run_checks

    cases = list_cases(split)
    if not cases:
        raise AuditError(f"no synthetic cases on disk for split {split!r}")
    old_mock = os.environ.get("MOCK_MODE")
    os.environ["MOCK_MODE"] = "1"
    try:
        settings.cache_clear()
    except Exception:
        pass
    rows: list[dict] = []
    errors: list[dict] = []
    try:
        for case in cases:
            try:
                try:
                    gold = load_gold(case.case_id)
                except Exception:
                    gold = None
                flags = run_checks(case)
                questions = build_questions(case, flags)
                state = build_state(case, flags)
                result = run_lane(lane, case, questions, state, gold=gold)
                verdict = compute_verdict(case, flags, result.answers)
                rows.append(
                    {
                        "case_id": case.case_id,
                        "split": case.split,
                        "lane": lane,
                        "model": result.model,
                        "simulated": result.simulated,
                        "latency_ms": result.latency_ms,
                        "cost_usd": result.cost_usd,
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                        "answers": {
                            name: {
                                "choice": a.choice,
                                "confidence": a.confidence,
                                "confidence_kind": a.confidence_kind,
                                "route": a.route,
                            }
                            for name, a in result.answers.items()
                        },
                        "verdict": verdict.model_dump(mode="json"),
                        "ts": _utc_now(),
                    }
                )
            except Exception as exc:
                errors.append({"case_id": case.case_id, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        if old_mock is None:
            os.environ.pop("MOCK_MODE", None)
        else:
            os.environ["MOCK_MODE"] = old_mock
        try:
            settings.cache_clear()
        except Exception:
            pass
    if not rows:
        detail = errors[0]["error"] if errors else "unknown"
        raise AuditError(f"audit run failed for all {len(cases)} cases (first: {detail})")
    return rows, errors


# ---------------------------------------------------------------- aggregation


def _case_titles() -> dict[str, str]:
    try:
        from billproof.data import list_cases

        return {c.case_id: c.title for c in list_cases()}
    except Exception:
        return {}


def _confidence(answers: dict) -> float | None:
    confs = [float(a.get("confidence") or 0) for a in answers.values() if isinstance(a, dict)]
    if not confs:
        return None
    human = [
        float(a.get("confidence") or 0)
        for a in answers.values()
        if isinstance(a, dict) and a.get("route") in ("human", "hard_cap")
    ]
    return round(sum(human) / len(human), 4) if human else round(min(confs), 4)


def _aggregate(
    rows: list[dict],
    *,
    split: str,
    lane: str,
    source: str,
    mode: str,
    run_id: str | None,
    run_mode: str | None,
    label_sha256: str,
    minutes_per_claim: float,
    errors: list[dict],
) -> dict:
    titles = _case_titles()
    by_error: dict[str, dict[str, Any]] = {}
    exceptions: list[dict] = []
    total_billed = 0
    recoverable = 0
    owed_total = 0
    cost_usd = 0.0
    latency_ms = 0.0

    def track(error_type: str, cents: int, case_id: str, *, line: bool) -> None:
        entry = by_error.setdefault(error_type, {"count": 0, "cases": set(), "cents": 0})
        if line:
            entry["count"] += 1
        entry["cases"].add(case_id)
        entry["cents"] += max(0, int(cents or 0))

    for row in rows:
        case_id = str(row.get("case_id") or "")
        verdict = row.get("verdict") or {}
        answers = row.get("answers") or {}
        needs_human = bool(verdict.get("needs_human"))
        billed = int(verdict.get("billed_balance_cents") or 0)
        disputed = int(verdict.get("disputed_cents") or 0)
        owed = int(verdict.get("owed_cents") or 0)
        # Dollars at stake on this claim: the computed overpayment when there is
        # one; else the billed-vs-owed gap the human is being asked about;
        # else the whole billed balance (e.g. NSA where EOB is silent).
        at_stake = disputed or (max(0, billed - owed) if needs_human else 0) or (
            billed if needs_human else 0
        )

        total_billed += billed
        recoverable += disputed
        owed_total += owed
        cost_usd += float(row.get("cost_usd") or 0.0)
        latency_ms += float(row.get("latency_ms") or 0.0)

        case_error_types: set[str] = set()
        case_cents: dict[str, int] = {}
        for line_verdict in verdict.get("lines") or []:
            cents = int(line_verdict.get("disputed_cents") or 0)
            reasons = [r for r in (line_verdict.get("reasons") or []) if str(r) not in _PROCESS_REASONS]
            if not reasons:
                continue
            for reason in reasons:
                et = _norm_reason(reason)
                case_error_types.add(et)
                case_cents[et] = case_cents.get(et, 0) + max(0, cents)
                track(et, cents, case_id, line=True)

        for qname, et in _CASE_ANSWER_ERRORS.items():
            ans = answers.get(qname)
            if isinstance(ans, dict) and str(ans.get("choice", "")).lower() == "yes":
                et = str(et)
                if et not in case_error_types:
                    case_error_types.add(et)
                    case_cents[et] = case_cents.get(et, 0) + at_stake
                    track(et, at_stake, case_id, line=False)
        for reason in verdict.get("human_reasons") or []:
            if str(reason) in _PROCESS_REASONS:
                continue
            et = _norm_reason(reason)
            if et not in case_error_types:
                case_error_types.add(et)
                case_cents[et] = case_cents.get(et, 0) + at_stake
                track(et, at_stake, case_id, line=False)

        if needs_human:
            human_reasons = [str(r) for r in (verdict.get("human_reasons") or [])]
            primary = max(case_cents, key=case_cents.get) if case_cents else None
            exceptions.append(
                {
                    "case_id": case_id,
                    "title": titles.get(case_id, "Billing case"),
                    "action": verdict.get("case_action") or "INSUFFICIENT_EVIDENCE",
                    "at_stake_cents": at_stake,
                    "recoverable_cents": disputed,
                    "billed_cents": billed,
                    "owed_cents": owed,
                    "confidence": _confidence(answers),
                    "error_types": sorted(case_error_types),
                    "error_labels": [error_label(t) for t in sorted(case_error_types)],
                    "human_reasons": human_reasons,
                    "human_reason_labels": [human_reason_label(r) for r in human_reasons],
                    "primary_reason": primary,
                    "primary_label": error_label(primary) if primary else (
                        human_reason_label(human_reasons[0]) if human_reasons else "Needs review"
                    ),
                }
            )

    exceptions.sort(key=lambda e: (-e["at_stake_cents"], e["confidence"] if e["confidence"] is not None else 1.0))
    n_cases = len(rows)
    n_exceptions = len(exceptions)
    n_automated = n_cases - n_exceptions
    minutes = max(0.0, float(minutes_per_claim))
    breakdown = [
        {
            "error_type": et,
            "label": error_label(et),
            "lines": entry["count"],
            "cases": len(entry["cases"]),
            "cents": entry["cents"],
        }
        for et, entry in sorted(by_error.items(), key=lambda kv: -kv[1]["cents"])
    ]
    return {
        "ok": True,
        "split": split,
        "lane": lane,
        "source": source,
        "mode": mode,
        "run_mode": run_mode,
        "run_id": run_id,
        "label_sha256": label_sha256,
        "n_cases": n_cases,
        "n_exceptions": n_exceptions,
        "n_automated": n_automated,
        "automation_rate": round(n_automated / n_cases, 4) if n_cases else 0.0,
        "total_billed_cents": total_billed,
        "recoverable_cents": recoverable,
        "owed_cents": owed_total,
        "recoverable_pct": round(recoverable / total_billed, 4) if total_billed else 0.0,
        "by_error_type": breakdown,
        "exceptions": exceptions,
        "assumptions": {
            "minutes_per_claim": minutes,
            "note": "analyst time a human would have spent per cleared claim",
        },
        "analyst_hours_saved": round(n_automated * minutes / 60.0, 1),
        "cost_usd": round(cost_usd, 6),
        "cost_per_case_usd": round(cost_usd / n_cases, 6) if n_cases else 0.0,
        "latency_ms": round(latency_ms, 1),
        "errors": errors,
        "created": _utc_now(),
    }


# ---------------------------------------------------------------- public API


def run_batch(
    split: str = "test",
    lane: str = "hybrid",
    source: Literal["replay", "sim"] = "replay",
    *,
    run_id: str | None = None,
    minutes_per_claim: float = DEFAULT_MINUTES_PER_CLAIM,
) -> dict:
    """Audit a whole split of claims and return the portfolio dict (see module docstring)."""
    if lane not in LANES:
        raise ValueError(f"lane must be one of {LANES}")
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}")
    if source not in ("replay", "sim"):
        raise ValueError("source must be 'replay' or 'sim'")

    try:
        from billproof.data import label_sha256

        sha = label_sha256("synthetic")
    except Exception:
        sha = ""

    errors: list[dict] = []
    if source == "replay":
        rows, meta, rid = _replay_rows(split, lane, run_id)
        run_mode = str(meta.get("mode") or "SIMULATED").upper()
        mode = "REPLAY"
    else:
        rows, errors = _sim_rows(split, lane)
        rid = None
        run_mode = None
        mode = "SIMULATED"  # MOCK_MODE=1 was forced for the compute above
    return _aggregate(
        rows,
        split=split,
        lane=lane,
        source=source,
        mode=mode,
        run_id=rid,
        run_mode=run_mode,
        label_sha256=sha,
        minutes_per_claim=minutes_per_claim,
        errors=errors,
    )


def exceptions_queue(
    split: str = "test",
    lane: str = "hybrid",
    source: Literal["replay", "sim"] = "replay",
    *,
    run_id: str | None = None,
) -> dict:
    """Just the human-review queue (the exceptions slice of run_batch)."""
    portfolio = run_batch(split, lane, source, run_id=run_id)
    return {
        "ok": True,
        "split": portfolio["split"],
        "lane": portfolio["lane"],
        "source": portfolio["source"],
        "mode": portfolio["mode"],
        "run_id": portfolio["run_id"],
        "n_cases": portfolio["n_cases"],
        "n_exceptions": portfolio["n_exceptions"],
        "exceptions": portfolio["exceptions"],
    }
