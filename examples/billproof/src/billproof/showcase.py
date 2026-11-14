"""Read the hand-picked showcase manifest and hand cases to the pipeline.

The showcase is explicitly a set of examples for the live demo, not a
benchmark: nothing here feeds an accuracy number. Reused demo cases keep their
published ids (``SYN-D01`` …); showcase-only cases live under
``data/showcase/cases/`` and are built by ``billproof.synth.showcase``.

Manifest shape::

    {"schema_version": 1, "cases": [
        {"case_id": "SC-01", "source_case_id": "SYN-D01", "title", "story",
         "expected_outcome", "expected_color", "status_color", "billed_cents",
         "expected_owed_cents", "verified_owed_cents", "dollars_at_stake_cents",
         "products", "documents", ...}]}

``case_id`` names the showcase slot; ``source_case_id`` is the id of the
``schema.Case`` that the pipeline can analyse.

Colour fields: ``expected_color`` is the semantic action token
(``ok`` / ``dispute`` / ``appeal`` / ``ask`` / ``insufficient``), not a literal
colour. ``status_color`` is the literal card colour (``green`` / ``red`` /
``amber``); ``amber`` means human review or missing evidence.

Owed fields: ``verified_owed_cents`` is the amount the UI may display as owed,
and is ``null`` when the amount is not verified. ``expected_owed_cents`` is the
rule-computed amount only when ``expected_owed_known`` is true; otherwise it is
just the bill's current ask and must never be shown as owed.
"""
from __future__ import annotations

import json
from pathlib import Path

from billproof.config import DATA_DIR, PKG_ROOT, SYN_DIR
from billproof.schema import Action, Case

SHOWCASE_DIR = DATA_DIR / "showcase"
MANIFEST_PATH = SHOWCASE_DIR / "cases.json"
SCHEMA_VERSION = 1
EXPECTED_COLORS = {"ok", "dispute", "appeal", "ask", "insufficient"}
STATUS_COLORS = {"green", "red", "amber"}
PRODUCT_STATUSES = {"active", "pending"}
REQUIRED_FIELDS = ("case_id", "source_case_id", "title", "story", "expected_outcome",
                   "expected_color", "status_color", "billed_cents", "expected_owed_cents",
                   "verified_owed_cents", "expected_owed_basis", "expected_owed_known",
                   "dollars_at_stake_cents", "products", "documents")


class ShowcaseError(ValueError):
    """The manifest or a referenced asset is invalid."""


def load_showcase_manifest(path: Path | None = None) -> dict:
    path = path or MANIFEST_PATH
    if not path.exists():
        raise ShowcaseError(f"showcase manifest not found: {path}")
    manifest = json.loads(path.read_text())
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ShowcaseError(f"unsupported showcase schema_version: {manifest.get('schema_version')!r}")
    if not isinstance(manifest.get("cases"), list):
        raise ShowcaseError("showcase manifest has no cases list")
    return manifest


def showcase_entries() -> list[dict]:
    """Manifest entries in demo order (for a case picker)."""
    return load_showcase_manifest()["cases"]


def showcase_case_entry(case_id: str) -> dict:
    """Look up an entry by showcase id (``SC-01``) or source id (``SYN-D01``)."""
    for entry in showcase_entries():
        if case_id in (entry["case_id"], entry["source_case_id"]):
            return entry
    raise KeyError(f"unknown showcase case: {case_id}")


def _case_path(source_case_id: str) -> Path:
    for path in (SYN_DIR / "cases" / f"{source_case_id}.json",
                 SHOWCASE_DIR / "cases" / f"{source_case_id}.json"):
        if path.exists():
            return path
    raise ShowcaseError(f"no case file for {source_case_id!r}")


def load_showcase_case(case_id: str) -> Case:
    """Load one showcase case as a pipeline-ready ``schema.Case``."""
    entry = showcase_case_entry(case_id)
    return Case.model_validate_json(_case_path(entry["source_case_id"]).read_text())


def load_showcase_cases() -> list[Case]:
    """All showcase cases, pipeline-ready, in demo order."""
    return [load_showcase_case(entry["case_id"]) for entry in showcase_entries()]


def showcase_documents(case_id: str) -> list[dict]:
    """``[{kind, pdf, pages}]`` for the entry, with pdf paths relative to billproof/."""
    return list(showcase_case_entry(case_id)["documents"])


