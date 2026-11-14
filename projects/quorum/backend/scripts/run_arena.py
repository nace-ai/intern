"""Run arena rounds: python backend/scripts/run_arena.py --rounds 10 --attacks 20 --clean 5"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app import arena, config, nace  # noqa: E402

KEEP = {"trusted.json"}


def archive_arena(arena_dir: Path, archive_root: Path, stamp: str | None = None) -> Path | None:
    """Move everything but trusted.json out of arena_dir if it holds results. Never deletes."""
    if not ((arena_dir / "attacks.jsonl").exists() or (arena_dir / "rounds.jsonl").exists()):
        return None
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = archive_root / stamp
    dest.mkdir(parents=True, exist_ok=False)
    for child in sorted(arena_dir.iterdir()):
        if child.name not in KEEP:
            shutil.move(str(child), str(dest / child.name))
    return dest


def _risk(v: Any) -> str:
    return "-" if v is None else f"{v:.3f}"


def format_round(rec: dict[str, Any]) -> str:
    parts = [f"round {rec['round']} mode={rec.get('mode') or 'swap'}:"]
    for name, s in rec["defenders"].items():
        parts.append(f"{name} raw={s['raw_breaches']} breach={s['breaches']} "
                     f"fa={s['false_alarms']} fail={s['failures']} "
                     f"min_risk={_risk(s.get('min_risk'))} |")
    a = rec["attacker"]
    parts.append(f"attacker in={a.get('input_tokens')} out={a.get('output_tokens')} "
                 f"turns={a.get('turns')}")
    if a.get("error"):
        parts.append(f"error={a['error']}")
    if a.get("warning"):
        parts.append(f"warning={a['warning']}")
    if rec.get("unavailable"):
        parts.append(f"unavailable={','.join(rec['unavailable'])}")
    return " ".join(parts)


def should_enable_risk(rec: dict[str, Any], with_risk: bool) -> bool:
    drex = rec["defenders"].get("drex") or {}
    return (not with_risk and rec["round"] == 1 and rec["n_attacks"] > 0
            and drex.get("raw_breaches", 0) == 0)


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--attacks", type=int, default=20)
    ap.add_argument("--clean", type=int, default=5)
    ap.add_argument("--risk-feedback", action="store_true")
    ap.add_argument("--mode", choices=list(arena.MODES), default="redirect")
    ap.add_argument("--fresh", action="store_true",
                    help="archive previous arena results before starting")
    args = ap.parse_args(argv)
    if args.fresh:
        moved = archive_arena(arena.ARENA, config.DATA / "arena-archive")
        if moved is not None:
            print(f"archived previous arena results to {moved}", flush=True)
    with_risk = args.risk_feedback
    start = arena.next_round()
    for r in range(start, start + args.rounds):
        rec = await arena.run_round(r, args.attacks, args.clean, with_risk=with_risk,
                                     mode=args.mode)
        print(format_round(rec), flush=True)
        if rec.get("all_failed"):
            print(f"STOPPED: all requests failed in round {r} (API refusing?)", flush=True)
            return 1
        if should_enable_risk(rec, with_risk):
            with_risk = True
            print("round 1: zero drex raw breaches; risk feedback on for later rounds", flush=True)
    print(f"usage: {dict(nace.USAGE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
