"""One-case calls, resumable JSONL, shared spend cap. No gold is sent to a model."""
from __future__ import annotations

import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from billproof.adapters.frontier import FrontierAdapter
from billproof.bench.hard.policy import (
    DREX_MODELS, GPT_SYSTEM, HYBRID_DREX_LANE, HYBRID_TAU_CEILING, NACE_CAP_USD,
    NACE_LANES, ROUTER_CAP_USD, ROUTER_LANES,
)
from billproof.config import RUNS_DIR, settings
from billproof.synth.hard.questions import questions_for, visible_state

import billproof.adapters.frontier as frontier_mod


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def runs_dir() -> Path:
    path = RUNS_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


_ARCHIVED_TEST = frozenset({"drex-v1", "drex-finance"})


def jsonl_path(split: str, lane: str, run_id: str | None = None) -> Path:
    name = run_id or f"hard_{split}_{lane}"
    legacy = runs_dir() / f"{name}.jsonl"
    if run_id is None and split == "test" and lane in _ARCHIVED_TEST:
        full = runs_dir() / "full" / f"{name}.jsonl"
        # Prefer the gitignored rich log. A checkout that only has the gzip
        # still points here; read_jsonl expands the archive when it is missing.
        if full.exists() or not legacy.exists():
            return full
        return legacy
    return legacy


