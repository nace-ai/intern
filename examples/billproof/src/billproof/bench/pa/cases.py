"""Loader for criteria-gym policy_checklist parquet rows.

Test unit (documented in docs/BENCH_PA.md): one `initial_packet` row per
`parent_id`. Each row is a self-contained single-turn task: `messages` =
[system, user] where the user message is JSON with `policy`, `request`,
`records`, `fact_definitions`, `criterion_fact_dependencies`, `episode`,
`scope_note`, `requested_fact_fields`. `target` is the gold structured answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from billproof.config import DATA_DIR

CRITERIA_GYM_DIR = DATA_DIR / "raw" / "criteria_gym"


@dataclass
class PaCase:
    row_id: str
    parent_id: str
    split: str  # "validation" | "development"
    scenario: str
    stage: str  # "initial_packet" | "supplement_received"
    connective: str  # "AND" | "OR"
    list_id: str
    publisher: str
    system: str
    policy: dict[str, Any]
    request: dict[str, Any]
    records: list[dict[str, Any]]
    fact_definitions: dict[str, str]
    criterion_deps: dict[str, list[str]]
    episode: dict[str, Any]
    scope_note: str
    requested_facts: list[str]
    target: dict[str, Any]
    scoring: dict[str, Any]
    construction: dict[str, Any]
    provenance: dict[str, Any]
    raw_user: str = field(repr=False, default="")

    @property
    def case_id(self) -> str:
        return self.row_id

    @property
    def n_criteria(self) -> int:
        return len(self.criterion_deps)

    @property
    def n_facts(self) -> int:
        return len(self.requested_facts)

    @property
    def n_records(self) -> int:
        return len(self.records)

    def state(self, *, drop_records: set[str] | None = None) -> dict[str, Any]:
        """The JSON state every column sees (no gold). Mirrors the dataset user payload."""
        records = self.records
        if drop_records:
            records = [r for r in records if r.get("id") not in drop_records]
        return {
            "policy": self.policy,
            "request": self.request,
            "records": records,
            "fact_definitions": self.fact_definitions,
            "criterion_fact_dependencies": self.criterion_deps,
            "scope_note": self.scope_note,
        }

    def gold_facts(self) -> dict[str, Any]:
        return dict(self.target.get("facts") or {})

    def gold_criteria(self) -> dict[str, dict]:
        return dict(self.target.get("criteria") or {})


def _parse_row(row: dict[str, Any], split: str) -> PaCase:
    messages = json.loads(row["messages"])
    system = next(m["content"] for m in messages if m["role"] == "system")
    raw_user = next(m["content"] for m in messages if m["role"] == "user")
    user = json.loads(raw_user)
    return PaCase(
        row_id=row["id"],
        parent_id=row["parent_id"],
        split=split,
        scenario=row["scenario"],
        stage=row["stage"],
        connective=row["connective"],
        list_id=row["list_id"],
        publisher=row["publisher"],
        system=system,
        policy=user["policy"],
        request=user["request"],
        records=user["records"],
        fact_definitions=user.get("fact_definitions") or {},
        criterion_deps=user.get("criterion_fact_dependencies") or {},
        episode=user.get("episode") or {},
        scope_note=user.get("scope_note") or "",
        requested_facts=list(user.get("requested_fact_fields") or []),
        target=json.loads(row["target"]),
        scoring=json.loads(row.get("scoring") or "{}"),
        construction=json.loads(row.get("construction") or "{}"),
        provenance=json.loads(row.get("provenance") or "{}"),
        raw_user=raw_user,
    )


def load_cases(split: str, *, stages: tuple[str, ...] = ("initial_packet",)) -> list[PaCase]:
    """split: 'validation' | 'development'. Default: initial_packet rows only —
    one row per parent_id (the benchmark test unit)."""
    path = CRITERIA_GYM_DIR / f"{split}.parquet"
    table = pq.read_table(path)
    rows = table.to_pylist()
    cases = [_parse_row(r, split) for r in rows]
    if stages is not None:
        cases = [c for c in cases if c.stage in stages]
    # one initial_packet row per parent_id; supplement variants share a parent
    # (satisfied/not_satisfied) and are each distinct cases.
    seen: set[str] = set()
    out: list[PaCase] = []
    for c in cases:
        key = c.parent_id if c.stage == "initial_packet" else c.row_id
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def stratified_sample(cases: list[PaCase], n: int, *, seed: int = 0) -> list[PaCase]:
    """Proportional-by-decision sample that spreads across list_ids.

    Within each decision stratum, cases are round-robined over list_id so every
    policy list is represented roughly equally."""
    import random

    rng = random.Random(seed)
    by_dec: dict[str, list[PaCase]] = {}
    for c in cases:
        by_dec.setdefault(c.target["decision"], []).append(c)
    picked: list[PaCase] = []
    total = len(cases)
    # largest-remainder proportional allocation
    quotas = {d: n * len(cs) / total for d, cs in by_dec.items()}
    whole = {d: int(q) for d, q in quotas.items()}
    leftover = n - sum(whole.values())
    for d, _ in sorted(quotas.items(), key=lambda kv: kv[1] - whole[kv[0]], reverse=True):
        if leftover <= 0:
            break
        whole[d] += 1
        leftover -= 1
    import hashlib

    def stable_h(c: PaCase) -> int:
        # hash() is salted per-process; use md5 so the sample is reproducible.
        return int(hashlib.md5(f"{seed}:{c.list_id}:{c.row_id}".encode()).hexdigest()[:16], 16)

    for d, cs in by_dec.items():
        rng.shuffle(cs)
        # sort by list_id so picks spread across lists deterministically-ish
        cs.sort(key=lambda c: (stable_h(c), c.row_id))
        seen_lists: dict[str, int] = {}
        chosen: list[PaCase] = []
        pool = list(cs)
        while pool and len(chosen) < whole[d]:
            # prefer least-picked list_id
            pool.sort(key=lambda c: (seen_lists.get(c.list_id, 0), stable_h(c)))
            c = pool.pop(0)
            chosen.append(c)
            seen_lists[c.list_id] = seen_lists.get(c.list_id, 0) + 1
        picked.extend(chosen)
    picked.sort(key=lambda c: c.row_id)
    return picked
