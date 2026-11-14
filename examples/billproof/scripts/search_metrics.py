"""Write data/metrics/search_*.json from the recorded chain and asks (no network).

    .venv/bin/python scripts/search_metrics.py [CASE_ID ...]     # default: every recorded chain
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from billproof.config import DATA_DIR  # noqa: E402
from billproof.search.ask import DEMO_QUESTIONS, ask, grade  # noqa: E402
from billproof.search.chain import RECORDED_DIR, recorded_chain  # noqa: E402

OUT = DATA_DIR / "metrics"


def _write(name: str, value, n: int, source: str, **extra) -> None:
    rec = {"value": value, "n": n, "source_run": source, "simulated": False,
           "computed_at": datetime.now(timezone.utc).isoformat(), **extra}
    (OUT / f"{name}.json").write_text(json.dumps(rec, indent=2) + "\n")
    print(f"{name}: {value} (n={n})")


def main(case_ids: list[str]) -> None:
    case_ids = case_ids or sorted(p.stem for p in RECORDED_DIR.glob("SYN-*.json"))
    chains = [c for c in (recorded_chain(cid) for cid in case_ids) if c]
    asks = []
    for cid in case_ids:
        for q in DEMO_QUESTIONS.get(cid, []):
            r = ask(cid, q["question"], schema=q["schema"])
            if r.get("status") != "not_recorded":
                asks.append({"case_id": cid, "id": q["id"], "ok": grade(q["expect"], r.get("structured_answer")),
                             "seconds": r["seconds"], "cost_usd": r.get("cost_usd") or 0.0,
                             "evidences": len(r["evidences"]), "verbatim": sum(e["verbatim"] for e in r["evidences"])})
    src = "search_recorded_" + "+".join(case_ids)
    OUT.mkdir(parents=True, exist_ok=True)

    if chains:
        quotes = [q for c in chains for q in c["search"]["clauses"] + c["search"]["record_facts"]]
        _write("search_agent_seconds", round(sum(c["search"]["seconds"] for c in chains) / len(chains), 1),
               len(chains), src, note="agent-mode search with output_schema, one per case")
        _write("search_chain_seconds", round(sum(c["seconds_total"] for c in chains) / len(chains), 1), len(chains), src,
               note="ingest check + agent search + 2 Drex calls + letter, wall clock")
        _write("search_quotes_per_case", round(len(quotes) / len(chains), 1), len(chains), src,
               note="plan clauses + record facts returned with page and quote")
        _write("search_quotes_verbatim_rate", round(sum(q["verbatim"] for q in quotes) / len(quotes), 3), len(quotes), src,
               note="quote found word for word in the cited PDF by code (pypdf)")
        _write("search_quotes_page_correct_rate", round(sum(q["page_ok"] for q in quotes) / len(quotes), 3),
               len(quotes), src)
        shift = []
        for c in chains:
            w = c["drex"]["without_documents"]["answers"]["plan_supports_coverage"]["probabilities"]["yes"]
            y = c["drex"]["with_documents"]["answers"]["plan_supports_coverage"]["probabilities"]["yes"]
            shift.append({"case_id": c["case_id"], "search_supports": c["search"]["supports_coverage"],
                          "p_yes_without": w, "p_yes_with": y})
        pos = [x["p_yes_with"] for x in shift if x["search_supports"]]
        _write("search_drex_p_supports_with_docs", min(pos) if pos else None, len(pos), src,
               detail=shift, model=chains[0]["drex"]["model"],
               note="Drex P(plan supports coverage) with the Search quotes as state, cases where Search found support; "
                    "detail has the without-documents baseline and the negative control")
    if asks:
        _write("search_ask_correct_rate", round(sum(a["ok"] for a in asks) / len(asks), 3), len(asks), src,
               detail=[{k: a[k] for k in ("case_id", "id", "ok")} for a in asks],
               note="demo questions with known answers from the synthetic documents, fact mode")
        _write("search_ask_seconds", round(sum(a["seconds"] for a in asks) / len(asks), 1), len(asks), src)
        _write("search_ask_evidence_per_answer", round(sum(a["evidences"] for a in asks) / len(asks), 1), len(asks), src,
               verbatim_rate=round(sum(a["verbatim"] for a in asks) / max(1, sum(a["evidences"] for a in asks)), 3))
    spend = sum((c["search"].get("cost_usd") or 0) + c["drex"]["without_documents"]["cost_usd"]
                + c["drex"]["with_documents"]["cost_usd"] for c in chains) + sum(a["cost_usd"] for a in asks)
    _write("search_spend_usd", round(spend, 4), len(chains) + len(asks), src,
           note="recorded runs only, token cost at $0.10/M in, $0.50/M out (Search) + Drex; ingest credits billed "
                "separately; re-runs and probes this session add ~$0.02",
           ingest_credits=sum(c["workspace"].get("ingest_credits") or 0 for c in chains))


if __name__ == "__main__":
    main(sys.argv[1:])