def load_cases(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def done_ids(path: Path) -> set[str]:
    """Last row per case. An unpaid exception is retried. A billed row stays."""
    if not path.exists():
        from billproof.bench.hard.drex_archive import case_ids_for

        return case_ids_for(path)
    last: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("case_id"):
            last[row["case_id"]] = row
    done = set()
    for case_id, row in last.items():
        unpaid = bool(row.get("error")) and not float(row.get("cost_usd") or 0)
        if not unpaid:
            done.add(case_id)
    return done


def _iter_rows():
    """Every stored hard row, once. Archived Drex lanes contribute one case row when the rich log is absent."""
    seen: set[str] = set()
    roots = [runs_dir()]
    full = runs_dir() / "full"
    if full.is_dir():
        roots.append(full)
    for root in roots:
        for path in sorted(root.glob("hard_*.jsonl")):
            if path.name in seen:
                continue
            seen.add(path.name)
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    from billproof.bench.hard.drex_archive import missing_case_rows

    yield from missing_case_rows(seen)


def spend() -> tuple[float, float]:
    router = nace = 0.0
    for row in _iter_rows():
        cost = float(row.get("cost_usd") or 0)
        lane = row.get("lane")
        if lane in ROUTER_LANES:
            router += cost
        elif lane in NACE_LANES:
            nace += cost
    return router, nace


def _reserve(lane: str) -> float:
    costs = [float(row.get("cost_usd") or 0) for row in _iter_rows()
             if row.get("lane") == lane and not row.get("error")]
    if lane in NACE_LANES:
        return max(0.004, (max(costs) * 1.5 if costs else 0.004))
    if lane == "gpt-one":
        return max(0.08, (max(costs) * 1.5 if costs else 0.08))
    return max(0.40, (max(costs) * 1.25 if costs else 0.40))


def allow(lane: str, phase_cap: float) -> tuple[bool, str]:
    """False when another call might cross the phase cap or the hard cap."""
    lock = runs_dir() / "hard_spend.lock"
    with open(lock, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        router, nace = spend()
        reserve = _reserve(lane)
        if lane in ROUTER_LANES:
            if router + reserve > ROUTER_CAP_USD or router + reserve > phase_cap:
                return False, f"router ${router:.4f} + reserve ${reserve:.4f} would pass ${min(ROUTER_CAP_USD, phase_cap):.2f}"
        else:
            if nace + reserve > NACE_CAP_USD or nace + reserve > phase_cap:
                return False, f"nace ${nace:.4f} + reserve ${reserve:.4f} would pass ${min(NACE_CAP_USD, phase_cap):.2f}"
        return True, f"router ${router:.4f} nace ${nace:.4f} reserve ${reserve:.4f}"


def append_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = runs_dir() / "hard_spend.lock"
    with open(lock, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        with path.open("a") as out:
            out.write(json.dumps(row, separators=(",", ":")) + "\n")


def _invalid(answer, call_error: str | None) -> bool:
    if call_error or answer is None:
        return True
    if answer.choice not in ("yes", "no"):
        return True
    raw = answer.raw if isinstance(answer.raw, dict) else {}
    if raw.get("error"):
        return True
    if raw == {}:
        return True
    return False


def grade(record: dict, questions, result) -> dict:
    gold = record["gold"]
    answers, correct, invalid = {}, {}, {}
    for q in questions:
        answer = result.answers.get(q.name)
        bad = _invalid(answer, result.error)
        choice = answer.choice if answer is not None else ""
        answers[q.name] = {
            "choice": choice if not bad else None,
            "confidence": None if answer is None else answer.confidence,
            "confidence_kind": None if answer is None else answer.confidence_kind,
            "probabilities": None if answer is None else answer.probabilities,
            "invalid": bad,
        }
        invalid[q.name] = bad
        correct[q.name] = (not bad) and choice == gold[q.name]
    return {
        "answers": answers,
        "gold": {q.name: gold[q.name] for q in questions},
        "correct": correct,
        "invalid": invalid,
        "case_correct": bool(correct) and all(correct.values()) and len(correct) == len(questions),
        "n_questions": len(questions),
        "n_correct": sum(1 for v in correct.values() if v),
        "n_invalid": sum(1 for v in invalid.values() if v),
    }


def _base_row(record: dict, lane: str, model: str, simulated: bool) -> dict:
    return {
        "run_id": f"hard_{record['split']}_{lane}",
        "bench": "hard",
        "case_id": record["case_id"],
        "split": record["split"],
        "lane": lane,
        "model": model,
        "simulated": simulated,
        "ts": now(),
    }


def install_gpt_system() -> None:
    """This process only. Does not edit adapters/frontier.py on disk."""
    frontier_mod._DECIDE_SYSTEM = GPT_SYSTEM


def gpt_adapter() -> FrontierAdapter:
    install_gpt_system()
    return FrontierAdapter(client=httpx.Client(timeout=240.0))


def drex_adapter(model: str):
    from billproof.adapters.drex import DEFAULT_PRICE_PER_MTOK, DrexAdapter
    cfg = settings()
    return DrexAdapter(
        cfg.nace_api_key, cfg.nace_base_url, model,
        cfg.drex_price_per_mtok or DEFAULT_PRICE_PER_MTOK,
        client=httpx.Client(timeout=180.0), max_retries=5,
    )


def call_lane(record: dict, lane: str, adapter) -> dict:
    questions = questions_for(record)
    state = visible_state(record)
    assert "gold" not in state
    result = adapter.decide(state, questions, case_id=record["case_id"])
    row = _base_row(record, lane, result.model, bool(result.simulated))
    row.update(grade(record, questions, result))
    row.update({
        "latency_ms": result.latency_ms,
        "cost_usd": result.cost_usd,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "error": result.error,
        "request_id": result.request_id,
    })
    return row


def one_question(record: dict):
    import hashlib
    questions = questions_for(record)
    digest = hashlib.sha256(f"gpt-one|{record['case_id']}".encode()).digest()
    return questions[int.from_bytes(digest[:4], "big") % len(questions)]


def call_gpt_one(record: dict, adapter) -> dict:
    question = one_question(record)
    state = visible_state(record)
    result = adapter.decide(state, [question], case_id=record["case_id"] + "|one")
    row = _base_row(record, "gpt-one", result.model, bool(result.simulated))
    graded = grade(record, [question], result)
    row.update(graded)
    row.update({
        "latency_ms": result.latency_ms,
        "cost_usd": result.cost_usd,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "error": result.error,
        "request_id": result.request_id,
        "question": question.name,
        "packing": "one question per call; not a primary lane",
    })
    return row


def _load_lane_map(split: str, lane: str) -> dict[str, dict]:
    from billproof.bench.hard.metrics import read_jsonl

    return {row["case_id"]: row for row in read_jsonl(jsonl_path(split, lane))}


def call_hybrid(record: dict, adapter, tau: float) -> dict | None:
    drex_row = _load_lane_map(record["split"], HYBRID_DREX_LANE).get(record["case_id"])
    if drex_row is None or drex_row.get("error"):
        return None
    ceiling = min(tau, HYBRID_TAU_CEILING) if record["split"] == "test" else HYBRID_TAU_CEILING
    # On test, tau is the frozen cutoff. On dev, fetch everything below the ceiling
    # so the sweep can score every grid point without another call.
    fetch_below = ceiling if record["split"] == "test" else HYBRID_TAU_CEILING
    wanted = []
    for question in questions_for(record):
        payload = (drex_row.get("answers") or {}).get(question.name) or {}
        conf = payload.get("confidence")
        if conf is None or float(conf) < fetch_below:
            wanted.append(question)
    gpt_answers = {}
    gpt_cost = gpt_latency = 0.0
    gpt_in = gpt_out = 0
    gpt_error = None
    model = drex_row.get("model") or DREX_MODELS["drex-v1"]
    if wanted:
        result = adapter.decide(visible_state(record), wanted, case_id=record["case_id"] + "|hybrid")
        gpt_error = result.error
        gpt_cost = float(result.cost_usd or 0)
        gpt_latency = float(result.latency_ms or 0)
        gpt_in = int(result.input_tokens or 0)
        gpt_out = int(result.output_tokens or 0)
        model = f"{drex_row.get('model')}+{result.model}"
        # A failed GPT call keeps the Drex answers. Invalid per-question choices still count wrong.
        if not gpt_error:
            gpt_answers = grade(record, wanted, result)["answers"]
    merged_q = questions_for(record)
    # Grade a synthetic result-like namespace via the stored drex answers + gpt.
    from billproof.schema import Answer, DecisionResult
    merged = {}
    for question in merged_q:
        drex_payload = (drex_row.get("answers") or {}).get(question.name) or {}
        conf = float(drex_payload.get("confidence") or 0)
        use_gpt = conf < tau and question.name in gpt_answers
        source = gpt_answers[question.name] if use_gpt else drex_payload
        raw = {"error": "invalid"} if source.get("invalid") or not source.get("choice") else {"kept": True}
        merged[question.name] = Answer(
            name=question.name,
            choice=source.get("choice") or "yes",
            probabilities=source.get("probabilities"),
            confidence=float(source.get("confidence") or 0),
            confidence_kind=source.get("confidence_kind") or "native",
            raw=raw,
        )
    # A failed GPT subset must not wipe the Drex answers that were kept.
    fake = DecisionResult(
        lane="hybrid", model=model, answers=merged, latency_ms=float(drex_row.get("latency_ms") or 0) + gpt_latency,
        input_tokens=gpt_in, output_tokens=gpt_out, cost_usd=gpt_cost, error=None,
    )
    row = _base_row(record, "hybrid", model, bool(drex_row.get("simulated")))
    row.update(grade(record, merged_q, fake))
    # Restore route after grade() rebuilt the answer dicts.
    for question in merged_q:
        drex_payload = (drex_row.get("answers") or {}).get(question.name) or {}
        conf = float(drex_payload.get("confidence") or 0)
        route = "frontier" if conf < tau and question.name in gpt_answers else "drex"
        row["answers"][question.name]["route"] = route
    row.update({
        "latency_ms": fake.latency_ms,
        "cost_usd": gpt_cost,
        "input_tokens": gpt_in,
        "output_tokens": gpt_out,
        "error": None,
        "gpt_error": gpt_error,
        "tau": tau,
        "tau_ceiling": fetch_below,
        "escalated": [q.name for q in wanted],
        "gpt_answers": gpt_answers,
        "packing": "fresh GPT call on low-confidence questions, no Drex draft",
    })
    return row


# Arithmetic is the rules engine. Drex judges the other four families.
SYSTEM_RULES = ("fee", "pr", "bill_balance")
SYSTEM_DREX = ("dup", "mue", "ncci", "eob")


def call_system(record: dict, adapter) -> dict:
    """Rules engine for fee, patient responsibility, and the bill total.

    Drex answers duplicate, MUE, NCCI, and EOB on the same question text and
    the same visible state. Fee, pr, and bill_balance match the oracle by
    construction and are labelled route "rules".
    """
    from billproof.schema import Answer, DecisionResult
    from billproof.synth.hard.oracle import answers as oracle_answers

    questions = questions_for(record)
    state = visible_state(record)
    assert "gold" not in state
    judge = [q for q in questions if q.family in SYSTEM_DREX]
    result = adapter.decide(state, judge, case_id=record["case_id"] + "|system")
    rules = oracle_answers(record)
    judged = {} if result.error else grade(record, judge, result)["answers"]
    merged = {}
    for question in questions:
        if question.family in SYSTEM_RULES:
            merged[question.name] = Answer(
                name=question.name, choice=rules[question.name], probabilities=None,
                confidence=1.0, confidence_kind="rule", raw={"rules": True},
            )
            continue
        payload = judged.get(question.name) or {}
        bad = bool(payload.get("invalid")) or payload.get("choice") not in ("yes", "no")
        merged[question.name] = Answer(
            name=question.name,
            choice=payload.get("choice") or "yes",
            probabilities=payload.get("probabilities"),
            confidence=float(payload.get("confidence") or 0),
            confidence_kind=payload.get("confidence_kind") or "native",
            raw={"error": "invalid"} if bad else {"kept": True},
        )
    model = f"rules+{result.model}"
    fake = DecisionResult(
        lane="system", model=model, answers=merged,
        latency_ms=float(result.latency_ms or 0),
        input_tokens=int(result.input_tokens or 0),
        output_tokens=int(result.output_tokens or 0),
        cost_usd=float(result.cost_usd or 0),
        error=None, simulated=bool(result.simulated),
    )
    row = _base_row(record, "system", model, bool(result.simulated))
    row.update(grade(record, questions, fake))
    for question in questions:
        row["answers"][question.name]["route"] = "rules" if question.family in SYSTEM_RULES else "drex"
    row.update({
        "latency_ms": fake.latency_ms,
        "cost_usd": fake.cost_usd,
        "input_tokens": fake.input_tokens,
        "output_tokens": fake.output_tokens,
        "error": result.error,
        "rules_families": list(SYSTEM_RULES),
        "drex_families": list(SYSTEM_DREX),
        "packing": "rules compute fee, pr, and bill_balance; Drex answers dup, mue, ncci, and eob",
    })
    return row


BILLPROOF_SYSTEM_MODEL = "BillProof system: code + Drex"


def call_billproof_system(record: dict, adapter) -> dict:
    """Code computes the arithmetic. Drex answers every yes/no question.

    The numbers come from plan, reference, bill, and eob. They are facts in
    the state. This lane does not import the label function. Fee, patient
    responsibility, and the balance sum are near-deterministic by design:
    money is computed by code, judgment by Drex.
    """
    from billproof.bench.hard.arithmetic import compute_facts

    questions = questions_for(record)
    state = visible_state(record)
    state["computed"] = compute_facts(record)
    assert "gold" not in state
    result = adapter.decide(state, questions, case_id=record["case_id"] + "|billproof-system")
    row = _base_row(record, "billproof-system", BILLPROOF_SYSTEM_MODEL, bool(result.simulated))
    row["drex_model"] = result.model
    row.update(grade(record, questions, result))
    row.update({
        "latency_ms": result.latency_ms,
        "cost_usd": result.cost_usd,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "error": result.error,
        "request_id": result.request_id,
        "packing": (
            "Code computes charge versus 150% of fee times units, expected patient "
            "responsibility, and the balance sum from the case inputs. Drex answers "
            "every yes/no question. Arithmetic families are near-deterministic by "
            "design: money is computed by code, judgment by Drex."
        ),
    })
    return row


def call_family_hybrid(record: dict, adapter, taus: dict[str, float], drex_row: dict | None) -> dict | None:
    """Drex first. Escalated questions get a fresh GPT call and no Drex draft.

    `taus` is the frozen per-family map. A tau of 1.0 escalates every question
    in that family. The state is the same visible state as the other GPT lanes.
    """
    from billproof.bench.hard.family_tau import escalates
    from billproof.schema import Answer, DecisionResult

    if drex_row is None or drex_row.get("error"):
        return None
    questions = questions_for(record)
    wanted = []
    for question in questions:
        payload = (drex_row.get("answers") or {}).get(question.name) or {}
        if escalates(question.family, payload.get("confidence"), taus):
            wanted.append(question)
    gpt_answers = {}
    gpt_cost = gpt_latency = 0.0
    gpt_in = gpt_out = 0
    gpt_error = None
    gpt_model = None
    gpt_simulated = False
    model = drex_row.get("model") or DREX_MODELS["drex-v1"]
    if wanted:
        state = visible_state(record)
        assert "gold" not in state and "computed" not in state
        result = adapter.decide(state, wanted, case_id=record["case_id"] + "|family-hybrid")
        gpt_error = result.error
        gpt_cost = float(result.cost_usd or 0)
        gpt_latency = float(result.latency_ms or 0)
        gpt_in = int(result.input_tokens or 0)
        gpt_out = int(result.output_tokens or 0)
        gpt_model = result.model
        gpt_simulated = bool(result.simulated)
        model = f"{drex_row.get('model')}+{result.model}"
        if not gpt_error:
            gpt_answers = grade(record, wanted, result)["answers"]
    merged = {}
    for question in questions:
        drex_payload = (drex_row.get("answers") or {}).get(question.name) or {}
        source = gpt_answers[question.name] if question.name in gpt_answers else drex_payload
        raw = {"error": "invalid"} if source.get("invalid") or not source.get("choice") else {"kept": True}
        merged[question.name] = Answer(
            name=question.name,
            choice=source.get("choice") or "yes",
            probabilities=source.get("probabilities"),
            confidence=float(source.get("confidence") or 0),
            confidence_kind=source.get("confidence_kind") or "native",
            raw=raw,
        )
    fake = DecisionResult(
        lane="family_hybrid", model=model, answers=merged,
        latency_ms=float(drex_row.get("latency_ms") or 0) + gpt_latency,
        input_tokens=gpt_in, output_tokens=gpt_out, cost_usd=gpt_cost, error=None,
    )
    row = _base_row(
        record, "family_hybrid", model,
        bool(drex_row.get("simulated")) or gpt_simulated,
    )
    row.update(grade(record, questions, fake))
    for question in questions:
        route = "frontier" if question.name in gpt_answers else "drex"
        row["answers"][question.name]["route"] = route
    row.update({
        "latency_ms": fake.latency_ms,
        "cost_usd": gpt_cost,
        "input_tokens": gpt_in,
        "output_tokens": gpt_out,
        "error": None,
        "gpt_error": gpt_error,
        "gpt_model": gpt_model,
        "taus": {family: float(tau) for family, tau in taus.items()},
        "escalated": [question.name for question in wanted],
        "n_escalated": len(wanted),
        "packing": "Drex first. Fresh GPT call on escalated questions, no Drex draft.",
    })
    return row
