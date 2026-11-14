"""Run one CaseContext JSON file through the live multi-agent engine and print the trace.

Usage:  python -m service.run_case tests/fixtures/case_pattern_mock.json [--save out.json]
Uses real Drex calls (NACE_API_KEY required) and the local NDI evidence cache.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents.drex_client import NaceDrexDecider
from agents.orchestrator import run_investigation
from evidence.store import EvidenceStore
from service.config import get_settings
from service.contracts import CaseContext


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_file", type=Path)
    parser.add_argument("--save", type=Path, help="write the InvestigationResult JSON here")
    args = parser.parse_args(argv)

    settings = get_settings()
    case = CaseContext.model_validate_json(args.case_file.read_text(encoding="utf-8"))
    store = EvidenceStore(settings.evidence_cache_dir)
    print(f"Evidence: {store.status()['detail']}")
    result = run_investigation(
        case,
        lambda: NaceDrexDecider(settings.decision_model, settings.drex_timeout_seconds),
        store,
        settings.decision_model,
    )

    for m in result.agent_trace:
        scores = ", ".join(f"{k}={v:.2f}" for k, v in m.probabilities.items())
        print(f"\n[round {m.round_number}] {m.agent.upper()}  -> {m.selected_option}  ({m.uncertainty_status}, {m.duration_seconds:.2f}s)")
        print(f"  scores: {scores}")
        print(f"  {m.action_summary}")
        for ref in m.evidence_refs:
            print(f"  cite [{ref.status}] {ref.title.split(':')[0]} {ref.page_or_region}: \"{ref.passage_text[:140]}\"")
    print(f"\nSTATUS {result.status} | final action: {result.final_recommended_next_action} | priority: {result.review_priority}")
    for note in result.policy_notes:
        print(f"  policy: {note}")
    for error in result.errors:
        print(f"  error [{error.code}] ({error.agent}): {error.message}")
    print(f"  model: {result.provider_model_details.get('decision_model')}  total {result.total_duration_seconds}s")

    if args.save:
        args.save.write_text(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"saved {args.save}")
    return 0 if result.status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
