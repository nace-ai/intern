#!/usr/bin/env python3
"""Resumable cost-ladder calls. One model per process. Set --model before imports.

Stops at $20 list-price spend or 1,200 counted router calls across ladder files.
A stopped prefix is the frozen id order. Items are never dropped by outcome.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(SCRIPTS))


def _parse():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--test", required=True, choices=("hard", "pa", "persius"))
    parser.add_argument("--prompt-only", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="prefix of the frozen order")
    return parser.parse_args()


ARGS = _parse()
os.environ["FRONTIER_MODEL"] = ARGS.model
if ARGS.prompt_only:
    os.environ["LADDER_PROMPT_ONLY"] = "1"

from billproof.config import RUNS_DIR, settings  # noqa: E402
from ladder_frontier import CAP_USD, LadderStop, configure  # noqa: E402

configure(ARGS.prompt_only)

PRICES = json.loads((RUNS_DIR / "ladder_prices.json").read_text())
MAX_ATTEMPTS = 3


def slug(model: str) -> str:
    return model.replace("/", "__").replace(" ", "_")


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def past_deadline() -> bool:
    return False


def price_row(model: str) -> dict | None:
    """Match vendor ids and dated router ids (gpt-5.4-mini-2026-03-17)."""
    tail = model.lower().rsplit("/", 1)[-1]
    if "flash-lite" in tail or "flash_lite" in tail:
        return PRICES["models"]["google/gemini-3.5-flash-lite"]
    if tail.startswith("gemini-3.6-flash") or tail == "gemini-3.5-flash" or (
        tail.startswith("gemini-3.5-flash-") and "lite" not in tail
    ):
        # Deprecated 3.5 Flash is billed as 3.6 Flash. Rate is the 2026 promotional price.
        return PRICES["models"]["google/gemini-3.5-flash"]
    prefixes = (
        ("gpt-5.4-mini", "openai/gpt-5.4-mini"),
        ("gpt-5.4-nano", "openai/gpt-5.4-nano"),
        ("claude-haiku-5-5", "anthropic/claude-haiku-5.5"),
        ("claude-haiku-5.5", "anthropic/claude-haiku-5.5"),
        ("deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash"),
        ("deepseek-v4-1-flash", "deepseek/deepseek-v4.1-flash"),
        ("deepseek-flash", "deepseek/deepseek-v4.1-flash"),
    )
    for prefix, key in prefixes:
        if tail.startswith(prefix):
            return PRICES["models"][key]
    return None


def list_cost(model: str, input_tokens: int, output_tokens: int) -> tuple[float | None, bool]:
    row = price_row(model)
    if row is None:
        return None, True
    pin = float(row["input_usd_per_mtok"])
    pout = float(row["output_usd_per_mtok"])
    if row.get("input_usd_per_mtok_over_100k") and input_tokens > 100_000:
        pin = float(row["input_usd_per_mtok_over_100k"])
        pout = float(row["output_usd_per_mtok_over_100k"])
    return (input_tokens * pin + output_tokens * pout) / 1_000_000, bool(row.get("estimated"))


def _iter_spend_rows():
    for path in sorted(RUNS_DIR.glob("ladder_*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def spent_usd() -> float:
    total = 0.0
    for row in _iter_spend_rows():
        total += float(row.get("cost_usd") or 0.0)
    return total


def out_path() -> Path:
    return RUNS_DIR / f"ladder_{ARGS.test}_{slug(ARGS.model)}.jsonl"


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def done_ids(path: Path) -> set[str]:
    last: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for row in load_rows(path):
        case_id = row.get("case_id")
        if not case_id:
            continue
        counts[case_id] = counts.get(case_id, 0) + 1
        last[case_id] = row
    done = set()
    for case_id, row in last.items():
        cost = float(row.get("cost_usd") or 0)
        if row.get("protocol_discard"):
            continue
        retry = bool(row.get("retry")) or (
            bool(row.get("error")) and cost == 0 and not row.get("invalid")
        )
        if retry and cost == 0 and counts[case_id] < MAX_ATTEMPTS:
            continue
        done.add(case_id)
    return done


def append_row(path: Path, row: dict) -> None:
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    lock = RUNS_DIR / "ladder_spend.lock"
    with open(lock, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        with path.open("a") as out:
            out.write(json.dumps(row, separators=(",", ":")) + "\n")
            out.flush()
            os.fsync(out.fileno())


def allow(reserve: float) -> None:
    import fcntl

    lock = RUNS_DIR / "ladder_spend.lock"
    with open(lock, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        spent = spent_usd()
        if spent + reserve > CAP_USD:
            raise LadderStop(f"list-price ${spent:.4f} + reserve ${reserve:.4f} would pass ${CAP_USD:.2f}")


def _transient(error: str | None) -> bool:
    if not error:
        return False
    text = str(error)
    return (
        text.startswith("frontier transport error")
        or text.startswith("frontier response")
        or text.startswith("HTTP 429")
        or text.startswith("HTTP 5")
    )


def run_hard(limit: int) -> None:
    from billproof.adapters.frontier import FrontierAdapter
    from billproof.bench.hard.runner import grade, install_gpt_system, load_cases
    from billproof.synth.hard.questions import questions_for, visible_state
    import httpx

    ids = json.loads((RUNS_DIR / "ladder_hard_ids.json").read_text())["run_order"]
    if limit:
        ids = ids[:limit]
    by_id = {}
    for record in load_cases(ROOT / "data" / "synthetic" / "hard" / "test.jsonl"):
        if record["case_id"] in ids:
            by_id[record["case_id"]] = record
    missing = [case_id for case_id in ids if case_id not in by_id]
    if missing:
        raise SystemExit(f"hard ids missing from test.jsonl: {missing[:3]}")
    install_gpt_system()
    adapter = FrontierAdapter(client=httpx.Client(timeout=240.0))
    path = out_path()
    finished = done_ids(path)
    reserve = 0.05
    observed = 0.0
    cfg = settings()
    print(f"start hard model={cfg.frontier_model} n={len(ids)} done={len(finished)} {now()}", flush=True)
    for case_id in ids:
        if case_id in finished:
            continue
        if past_deadline():
            raise LadderStop("run stopped; remaining ids stay a prefix")
        allow(max(reserve, observed * 1.5 if observed else reserve))
        record = by_id[case_id]
        questions = questions_for(record)
        state = visible_state(record)
        if "gold" in state:
            raise SystemExit("gold leaked into visible_state")
        try:
            result = adapter.decide(state, questions, case_id=case_id)
        except LadderStop:
            raise
        except Exception as exc:  # noqa: BLE001
            transient = _transient(f"{type(exc).__name__}: {exc}") or _transient(str(exc))
            row = {
                "bench": "hard", "case_id": case_id, "model": ARGS.model,
                "requested_model": ARGS.model,
                "error": f"{type(exc).__name__}: {exc}", "cost_usd": 0.0,
                "latency_ms": 0.0, "input_tokens": 0, "output_tokens": 0,
                "invalid": True, "retry": transient,
                "n_questions": len(record["gold"]), "n_correct": 0,
                "n_invalid": len(record["gold"]), "ts": now(),
                "prompt_only": bool(ARGS.prompt_only),
                "trace": traceback.format_exc(limit=2),
            }
            append_row(path, row)
            print(f"ERROR {case_id} {row['error']}", flush=True)
            if not transient:
                finished.add(case_id)
            continue
        graded = grade(record, questions, result)
        cost, estimated = list_cost(result.model or ARGS.model, int(result.input_tokens or 0), int(result.output_tokens or 0))
        if cost is None:
            cost = 0.0
            estimated = True
        answers = {
            name: {
                "choice": body.get("choice"),
                "confidence": body.get("confidence"),
                "invalid": body.get("invalid"),
            }
            for name, body in graded["answers"].items()
        }
        row = {
            "bench": "hard", "case_id": case_id, "model": result.model, "requested_model": ARGS.model,
            "simulated": bool(result.simulated), "error": result.error,
            "latency_ms": result.latency_ms, "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens, "cost_usd": cost, "cost_estimated": estimated,
            "adapter_cost_usd": result.cost_usd, "prompt_only": bool(ARGS.prompt_only),
            "n_questions": graded["n_questions"], "n_correct": graded["n_correct"],
            "n_invalid": graded["n_invalid"], "case_correct": graded["case_correct"],
            "answers": answers, "request_id": result.request_id, "ts": now(),
            "retry": bool(_transient(result.error) and cost == 0 and not (result.input_tokens or 0)),
        }
        append_row(path, row)
        observed = max(observed, cost)
        if row["retry"]:
            print(f"RETRY {case_id} {result.error}", flush=True)
            continue
        finished.add(case_id)
        acc = graded["n_correct"] / graded["n_questions"] if graded["n_questions"] else 0
        print(
            f"hard {case_id} acc={acc:.3f} invalid={graded['n_invalid']} "
            f"cost={cost:.5f} latency_ms={result.latency_ms:.0f} spent={spent_usd():.4f}",
            flush=True,
        )
        if cost > 0.40:
            raise LadderStop(f"{case_id} list-price ${cost:.4f} exceeds the $0.40 case guard")
    _require_complete(ids, finished)


def _require_complete(ids: list, finished: set[str]) -> None:
    left = [case_id for case_id in ids if case_id not in finished]
    if left:
        print(f"INCOMPLETE {len(left)} first={left[0]}", flush=True)
        raise SystemExit(75)


def run_pa(limit: int) -> None:
    import httpx

    from billproof.adapters.frontier import FrontierAdapter
    from billproof.bench.pa import columns as pc
    from billproof.bench.pa.cases import load_cases
    from billproof.bench.pa.score import score_prediction

    ids = json.loads((RUNS_DIR / "ladder_pa_ids.json").read_text())["run_order"]
    if limit:
        ids = ids[:limit]
    wanted = set(ids)
    cases = [case for case in load_cases("development") if case.case_id in wanted]
    by_id = {case.case_id: case for case in cases}
    missing = [case_id for case_id in ids if case_id not in by_id]
    if missing:
        raise SystemExit(f"pa ids missing: {len(missing)} e.g. {missing[:2]}")

    def _frontier():
        return FrontierAdapter(client=httpx.Client(timeout=180.0))

    pc._frontier = _frontier
    path = out_path()
    finished = done_ids(path)
    observed = 0.0
    print(f"start pa model={settings().frontier_model} n={len(ids)} done={len(finished)} {now()}", flush=True)
    for case_id in ids:
        if case_id in finished:
            continue
        if past_deadline():
            raise LadderStop("run stopped; remaining ids stay a prefix")
        allow(max(0.02, observed * 1.5 if observed else 0.02))
        case = by_id[case_id]
        result = pc.gpt_structured(case)
        pred = result.get("prediction")
        score = score_prediction(case, pred, check_evidence=True) if pred else {
            "whole_case": False, "decision": False, "n_facts": case.n_facts, "n_facts_right": 0,
        }
        cost, estimated = list_cost(
            result.get("model") or ARGS.model,
            int(result.get("input_tokens") or 0),
            int(result.get("output_tokens") or 0),
        )
        if cost is None:
            cost, estimated = 0.0, True
        invalid = bool(result.get("error")) or pred is None
        row = {
            "bench": "pa", "case_id": case_id, "model": result.get("model"),
            "requested_model": ARGS.model, "protocol": "gpt_structured",
            "error": result.get("error"), "invalid": invalid,
            "whole_case": bool(score.get("whole_case")) and not invalid,
            "decision": bool(score.get("decision")) and not invalid,
            "n_facts": score.get("n_facts"), "n_facts_right": score.get("n_facts_right"),
            "latency_ms": result.get("latency_ms"), "input_tokens": result.get("input_tokens"),
            "output_tokens": result.get("output_tokens"), "cost_usd": cost,
            "cost_estimated": estimated, "adapter_cost_usd": result.get("cost_usd"),
            "prompt_only": bool(ARGS.prompt_only), "prediction": pred,
            "request_id": result.get("request_id"), "simulated": False, "ts": now(),
            "retry": bool(_transient(result.get("error")) and cost == 0 and not (result.get("input_tokens") or 0)),
        }
        append_row(path, row)
        observed = max(observed, cost)
        if not row["retry"]:
            finished.add(case_id)
        print(
            f"pa {case_id} whole={row['whole_case']} decision={row['decision']} "
            f"invalid={invalid} cost={cost:.5f} latency_ms={row['latency_ms']} spent={spent_usd():.4f}",
            flush=True,
        )
    _require_complete(ids, finished)


def run_persius(limit: int) -> None:
    import httpx

    from c_persius_fair import QUESTION, FairFrontier, _texts, _verify_body
    from c_persius import _state

    document = json.loads((RUNS_DIR / "ladder_persius_ids.json").read_text())
    items = document["run_order"]
    if limit:
        items = items[:limit]
    texts = _texts(ROOT / "data" / "raw" / "persius_imr_appeals" / "data" / "test-00000-of-00001.parquet",
                   {item["row_index"] for item in items})
    adapter = FairFrontier(client=httpx.Client(timeout=120.0))
    path = out_path()
    finished = done_ids(path)
    observed = 0.0
    print(f"start persius model={settings().frontier_model} n={len(items)} done={len(finished)} {now()}", flush=True)
    for item in items:
        case_id = item["case_id"]
        if case_id in finished:
            continue
        if past_deadline():
            raise LadderStop("run stopped; remaining ids stay a prefix")
        allow(max(0.01, observed * 1.5 if observed else 0.01))
        body = texts[item["row_index"]]
        _verify_body(item, body)
        state = _state(body)
        result = adapter.decide(state, [QUESTION], case_id=case_id)
        answer = result.answers.get(QUESTION.name) if result.answers else None
        raw_error = (answer.raw or {}).get("error") if answer is not None else None
        choice = answer.choice if answer is not None else None
        invalid = bool(result.error or raw_error) or choice not in ("yes", "no")
        correct = (not invalid) and choice == item["gold"]
        cost, estimated = list_cost(
            result.model or ARGS.model, int(result.input_tokens or 0), int(result.output_tokens or 0),
        )
        if cost is None:
            cost, estimated = 0.0, True
        row = {
            "bench": "persius", "case_id": case_id, "row_index": item["row_index"],
            "model": result.model, "requested_model": ARGS.model, "gold": item["gold"],
            "choice": None if invalid else choice,
            "confidence": None if answer is None else answer.confidence,
            "correct": bool(correct), "invalid": invalid,
            "error": result.error or raw_error, "latency_ms": result.latency_ms,
            "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
            "cost_usd": cost, "cost_estimated": estimated, "adapter_cost_usd": result.cost_usd,
            "prompt_only": bool(ARGS.prompt_only), "simulated": bool(result.simulated),
            "text_sha256": item["text_sha256"], "request_id": result.request_id, "ts": now(),
            "retry": bool(_transient(row["error"]) and cost == 0 and not (result.input_tokens or 0)),
        }
        append_row(path, row)
        observed = max(observed, cost)
        if not row["retry"]:
            finished.add(case_id)
        print(
            f"persius {case_id} choice={row['choice']} correct={correct} invalid={invalid} "
            f"cost={cost:.5f} latency_ms={result.latency_ms:.0f} spent={spent_usd():.4f}",
            flush=True,
        )
    _require_complete([item["case_id"] for item in items], finished)


def main() -> None:
    cfg = settings()
    if cfg.frontier_model != ARGS.model:
        raise SystemExit(f"FRONTIER_MODEL resolved to {cfg.frontier_model!r}, want {ARGS.model!r}")
    if not cfg.use_real("frontier"):
        raise SystemExit("frontier adapter would be simulated")
    try:
        if ARGS.test == "hard":
            run_hard(ARGS.limit)
        elif ARGS.test == "pa":
            run_pa(ARGS.limit)
        else:
            run_persius(ARGS.limit)
    except LadderStop as exc:
        print(f"STOP {exc}", flush=True)
        return
    print(f"DONE {ARGS.test} {ARGS.model} {now()}", flush=True)


if __name__ == "__main__":
    main()
