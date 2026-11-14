"""Shared rulebook and evidence serializer. Model input is an allowlist."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from config import PROMPT_VERSION, RULEBOOK_VERSION

RULEBOOK = """COSMIC CUSTOMS RULEBOOK
version: cosmic-customs-rules-1

You are the orbital cargo inspector. Decide one verdict for the shipment described in the evidence.

Verdicts
- HOLD: detain the shipment.
- REVIEW: send the packet to a human reviewer.
- CLEAR: release the shipment.

Precedence, first match wins
1. A confirmed damaged seal, or a known seal pressure below 90 kPa, is HOLD. This still holds when other facts are missing or contradict each other.
2. Otherwise, unresolved contradictions or any missing required fact is REVIEW.
3. Otherwise, when a containment certificate exists, REVIEW if abs(certificate gross weight - manifest gross weight) / manifest gross weight is greater than 0.03.
4. Otherwise, singing or telepathic cargo without a valid certificate is REVIEW, unless it has a valid emergency waiver and its hazard class is not class 4 acoustic.
5. Otherwise CLEAR.

Required facts
- Always required: whether the cargo is singing or telepathic, hazard class, seal condition, seal pressure, manifest gross weight, and certificate status.
- Waiver status is required only when the cargo is singing or telepathic and the certificate is not valid.
- Certificate gross weight is required only when the certificate is valid or expired.
- The explicit status "not on file" means the item is absent. That is known, not unknown.
- Silence, an omitted line, or "status unknown" means the fact is unknown.
- A waiver never bypasses a damaged seal, pressure below 90 kPa, contradictory statements, or a weight mismatch.
- Class 4 acoustic cargo is not an automatic HOLD by itself.
- Exactly 90 kPa does not meet the pressure hold. Exactly 3 percent discrepancy does not meet the weight review.
- A suspicion that the packet says was corrected is not a confirmed physical hazard.

Use only this rulebook and the evidence documents. Do not assume a stamp, a template, or a hidden label.
"""

BANNED_INPUT_MARKERS = (
    "expected_verdict",
    "rule_signature",
    "template_family",
    "source_annotations",
    "\"split\"",
    "generator_facts",
)


def evidence_block(documents: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for document in documents:
        chunks.append(
            f"DOCUMENT {document['document_id']}\nKIND {document['kind']}\n{document['text'].strip()}"
        )
    return "\n\n".join(chunks)


def shared_input_from_documents(documents: list[dict[str, Any]]) -> str:
    """Rulebook plus evidence only. Caller must not pass hidden case fields."""
    return f"{RULEBOOK.strip()}\n\nEVIDENCE\n\n{evidence_block(documents)}\n"


def shared_input_for_case(case: dict[str, Any]) -> str:
    return shared_input_from_documents(case["documents"])


def input_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def assert_model_input_allowlist(case: dict[str, Any], rendered: str) -> None:
    """Fail if hidden generator fields or verdict stamps leak into model input."""
    lowered = rendered.lower()
    for marker in BANNED_INPUT_MARKERS:
        if marker.lower() in lowered:
            raise ValueError(f"model input contains hidden marker {marker}")
    for stamp in ("verdict: clear", "verdict: hold", "verdict: review", "outcome stamp"):
        if stamp in lowered:
            raise ValueError(f"model input contains outcome stamp {stamp}")
    if case["expected_verdict"] in rendered.split("EVIDENCE", 1)[-1].upper():
        # The rulebook names the verdicts. The evidence section must not.
        evidence = rendered.split("EVIDENCE", 1)[1].upper()
        for word in ("CLEAR", "HOLD", "REVIEW"):
            if word in evidence:
                raise ValueError(f"evidence contains verdict word {word}")
    blob = json.dumps(
        {
            "facts": case["facts"],
            "source_annotations": case["source_annotations"],
            "template_family": case["template_family"],
            "rule_signature": case["rule_signature"],
        },
        sort_keys=True,
    )
    if case["rule_signature"] in rendered:
        raise ValueError("rule signature leaked into model input")
    if blob in rendered:
        raise ValueError("generator payload leaked into model input")


def prompt_identity() -> dict[str, str]:
    return {
        "rulebook_version": RULEBOOK_VERSION,
        "prompt_version": PROMPT_VERSION,
        "rulebook_sha256": input_hash(RULEBOOK),
    }