def showcase_owed_cents(case_id: str) -> int | None:
    """The only amount the UI may display as owed; ``None`` means not verified."""
    return showcase_case_entry(case_id).get("verified_owed_cents")


def validate_showcase() -> dict:
    """Minimal consistency check: ids, outcomes, money and referenced assets."""
    manifest = load_showcase_manifest()
    problems: list[str] = []
    product_status = manifest.get("product_status", {})
    if set(product_status) != set(manifest.get("products", {})):
        problems.append("product_status keys must match products")
    for name, status in product_status.items():
        if status not in PRODUCT_STATUSES:
            problems.append(f"product {name!r}: unknown status {status!r}")
    seen_showcase: set[str] = set()
    seen_source: set[str] = set()
    for index, entry in enumerate(manifest["cases"]):
        label = entry.get("case_id", f"cases[{index}]")
        missing = [field for field in REQUIRED_FIELDS if field not in entry]
        if missing:
            problems.append(f"{label}: missing {', '.join(missing)}")
            continue
        if entry["case_id"] in seen_showcase:
            problems.append(f"{label}: duplicate case_id")
        if entry["source_case_id"] in seen_source:
            problems.append(f"{label}: duplicate source_case_id")
        seen_showcase.add(entry["case_id"])
        seen_source.add(entry["source_case_id"])
        if entry["expected_outcome"] not in {action.value for action in Action}:
            problems.append(f"{label}: unknown expected_outcome {entry['expected_outcome']!r}")
        if entry["expected_color"] not in EXPECTED_COLORS:
            problems.append(f"{label}: unknown expected_color {entry['expected_color']!r}")
        if entry["status_color"] not in STATUS_COLORS:
            problems.append(f"{label}: unknown status_color {entry['status_color']!r}")
        for field in ("billed_cents", "expected_owed_cents", "dollars_at_stake_cents"):
            value = entry[field]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                problems.append(f"{label}: {field} must be a non-negative int")
        known = entry["expected_owed_known"]
        verified = entry["verified_owed_cents"]
        if not isinstance(known, bool):
            problems.append(f"{label}: expected_owed_known must be a bool")
        elif known:
            if entry["expected_owed_basis"] != "eob_recomputed":
                problems.append(f"{label}: verified owed must have expected_owed_basis eob_recomputed")
            if verified != entry["expected_owed_cents"]:
                problems.append(f"{label}: verified_owed_cents must equal expected_owed_cents")
        else:
            if entry["expected_owed_basis"] != "bill_balance_unverified":
                problems.append(f"{label}: unverified owed must have expected_owed_basis bill_balance_unverified")
            if verified is not None:
                problems.append(f"{label}: verified_owed_cents must be null when the amount is unverified")
            if entry["expected_owed_cents"] != entry["billed_cents"]:
                problems.append(f"{label}: unverified expected_owed_cents must equal the bill's ask")
        if isinstance(entry["billed_cents"], int) and isinstance(entry["expected_owed_cents"], int):
            if entry["expected_owed_cents"] > entry["billed_cents"] and known:
                problems.append(f"{label}: expected_owed_cents exceeds billed_cents")
        if not isinstance(entry["products"], list) or not entry["products"] or any(
            not isinstance(product, dict) or not product.get("name") or not product.get("role")
            or product.get("status") not in PRODUCT_STATUSES
            for product in entry["products"]
        ):
            problems.append(f"{label}: products need name, role and active/pending status")
        if not isinstance(entry["documents"], list) or not entry["documents"]:
            problems.append(f"{label}: documents must be a non-empty list")
        try:
            case = load_showcase_case(entry["case_id"])
        except (ShowcaseError, KeyError, ValueError) as exc:
            problems.append(f"{label}: cannot load case ({exc})")
            continue
        if case.case_id != entry["source_case_id"]:
            problems.append(f"{label}: source_case_id {entry['source_case_id']!r} != case_id {case.case_id!r}")
        if case.bill.patient_balance_cents != entry["billed_cents"]:
            problems.append(f"{label}: billed_cents does not match the bill's patient balance")
        for document in entry["documents"]:
            pdf = PKG_ROOT / document.get("pdf", "")
            if not pdf.exists():
                problems.append(f"{label}: missing document {document.get('pdf')!r}")
    return {"ok": not problems, "n": len(manifest["cases"]), "problems": problems}


def _main() -> int:
    result = validate_showcase()
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
