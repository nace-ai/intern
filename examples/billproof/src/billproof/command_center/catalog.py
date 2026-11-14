"""Slim catalog of every criteria-gym policy_checklist row.

Train, validation, and development, both stages. parser is used
as-is. This module does not score and does not call a model. Published
clause text and dates are copied off the rows.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Iterator

import pyarrow.parquet as pq

from billproof.bench.pa.cases import CRITERIA_GYM_DIR, PaCase, _parse_row
from billproof.command_center.common import CASES_PATH, POLICIES_PATH, atomic_write, now_iso

SPLITS = ("train", "validation", "development")

_DATE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def drug_name(title: str | None, lead_in: str | None) -> str | None:
    """A name that is already printed on the list, or None.

    Pharmacy pages put the drug in capitals just before
    ``Length of Authorization``. Drug-criteria titles often end in
    ``Criteria``. Filenames and bare dates are not drug names.
    """
    lead = lead_in or ""
    idx = lead.find("Length of Authorization")
    if idx > 0:
        window = lead[max(0, idx - 90):idx]
        match = re.search(r"([A-Z][A-Z0-9][A-Z0-9 /()+.,'-]{1,70})\s*$", window)
        if match:
            name = " ".join(match.group(1).split())
            if len(name) >= 3:
                return name
    text = (title or "").strip()
    if not text or text.lower().endswith(".pdf") or _DATE.fullmatch(text):
        return None
    if text.lower().endswith(" criteria"):
        text = text[: -len(" criteria")].strip()
    return text or None


def _iter_raw(only: str | None = None) -> Iterator[tuple[str, dict[str, Any]]]:
    names = (only,) if only else SPLITS
    for split in names:
        path = CRITERIA_GYM_DIR / f"{split}.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"missing criteria-gym file: {path}")
        table = pq.read_table(path)
        for row in table.to_pylist():
            yield split, row


def load_pa_cases(split: str | None = None) -> list[PaCase]:
    """Every policy_checklist row. ``split`` None loads all three files."""
    out: list[PaCase] = []
    for name, row in _iter_raw(split):
        out.append(_parse_row(row, name))
    return out


def load_cases_by_ids(wanted: set[str]) -> dict[str, PaCase]:
    """Parse only the requested row ids. Used when a new criterion is asked."""
    found: dict[str, PaCase] = {}
    if not wanted:
        return found
    for name, row in _iter_raw():
        if row.get("id") in wanted:
            found[row["id"]] = _parse_row(row, name)
            if len(found) == len(wanted):
                break
    return found


def _policy_blob(case: PaCase) -> dict[str, Any]:
    policy = case.policy
    window = policy.get("exercise_applicability") or {}
    clauses = []
    fact_text: dict[str, str] = {}
    for item in policy.get("criteria_list") or []:
        fact = str(item.get("fact") or "")
        text = (item.get("text") or fact).strip()
        if not fact:
            continue
        clauses.append({"fact": fact, "text": text})
        fact_text[fact] = text
    rule = {str(k): [str(f) for f in (v or [])] for k, v in case.criterion_deps.items()}
    criteria = []
    for name, facts in rule.items():
        parts = [fact_text.get(f, f) for f in facts]
        if len(parts) <= 1:
            text = parts[0] if parts else name
        else:
            text = " OR ".join(parts)
        criteria.append({"name": name, "text": text, "facts": facts})
    title = policy.get("document_title") or ""
    lead = policy.get("lead_in") or ""
    return {
        "id": case.list_id,
        "policy_id": policy.get("id"),
        "payer": policy.get("payer"),
        "state": (case.provenance or {}).get("state"),
        "family": case.list_id.split(":", 1)[0],
        "publisher": case.publisher,
        "title": title,
        "drug": drug_name(title, lead),
        "connective": case.connective,
        "window": {"from": window.get("from") or "", "through": window.get("through") or ""},
        "clauses": clauses,
        "criteria": criteria,
        "rule": rule,
        "facts": list(case.requested_facts),
    }


def build_catalog() -> dict[str, Any]:
    """One pass over the parquet. Returns ``{policies, cases, counts}``."""
    grouped: dict[str, dict[str, Any]] = {}
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for split, row in _iter_raw():
        case = _parse_row(row, split)
        if case.case_id in seen:
            continue
        seen.add(case.case_id)
        blob = grouped.get(case.list_id)
        if blob is None:
            blob = _policy_blob(case)
            blob["n_cases"] = 0
            blob["n_initial"] = 0
            blob["n_followup"] = 0
            grouped[case.list_id] = blob
        blob["n_cases"] += 1
        if case.stage == "initial_packet":
            blob["n_initial"] += 1
        else:
            blob["n_followup"] += 1
        cases.append({
            "case_id": case.case_id,
            "parent_id": case.parent_id,
            "split": case.split,
            "stage": case.stage,
            "scenario": case.scenario,
            "list_id": case.list_id,
            "service_date": case.request.get("service_date") or "",
            "request_payer": case.request.get("payer") or "",
            "gold": (case.target or {}).get("decision"),
        })
    policies = [grouped[k] for k in sorted(grouped)]
    state_of = {policy["id"]: policy["state"] for policy in policies}
    cases.sort(key=lambda c: c["case_id"])
    n_initial = sum(1 for c in cases if c["stage"] == "initial_packet")
    counts = {
        "n_policies": len(policies),
        "n_cases": len(cases),
        "n_initial": n_initial,
        "n_followup": len(cases) - n_initial,
        "n_families": len({p["family"] for p in policies}),
        "by_state": dict(sorted(Counter(
            state_of[row["list_id"]] for row in cases
        ).items())),
        "source": "criteria-gym policy_checklist train+validation+development",
        "built_at": now_iso(),
    }
    return {"policies": policies, "cases": cases, "counts": counts}


def write_catalog(catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    catalog = catalog or build_catalog()
    payload = {
        "counts": catalog["counts"],
        "policies": catalog["policies"],
    }
    POLICIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(POLICIES_PATH, json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    lines = [json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in catalog["cases"]]
    atomic_write(CASES_PATH, ("\n".join(lines) + "\n") if lines else "")
    return catalog["counts"]


def load_policies() -> dict[str, Any]:
    if not POLICIES_PATH.is_file():
        raise FileNotFoundError(
            "policies.json is missing. Run scripts/cc_scale.py --catalog-only."
        )
    return json.loads(POLICIES_PATH.read_text())


def load_case_rows() -> list[dict[str, Any]]:
    if not CASES_PATH.is_file():
        raise FileNotFoundError(
            "cases_index.jsonl is missing. Run scripts/cc_scale.py --catalog-only."
        )
    rows = []
    with CASES_PATH.open() as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def index_policies(policies: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """list_id and the published policy id both resolve."""
    out: dict[str, dict[str, Any]] = {}
    for policy in policies:
        out[policy["id"]] = policy
        pid = policy.get("policy_id")
        if pid:
            out[str(pid)] = policy
    return out
