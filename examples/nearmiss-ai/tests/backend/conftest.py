"""Builder B test helpers.

ScriptedDecider is a TEST-ONLY stand-in for Drex: its scores are made up to
exercise control flow. It is never imported by service/, agents/ or evidence/.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from agents.drex_client import ChoiceResult, DrexError, DrexOutcome, QuestionSpec  # noqa: E402
from evidence.store import EvidenceStore  # noqa: E402
from service.contracts import CaseContext  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures"


class ScriptedDecider:
    """Answers each question id from a script. Round-specific overrides use keys like 'pattern_family@2'."""

    def __init__(self, script: dict[str, object] | None = None, fail_on: str | None = None):
        self.script = script or {}
        self.fail_on = fail_on
        self.calls: list[tuple[object, dict[str, QuestionSpec]]] = []
        self.closed = False

    def _round_of(self, questions: dict[str, QuestionSpec]) -> int:
        primary = next(iter(questions))
        return 1 + sum(1 for _, q in self.calls if next(iter(q)) == primary)

    def decide(self, state, questions):
        round_number = self._round_of(questions)
        self.calls.append((state, questions))
        if self.fail_on and self.fail_on in questions:
            raise DrexError("provider_unavailable", "Scripted test failure.")
        outcome = DrexOutcome(model="test-fake-model", request_id=f"test-{len(self.calls)}", duration_seconds=0.01)
        for qid, spec in questions.items():
            wanted = self.script.get(f"{qid}@{round_number}", self.script.get(qid))
            if spec.kind == "choice":
                options = spec.options
                selected = wanted if wanted in options else options[0]
                rest = 0.3 / (len(options) - 1) if len(options) > 1 else 0.0
                probs = {o: (0.7 if o == selected else rest) if len(options) > 1 else 1.0 for o in options}
                outcome.choices[qid] = ChoiceResult(selected=selected, probabilities=probs)
            else:
                outcome.nouls[qid] = float(wanted) if isinstance(wanted, (int, float)) else 0.8
        return outcome

    def close(self):
        self.closed = True


@pytest.fixture
def pattern_case() -> CaseContext:
    return CaseContext.model_validate_json((FIXTURES / "case_pattern_mock.json").read_text(encoding="utf-8"))


@pytest.fixture
def uncertain_case() -> CaseContext:
    return CaseContext.model_validate_json((FIXTURES / "case_uncertain_mock.json").read_text(encoding="utf-8"))


@pytest.fixture
def synthetic_store(tmp_path) -> EvidenceStore:
    """A store built from SYNTHETIC passages shaped like evidence/ingest.py output (test only)."""
    data = {
        "documents": [
            {
                "document_id": "SYN-DOC",
                "title": "Synthetic test document (not real guidance)",
                "source": "test://synthetic",
                "page_count": 2,
                "parse_job_id": "test-parse",
                "passages": [
                    {
                        "passage_id": "SYN-DOC#s0",
                        "document_id": "SYN-DOC",
                        "title": "Synthetic test document (not real guidance)",
                        "source": "test://synthetic",
                        "topic": "communication_coordination",
                        "passage_text": "Synthetic: clarify any call sign or taxi clearance that is unclear before continuing.",
                        "page": 1,
                        "status": "grounded",
                    },
                    {
                        "passage_id": "SYN-DOC#s1",
                        "document_id": "SYN-DOC",
                        "title": "Synthetic test document (not real guidance)",
                        "source": "test://synthetic",
                        "topic": "runway_surface",
                        "passage_text": "Synthetic: stop at the hold short line unless cleared to cross the runway.",
                        "page": 2,
                        "status": "parsed",
                    },
                ],
            }
        ]
    }
    (tmp_path / "passages.json").write_text(json.dumps(data), encoding="utf-8")
    return EvidenceStore(tmp_path)


@pytest.fixture
def empty_store(tmp_path) -> EvidenceStore:
    return EvidenceStore(tmp_path / "missing")
