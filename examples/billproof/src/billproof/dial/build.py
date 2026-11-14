"""Write data/metrics/dial_<bench>.json from recorded answers. No model calls.

    PYTHONPATH=src python -m billproof.dial.build [bench ...]
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from billproof.dial.simulate import BENCHES, METRICS, curve_document


def path_for(bench: str):
    return METRICS / f"dial_{bench}.json"


def build(bench: str) -> dict:
    document = curve_document(bench)
    document["computed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    document["generator"] = "billproof.dial.build"
    path_for(bench).write_text(json.dumps(document, indent=1) + "\n")
    return document


def main(argv: list[str]) -> None:
    for bench in argv or list(BENCHES):
        document = build(bench)
        chart = document["chart"]
        gpt, drex = document["gpt_only"], document["drex_only"]
        match = document["match_gpt_within_1pt"]
        print(
            f"{bench}: drex {drex['value']:.4f} ${drex['cost_per_1000_usd']:.2f}/1k | "
            f"gpt {gpt['value']:.4f} ${gpt['cost_per_1000_usd']:.2f}/1k | "
            f"match {chart}: {match}"
        )
        if bench == "hard":
            print("  family match:", document.get("match_gpt_within_1pt_family"))
            print("  measured vs simulated:", json.dumps(document.get("measured_vs_simulated"), indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
