"""Adapter interfaces. Each service has a real implementation (documented wire format, tested with mocked HTTP)
and a simulator/fixture implementation; `get_*()` factories in each module pick one via config.settings().use_real().
"""

from __future__ import annotations

from typing import Protocol

from billproof.schema import Answer, Citation, DecisionResult, GoldItem, Question


class DecisionAdapter(Protocol):
    lane: str  # "drex" | "frontier"
    simulated: bool

    def decide(
        self,
        state: dict,
        questions: list[Question],
        *,
        case_id: str,
        gold: dict[str, GoldItem] | None = None,  # simulators only; real adapters MUST ignore it
    ) -> DecisionResult: ...


class VerifierAdapter(Protocol):
    """Frontier in the hybrid lane: verifies Drex's draft answers, does not regenerate from scratch."""

    def verify(
        self,
        state: dict,
        questions: list[Question],
        drafts: dict[str, Answer],
        *,
        case_id: str,
        gold: dict[str, GoldItem] | None = None,
    ) -> DecisionResult: ...


class ExtractedField(dict):
    """{"path": str, "value": any, "status": "found"|"not_found"|"ambiguous", "citations": [Citation-dict]}"""


class DocumentAdapter(Protocol):
    simulated: bool

    def extract(self, case_id: str, doc: str) -> list[dict]: ...  # list of ExtractedField dicts

    def ground(self, case_id: str, citation: Citation) -> bytes | None: ...  # PNG crop of the region
