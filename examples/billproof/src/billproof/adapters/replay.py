"""Replay adapter: serves recorded answers from data/runs/<run_id>.jsonl.

Used by the race stream and the stage demo to re-run a recorded bench exactly
(timings and answers preserved; results flagged replayed=True).
Row format: docs/CONTRACT.md 2 (one JSON line per (case, lane)).
"""

from __future__ import annotations

import json
from pathlib import Path

from billproof.config import RUNS_DIR
from billproof.schema import Answer, DecisionResult, GoldItem, Question


class ReplayAdapter:
    """One lane of one recorded run. Indexed by case_id at construction."""

    simulated = False

    def __init__(self, run_id: str, lane: str, *, runs_dir: Path = RUNS_DIR):
        self.run_id = run_id
        self.lane = lane
        path = runs_dir / f"{run_id}.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"no recorded run at {path}")
        self._rows: dict[str, dict] = {}
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    row = json.loads(line)
                    if row.get("lane") == lane:
                        self._rows[row["case_id"]] = row

    @property
    def case_ids(self) -> list[str]:
        return list(self._rows)

    def decide(self, state: dict | str, questions: list[Question], *, case_id: str,
               gold: dict[str, GoldItem] | None = None) -> DecisionResult:
        row = self._rows.get(case_id)
        if row is None:
            return DecisionResult(lane=self.lane, model="replay", answers={}, latency_ms=0.0,
                                  replayed=True,
                                  error=f"no replay row for lane={self.lane} case={case_id} in {self.run_id}")
        answers: dict[str, Answer] = {}
        for q in questions:
            rec = (row.get("answers") or {}).get(q.name)
            if rec is None:
                continue
            answers[q.name] = Answer(
                name=q.name, choice=rec["choice"],
                probabilities=rec.get("probabilities"),
                confidence=rec.get("confidence", 0.0),
                confidence_kind=rec.get("confidence_kind", "native"),
                route=rec.get("route"), raw={"replayed_from": self.run_id})
        return DecisionResult(
            lane=self.lane, model=row.get("model", "replay"), answers=answers,
            latency_ms=row.get("latency_ms", 0.0),
            input_tokens=row.get("input_tokens", 0),
            output_tokens=row.get("output_tokens", 0),
            cost_usd=row.get("cost_usd", 0.0),
            simulated=row.get("simulated", False), replayed=True)


def get_replay(run_id: str, lane: str) -> ReplayAdapter:
    return ReplayAdapter(run_id, lane)
