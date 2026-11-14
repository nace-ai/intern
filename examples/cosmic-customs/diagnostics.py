"""Read-only diagnostic for a saved decision run. It does not call providers."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from config import ROOT
from evaluation import paired_speedup, summarize
from schemas import DecisionResult

VERDICTS = ("CLEAR", "HOLD", "REVIEW")
CASE_FILES = ("dev_20.json", "test_100.json", "hard_cases.json")


def case_index() -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for name in CASE_FILES:
        path = ROOT / "data" / name
        if not path.is_file():
            continue
        for case in json.loads(path.read_text(encoding="utf-8")):
            index[case["case_id"]] = case
    return index


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_records(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def load_saved_run(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    """Load a run directory or a latest.json file. This function only reads."""
    if path.is_dir():
        meta = _read_json(path / "run.json")
        records = _read_records(path / "records.jsonl") if (path / "records.jsonl").is_file() else []
        saved = _read_json(path / "summary.json") if (path / "summary.json").is_file() else None
        meta["source"] = str(path)
        return meta, records, saved
    if path.is_file():
        payload = _read_json(path)
        records: list[dict[str, Any]] = []
        for row in payload.get("cases") or []:
            for provider in ("drex", "openrouter"):
                item = row.get(provider)
                if isinstance(item, dict):
                    records.append(item)
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else None
        meta = {
            "run_id": payload.get("run_id") or (summary or {}).get("run_id"),
            "measurements": payload.get("measurements") or (summary or {}).get("measurements"),
            "mode": payload.get("mode") or (summary or {}).get("mode"),
            "planned": (summary or {}).get("planned") or payload.get("planned"),
            "split": (summary or {}).get("split") or payload.get("split"),
            "dataset": (summary or {}).get("dataset") or payload.get("dataset"),
            "published_benchmark": (summary or {}).get("published_benchmark", payload.get("published_benchmark")),
            "source": str(path),
        }
        return meta, records, summary
    raise SystemExit(f"not a saved run: {path}")


def _results(records: list[dict[str, Any]], provider: str) -> list[DecisionResult]:
    return [DecisionResult.model_validate(row) for row in records if row.get("provider") == provider]


def _gold_map(rows: list[DecisionResult], index: dict[str, dict[str, Any]]) -> dict[str, str]:
    gold: dict[str, str] = {}
    for row in rows:
        case = index.get(row.case_id)
        if case is None:
            raise KeyError(f"no published or hard case for {row.case_id}")
        gold[row.case_id] = case["expected_verdict"]
    return gold


def _by_verdict(rows: list[DecisionResult], gold: dict[str, str]) -> dict[str, dict[str, int]]:
    counts = {label: {"correct": 0, "attempted": 0} for label in VERDICTS}
    for row in rows:
        label = gold[row.case_id]
        counts[label]["attempted"] += 1
        if row.status == "ok" and row.verdict == label:
            counts[label]["correct"] += 1
    return counts


def _by_signature(rows: list[DecisionResult], gold: dict[str, str], index: dict[str, dict[str, Any]]) -> dict[str, dict[str, int]]:
    grouped: dict[str, dict[str, int]] = {}
    for row in rows:
        signature = index[row.case_id].get("rule_signature") or "unknown"
        bucket = grouped.setdefault(
            signature,
            {"correct": 0, "attempted": 0, "false_clearance": 0, "failures": 0},
        )
        bucket["attempted"] += 1
        label = gold[row.case_id]
        if row.status == "ok" and row.verdict == label:
            bucket["correct"] += 1
        if row.status != "ok":
            bucket["failures"] += 1
        if label in ("HOLD", "REVIEW") and row.status == "ok" and row.verdict == "CLEAR":
            bucket["false_clearance"] += 1
    return dict(sorted(grouped.items()))


def _false_clearances(rows: list[DecisionResult], gold: dict[str, str], index: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    found = []
    for row in rows:
        label = gold[row.case_id]
        if label in ("HOLD", "REVIEW") and row.status == "ok" and row.verdict == "CLEAR":
            found.append(
                {
                    "case_id": row.case_id,
                    "gold": label,
                    "predicted": "CLEAR",
                    "rule_signature": index[row.case_id].get("rule_signature") or "unknown",
                }
            )
    return sorted(found, key=lambda item: item["case_id"])


def _disagreements(left: list[DecisionResult], right: list[DecisionResult], gold: dict[str, str]) -> list[dict[str, Any]]:
    right_by_case = {row.case_id: row for row in right}
    rows = []
    for row in left:
        other = right_by_case.get(row.case_id)
        if other is None:
            kind = "unmatched"
        elif row.status != "ok" or other.status != "ok":
            kind = "invalid_or_unmatched_attempt"
        elif row.verdict != other.verdict:
            kind = "verdict"
        else:
            continue
        rows.append(
            {
                "case_id": row.case_id,
                "gold": gold[row.case_id],
                "kind": kind,
                "drex_status": row.status,
                "drex_verdict": row.verdict,
                "openrouter_status": None if other is None else other.status,
                "openrouter_verdict": None if other is None else other.verdict,
            }
        )
    return sorted(rows, key=lambda item: item["case_id"])


def _cost_view(block: dict[str, Any]) -> dict[str, Any]:
    known = block["cost_usd"]["known_total"]
    if known is None:
        state = "unknown"
    elif Decimal(known) == 0:
        state = "known_zero"
    else:
        state = "known"
    return {
        "state": state,
        "known_total": known,
        "known_count": block["cost_usd"]["known_count"],
        "unknown_count": block["cost_usd"]["unknown_count"],
    }


def _saved_agrees(saved: dict[str, Any] | None, providers: dict[str, Any], speedup: dict[str, Any]) -> bool | None:
    if not saved or "drex" not in saved or "openrouter" not in saved:
        return None
    for name in ("drex", "openrouter"):
        if saved[name].get("accuracy") != providers[name]["accuracy"]:
            return False
        if saved[name].get("false_clearance") != providers[name]["false_clearance"]:
            return False
    saved_speed = saved.get("speedup") or {}
    if saved_speed.get("matched_valid_pairs") != speedup["matched_valid_pairs"]:
        return False
    return True


def _banner(meta: dict[str, Any], case_ids: list[str]) -> list[str]:
    hard = meta.get("dataset") == "hard_cases" or meta.get("split") == "hard" or any(case_id.startswith("HRD-") for case_id in case_ids)
    simulated = meta.get("measurements") != "measured" or meta.get("mode") == "mock"
    lines = []
    if simulated:
        lines.append("SIMULATED. These figures are not measured model performance.")
    else:
        lines.append("MEASURED live run.")
    if hard and simulated:
        lines.append("The hard-case set has not been measured in this run. Do not read it as model performance.")
    elif hard:
        lines.append("MEASURED hard-case run. This is not the published 100-case benchmark.")
    elif not simulated:
        lines.append("Published decision benchmark sample.")
    return lines


def _infer_split(case_ids: list[str], meta: dict[str, Any]) -> str | None:
    if meta.get("split"):
        return meta["split"]
    prefixes = {case_id.split("-", 1)[0] for case_id in case_ids}
    if prefixes == {"HRD"}:
        return "hard"
    if prefixes == {"TST"}:
        return "test"
    if prefixes == {"DEV"}:
        return "dev"
    return meta.get("split")


def diagnose(path: Path, index: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    meta, records, saved = load_saved_run(path)
    if meta.get("mode") is None:
        modes = {row.get("mode") for row in records if isinstance(row, dict) and row.get("mode")}
        if len(modes) == 1:
            meta["mode"] = modes.pop()
    index = case_index() if index is None else index
    providers: dict[str, Any] = {}
    parsed: dict[str, list[DecisionResult]] = {}
    for name in ("drex", "openrouter"):
        rows = _results(records, name)
        parsed[name] = rows
        gold = _gold_map(rows, index)
        block = summarize(rows, gold, int(meta.get("planned") or len(rows)))
        providers[name] = {
            "sample": {
                "planned": block["planned"],
                "attempted": block["attempted"],
                "completed": block["completed"],
                "failures": block["failures"],
            },
            "accuracy": block["accuracy"],
            "false_clearance": block["false_clearance"],
            "by_verdict": _by_verdict(rows, gold),
            "by_signature": _by_signature(rows, gold, index),
            "false_clearance_cases": _false_clearances(rows, gold, index),
            "latency_ms": block["latency_ms"],
            "cost": _cost_view(block),
        }
    gold_for_pairs = _gold_map(parsed["drex"] + parsed["openrouter"], index)
    speedup = paired_speedup(parsed["drex"], parsed["openrouter"])
    present = {row.case_id for row in parsed["drex"] + parsed["openrouter"]}
    valid_ids = set()
    for left in parsed["drex"]:
        match = next((row for row in parsed["openrouter"] if row.case_id == left.case_id), None)
        if (
            match is not None
            and left.status == "ok"
            and match.status == "ok"
            and left.latency_ms is not None
            and match.latency_ms is not None
            and left.latency_ms > 0
            and match.latency_ms > 0
        ):
            valid_ids.add(left.case_id)
    case_ids = sorted(present)
    meta["split"] = _infer_split(case_ids, meta)
    if meta.get("dataset") is None and meta.get("split") == "hard":
        meta["dataset"] = "hard_cases"
    return {
        "run_id": meta.get("run_id"),
        "source": meta.get("source"),
        "mode": meta.get("mode"),
        "measurements": meta.get("measurements"),
        "split": meta.get("split"),
        "dataset": meta.get("dataset"),
        "planned": meta.get("planned"),
        "banner": _banner(meta, case_ids),
        "simulated": meta.get("measurements") != "measured" or meta.get("mode") == "mock",
        "providers": providers,
        "disagreements": _disagreements(parsed["drex"], parsed["openrouter"], gold_for_pairs),
        "speedup": speedup,
        "latency_pairs_excluded": len(present - valid_ids),
        "saved_summary_agrees": _saved_agrees(saved, providers, speedup),
    }


def _ratio(block: dict[str, int]) -> str:
    return f"{block['correct']}/{block['attempted']}"


def format_report(report: dict[str, Any]) -> str:
    lines = ["Cosmic Customs decision diagnostic", *report["banner"], ""]
    lines.append(f"run: {report['run_id']}")
    lines.append(f"source: {report['source']}")
    lines.append(f"mode: {report['mode']}  measurements: {report['measurements']}  split: {report['split']}")
    lines.append(f"dataset: {report['dataset'] or 'published decisions'}  planned: {report['planned']}")
    if report["saved_summary_agrees"] is not None:
        lines.append(f"recomputed summary agrees with saved summary: {str(report['saved_summary_agrees']).lower()}")
    lines.append("")
    lines.append("samples")
    for name in ("drex", "openrouter"):
        sample = report["providers"][name]["sample"]
        lines.append(
            f"  {name}: planned {sample['planned']}, attempted {sample['attempted']}, "
            f"completed {sample['completed']}, failures {sample['failures']}"
        )
    lines.append("")
    lines.append("per-verdict accuracy (correct/attempted, failures stay in the denominator)")
    for name in ("drex", "openrouter"):
        for label in VERDICTS:
            block = report["providers"][name]["by_verdict"][label]
            lines.append(f"  {name} {label} {_ratio(block)}")
    lines.append("")
    lines.append("per-rule-signature accuracy")
    for name in ("drex", "openrouter"):
        for signature, block in report["providers"][name]["by_signature"].items():
            lines.append(
                f"  {name} {signature} {block['correct']}/{block['attempted']} "
                f"false_clearance {block['false_clearance']} failures {block['failures']}"
            )
    lines.append("")
    lines.append("false clearances (predicted CLEAR where gold is HOLD or REVIEW)")
    for name in ("drex", "openrouter"):
        block = report["providers"][name]["false_clearance"]
        lines.append(f"  {name} {block['numerator']}/{block['denominator']}")
        for item in report["providers"][name]["false_clearance_cases"]:
            lines.append(f"    {item['case_id']} gold {item['gold']} signature {item['rule_signature']}")
    lines.append("")
    verdict_gaps = [item for item in report["disagreements"] if item["kind"] == "verdict"]
    other_gaps = [item for item in report["disagreements"] if item["kind"] != "verdict"]
    lines.append(f"Drex versus OpenRouter verdict disagreements: {len(verdict_gaps)}")
    for item in verdict_gaps:
        lines.append(
            f"  {item['case_id']} gold {item['gold']} drex {item['drex_verdict']} openrouter {item['openrouter_verdict']}"
        )
    if other_gaps:
        lines.append(f"invalid or unmatched attempts: {len(other_gaps)}")
        for item in other_gaps:
            lines.append(
                f"  {item['case_id']} gold {item['gold']} drex {item['drex_status']}/{item['drex_verdict']} "
                f"openrouter {item['openrouter_status']}/{item['openrouter_verdict']}"
            )
    lines.append("")
    speed = report["speedup"]
    lines.append(
        "matched latency: "
        f"{speed['matched_valid_pairs']} valid pairs, "
        f"median openrouter/drex {speed['median_right_over_left']}, "
        f"excluded invalid or unmatched attempts {report['latency_pairs_excluded']}"
    )
    lines.append("A ratio above 1 means Drex returned first. Null latency is not a measurement.")
    lines.append("")
    lines.append("cost (known zero is not unknown)")
    for name in ("drex", "openrouter"):
        cost = report["providers"][name]["cost"]
        lines.append(
            f"  {name}: {cost['state']} total {cost['known_total']} "
            f"({cost['known_count']} known, {cost['unknown_count']} unknown)"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Read a saved decision run and print a diagnostic. Nothing is rewritten.")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(format_report(diagnose(args.path)), end="")


if __name__ == "__main__":
    main()
