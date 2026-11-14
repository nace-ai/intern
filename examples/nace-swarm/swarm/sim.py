"""Simulation CLI: run election rounds and write web/data/run.json.

Run: python -m swarm.sim --agents 100 --rounds 5 --seed 1 [--fake]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .election import World, run_round
from .individualizer import make_agents
from .models import CountryState

DATA_PATH = Path("web/data/run.json")


def _initial_country() -> CountryState:
    return CountryState(
        stock_market=0.5,
        class_mobility=0.5,
        foreign_relations=0.5,
        unemployment=0.4,
        healthcare_access=0.6,
    )


def _print_tally(record: dict) -> None:
    print(f"Round {record['round']}:")
    for party in ("Left", "Right"):
        parts = ", ".join(f"{cand}={n}" for cand, n in record["primary_tallies"][party].items())
        print(f"  {party} primary: {parts}")
    parts = ", ".join(f"{cand}={n}" for cand, n in record["general_tally"].items())
    print(f"  general: {parts}")
    winner = record["winner"]
    print(f"  winner: {winner['name']} ({winner['party']})")


def _print_usage(usage: dict[str, int]) -> None:
    if not usage:
        print("total Drex usage: (fake mode, no Drex requests)")
        return
    parts = ", ".join(f"{key}={value}" for key, value in sorted(usage.items()))
    print(f"total Drex usage: {parts}")


async def _run(args: argparse.Namespace) -> None:
    world = World(agents=make_agents(args.agents, args.seed), country=_initial_country())
    usage: dict[str, int] = {}
    records = []
    for k in range(1, args.rounds + 1):
        record = await run_round(world, k, fake=args.fake, usage=usage)
        records.append(record)
        _print_tally(record)

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps({"rounds": records}, indent=2))
    print(f"wrote {DATA_PATH} ({len(records)} rounds)")
    _print_usage(usage)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents", type=int, required=True)
    parser.add_argument("--rounds", type=int, required=True)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--fake", action="store_true", help="no network: fake voters and candidates")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
