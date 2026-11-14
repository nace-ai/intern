"""Paired decision benchmark. The default is a five-case development pilot."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import subprocess
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from clients import (
    DrexClient,
    MockDecisionClient,
    OpenRouterClient,
    confirm_published_drex_rate,
    find_openrouter_model,
    list_nace_models,
    utc_now,
)
from config import (
    DREX_BUDGET_USD,
    DREX_PRICING_PROVENANCE,
    NACE_DREX_MODEL,
    OPENROUTER_BUDGET_USD,
    OPENROUTER_MODEL,
    ROOT,
    nace_api_key,
    openrouter_api_key,
)
from evaluation import oracle_baseline, paired_speedup, project_cost, regex_baseline, summarize
from prompts import input_hash, prompt_identity, shared_input_for_case
from schemas import DecisionInput, DecisionResult, Facts
from storage import ResultStore

ORDER_SEED = 20261114
DREX_RESERVE = Decimal("0.05")
OPENROUTER_RESERVE = Decimal("0.40")


def code_revision() -> str:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        return commit + ("-dirty" if dirty else "")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "no-git-revision"


def load_cases(split: str) -> list[dict[str, Any]]:
    names = {"dev": "dev_20.json", "test": "test_100.json", "hard": "hard_cases.json"}
    if split not in names:
        raise ValueError(split)
    return json.loads((ROOT / "data" / names[split]).read_text(encoding="utf-8"))


def ordered_cases(cases: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    rng = random.Random(ORDER_SEED)
    rows = list(cases)
    rng.shuffle(rows)
    return rows[:limit]


def selected_cases(cases: list[dict[str, Any]], limit: int, split: str) -> list[dict[str, Any]]:
    """Keep the frozen development shuffle. Leave hard-case pairs in file order."""
    if split == "hard":
        return list(cases[:limit])
    return ordered_cases(cases, limit)


class Ledger:
    def __init__(self, limit: Decimal) -> None:
        self.limit = limit
        self.spent = Decimal("0")
        self.unknown = 0

    def allow(self, reserve: Decimal) -> bool:
        return self.spent + reserve <= self.limit

    def commit(self, actual: Decimal | None, reserve: Decimal) -> None:
        if actual is None:
            self.unknown += 1
            self.spent += reserve
        else:
            self.spent += actual


def baseline_result(case: dict[str, Any], provider: str, verdict: str, shared: str) -> dict[str, Any]:
    return DecisionResult.model_validate(
        {
            "case_id": case["case_id"],
            "provider": provider,
            "requested_model": provider,
            "actual_model": provider,
            "verdict": verdict,
            "status": "ok",
            "input_hash": input_hash(shared),
            "timestamp": utc_now(),
            "mode": "reference",
            "cost_usd": None,
            "cost_source": "not_measured",
        }
    ).dump_public()


async def prepare_live(http: httpx.AsyncClient) -> dict[str, Any]:
    nace_key = nace_api_key()
    router_key = openrouter_api_key()
    if not nace_key or not router_key:
        missing = []
        if not nace_key:
            missing.append("NACE_API_KEY")
        if not router_key:
            missing.append("OPENROUTER_API_KEY")
        raise SystemExit("missing configuration: " + ", ".join(missing))
    models = await list_nace_models(http, nace_key)
    names = [row.get("name") for row in models if isinstance(row, dict)]
    if NACE_DREX_MODEL not in names:
        raise SystemExit(f"{NACE_DREX_MODEL} is not in GET /v1/models for this key. No substitute was called.")
    priced = await confirm_published_drex_rate(http)
    router_model = await find_openrouter_model(http, router_key, OPENROUTER_MODEL)
    if router_model is None:
        raise SystemExit(f"{OPENROUTER_MODEL} is not in the OpenRouter model list. No substitute was called.")
    supported = router_model.get("supported_parameters") if isinstance(router_model.get("supported_parameters"), list) else []
    output_field = "max_completion_tokens" if "max_completion_tokens" in supported and "max_tokens" not in supported else "max_tokens"
    return {
        "drex_price_confirmed": priced,
        "pricing_provenance": DREX_PRICING_PROVENANCE,
        "openrouter_model": {
            key: router_model.get(key)
            for key in ("id", "pricing", "supported_parameters", "context_length", "architecture")
            if key in router_model
        },
        "openrouter_output_field": output_field,
        "openrouter_output_limit": 2048,
    }


async def run_benchmark(mock: bool, limit: int, split: str) -> None:
    cases = selected_cases(load_cases(split), limit, split)
    run_id = utc_now().replace(":", "").replace("+00:00", "Z") + "-" + uuid.uuid4().hex[:8]
    mode = "mock" if mock else "live"
    track = "hard_cases" if split == "hard" else "decisions"
    store = ResultStore()
    run_dir = store.root / mode / track / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    settings: dict[str, Any] = {"mock": mock}
    async with httpx.AsyncClient() as http:
        if mock:
            drex = MockDecisionClient("drex")
            router = MockDecisionClient("openrouter")
        else:
            settings = await prepare_live(http)
            drex = DrexClient(http)
            router = OpenRouterClient(http)
            router.output_field = settings["openrouter_output_field"]
            router.max_tokens = settings["openrouter_output_limit"]
            if not settings["drex_price_confirmed"]:
                drex.usd_per_input_token = None
                print("Drex price page did not confirm the captured rate. Drex costs will stay unknown.")
        drex_ledger = Ledger(DREX_BUDGET_USD)
        router_ledger = Ledger(OPENROUTER_BUDGET_USD)
        metadata = {
            "run_id": run_id,
            "mode": mode,
            "measurements": "simulated" if mock else "measured",
            "track": track,
            "split": split,
            "planned": limit,
            "order_seed": ORDER_SEED,
            "case_ids": [case["case_id"] for case in cases],
            "code_revision": code_revision(),
            "prompt": prompt_identity(),
            "requested_models": {"drex": NACE_DREX_MODEL, "openrouter": OPENROUTER_MODEL},
            "settings": settings,
            "concurrency": {"per_provider": 1, "providers_in_parallel": 2},
            "timeouts_s": {"drex": 60, "openrouter": 90},
            "application_response_cache": False,
            "automatic_retries": 0,
            "started_at": utc_now(),
        }
        if split == "hard":
            metadata["dataset"] = "hard_cases"
            metadata["published_benchmark"] = False
            metadata["live_opt_in"] = not mock
        (run_dir / "run.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        drex_rows: list[DecisionResult] = []
        router_rows: list[DecisionResult] = []
        for case in cases:
            shared = shared_input_for_case(case)
            decision = DecisionInput(
                case_id=case["case_id"],
                shared_input=shared,
                input_hash=input_hash(shared),
                evidence_mode="raw_text",
            )
            if not mock:
                if not drex_ledger.allow(DREX_RESERVE) or not router_ledger.allow(OPENROUTER_RESERVE):
                    print("local budget reservation stopped the run before", case["case_id"])
                    break
            pair = await asyncio.gather(drex.decide(decision), router.decide(decision))
            for result, ledger, reserve in (
                (pair[0], drex_ledger, DREX_RESERVE),
                (pair[1], router_ledger, OPENROUTER_RESERVE),
            ):
                public = result.dump_public()
                public["run_id"] = run_id
                store.append(mode, track, public)
                with (run_dir / "records.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(public) + "\n")
                if not mock:
                    ledger.commit(result.cost_usd, reserve)
            drex_rows.append(pair[0])
            router_rows.append(pair[1])
            text = "\n".join(document["text"] for document in case["documents"])
            facts = Facts.model_validate(case["facts"])
            for baseline in (
                baseline_result(case, "oracle_baseline", oracle_baseline(facts), shared),
                baseline_result(case, "regex_baseline", regex_baseline(text), shared),
            ):
                baseline["run_id"] = run_id
                with (run_dir / "baselines.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(baseline) + "\n")
        gold = {case["case_id"]: case["expected_verdict"] for case in cases}
        summary = {
            "run_id": run_id,
            "measurements": metadata["measurements"],
            "planned": limit,
            "drex": summarize(drex_rows, gold, limit),
            "openrouter": summarize(router_rows, gold, limit),
            "speedup": paired_speedup(drex_rows, router_rows),
            "finished_at": utc_now(),
        }
        if split == "hard":
            summary["dataset"] = "hard_cases"
            summary["published_benchmark"] = False
            summary["split"] = "hard"
        if not mock:
            summary["charges"] = {
                "drex_known_usd": summary["drex"]["cost_usd"]["known_total"],
                "drex_unknown": summary["drex"]["cost_usd"]["unknown_count"],
                "openrouter_known_usd": summary["openrouter"]["cost_usd"]["known_total"],
                "openrouter_unknown": summary["openrouter"]["cost_usd"]["unknown_count"],
            }
            if split != "hard":
                summary["charges"]["drex_projection_100"] = project_cost(
                    Decimal(summary["drex"]["cost_usd"]["known_total"]) if summary["drex"]["cost_usd"]["known_total"] else None,
                    summary["drex"]["attempted"],
                    100,
                    summary["drex"]["cost_usd"]["unknown_count"],
                )
                summary["charges"]["openrouter_projection_100"] = project_cost(
                    Decimal(summary["openrouter"]["cost_usd"]["known_total"]) if summary["openrouter"]["cost_usd"]["known_total"] else None,
                    summary["openrouter"]["attempted"],
                    100,
                    summary["openrouter"]["cost_usd"]["unknown_count"],
                )
            freeze = {
                "frozen_at": utc_now(),
                "source_run": run_id,
                "drex_model": NACE_DREX_MODEL,
                "drex_price_confirmed": settings.get("drex_price_confirmed"),
                "pricing_provenance": DREX_PRICING_PROVENANCE,
                "openrouter_model": OPENROUTER_MODEL,
                "openrouter_output_field": settings.get("openrouter_output_field"),
                "openrouter_output_limit": settings.get("openrouter_output_limit"),
                "openrouter_model_record": settings.get("openrouter_model"),
                "provider_preferences": {"require_parameters": True},
                "note": "Frozen from the development pilot. Do not retune on test outcomes.",
            }
            freeze_path = store.root / "live" / "frozen_settings.json"
            if split == "dev":
                freeze_path.parent.mkdir(parents=True, exist_ok=True)
                freeze_path.write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8")
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        latest = store.root / mode / track / "latest.json"
        paired = [
            {
                "case_id": left.case_id,
                "expected_verdict": gold[left.case_id],
                "drex": left.dump_public(),
                "openrouter": right.dump_public(),
            }
            for left, right in zip(drex_rows, router_rows)
        ]
        latest.write_text(
            json.dumps(
                {"run_id": run_id, "summary": summary, "measurements": metadata["measurements"], "cases": paired},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print_summary(summary, mock)


def print_summary(summary: dict[str, Any], mock: bool) -> None:
    if summary.get("dataset") == "hard_cases":
        print("Hard-case diagnostic set. These figures are not the measured 100-case benchmark.")
    label = "SIMULATED mock run. These figures are not measured model performance." if mock else "Measured live run."
    print(label)
    print("run", summary["run_id"], "planned", summary["planned"])
    for name in ("drex", "openrouter"):
        block = summary[name]
        accuracy = block["accuracy"]
        clearance = block["false_clearance"]
        print(
            name,
            f"accuracy {accuracy['numerator']}/{accuracy['denominator']}",
            f"false_clearance {clearance['numerator']}/{clearance['denominator']}",
            f"failures {block['failures']}",
            f"known_cost {block['cost_usd']['known_total']}",
            f"unknown_cost {block['cost_usd']['unknown_count']}",
        )
    if not mock:
        if summary.get("dataset") == "hard_cases":
            print("hard-case charges within the configured budgets:")
        elif summary["planned"] >= 100:
            print("measured full-run charges:")
        else:
            print("pilot charges and 100-case projection:")
        print(json.dumps(summary["charges"], indent=2))
        if summary.get("dataset") != "hard_cases" and summary["planned"] < 100:
            print("The full paid benchmark was not started by this command.")


def resolve_plan(args: argparse.Namespace) -> tuple[bool, int, str]:
    if args.pilot and args.full:
        raise SystemExit("choose either --pilot or --full")
    if args.hard and (args.pilot or args.full):
        raise SystemExit("choose either --hard or --pilot/--full")
    if args.hard:
        if not args.mock and not args.confirm_live:
            raise SystemExit(
                "refusing a live hard-case run without --confirm-live. "
                "Use --mock for the offline diagnostic set. A live run spends the configured budgets."
            )
        return args.mock, len(load_cases("hard")), "hard"
    if args.full:
        return args.mock, args.full, "test"
    limit = 5 if args.pilot is None else args.pilot
    return args.mock, limit, "dev"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--pilot", type=int)
    parser.add_argument("--full", type=int)
    parser.add_argument("--hard", action="store_true", help="run the separate hard-case diagnostic set")
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="required opt-in before a live hard-case run; budgets still apply",
    )
    args = parser.parse_args()
    mock, limit, split = resolve_plan(args)
    if split == "test" and not mock:
        freeze_path = ResultStore().root / "live" / "frozen_settings.json"
        if not freeze_path.exists():
            raise SystemExit("refusing the paid test run until a development pilot freezes settings")
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        if freeze.get("drex_model") != NACE_DREX_MODEL or freeze.get("openrouter_model") != OPENROUTER_MODEL:
            raise SystemExit("frozen settings do not match the configured models")
    asyncio.run(run_benchmark(mock, limit, split))


if __name__ == "__main__":
    main()
