"""Replay one decision alone: `python3 -m engine.replay runs/<run_id> <event_index>`.

Re-sends that single question with the state saved in the run's request file and prints the old and the
new answer. A simulated run replays offline and must give the identical answer; a live run needs the key
and may differ, which is the point of the check. The replay's own audit record goes to runs/<run_id>/replays/.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from . import drex


def _show(rec: dict) -> str:
    out = f"p(yes)={rec['p']:.4f}" if "p" in rec else "dist=[" + ", ".join(f"{v:.4f}" for v in rec["dist"]) + "]"
    return out + f" stable={rec['stable']} conf={rec['conf']:.3f}"


def replay(run_dir: Path | str, index: int) -> dict:
    """Return {"event", "old", "new", "same"} for the Drex event at `index` in the run's events.jsonl."""
    run = Path(run_dir)
    events = [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if not 0 <= index < len(events):
        raise IndexError(f"event index {index} out of range 0..{len(events) - 1}")
    ev = events[index]
    if ev.get("src") != "drex" or "qid" not in ev or "req" not in ev:
        raise ValueError(f"event {index} ({ev.get('src')}/{ev.get('id')}) is not a Drex decision")
    saved = json.loads((run / "requests" / f"{ev['req']}.json").read_text(encoding="utf-8"))
    spec = drex.from_wire(saved["request"]["questions"][ev["qid"]], saved.get("hints", {}).get(ev["qid"]))
    images = [p for p in saved.get("image_paths", []) if Path(p).is_file()]
    session = drex.Session(run_dir=run / "replays" / f"{index}-{time.time_ns()}", simulate=bool(saved["simulated"]))
    new = session.decide(saved["request"]["state"], {ev["qid"]: spec}, saved["model"], images or None)[ev["qid"]]
    old = {k: ev[k] for k in ("p", "dist", "stable", "conf") if k in ev}
    same = all(abs(a - b) < 1e-9 for a, b in zip(old.get("dist", [old.get("p")]), new.get("dist", [new.get("p")]))) \
        and old["stable"] == new["stable"]
    return {"event": ev, "old": old, "new": new, "same": same}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or not args[1].isdigit():
        print("usage: python3 -m engine.replay runs/<run_id> <event_index>")
        return 2
    try:
        res = replay(args[0], int(args[1]))
    except (OSError, ValueError, IndexError, KeyError, drex.DrexError) as e:
        print(f"replay failed: {e}")
        return 1
    ev = res["event"]
    print(f"event {ev['i']}: {ev['agent']} {ev['id']} ({ev['req']}, {ev['q']})")
    print("old:", _show(res["old"]))
    print("new:", _show(res["new"]))
    print("same answer:", res["same"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
