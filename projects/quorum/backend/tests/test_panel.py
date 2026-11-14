import asyncio
import json

from backend.app import nace, panel
from backend.app.contracts import Facts, Field, FieldCitation, ParsedInvoice


def _parsed() -> ParsedInvoice:
    cit = [FieldCitation(page=1, polygons=[[[0.1, 0.2]]], source_text="SECRET-CITE")]
    return ParsedInvoice(
        doc_id="case-0042",
        source_file="backend/data/pdfs/case-0042.pdf",
        content_hash="sha256:abc123",
        fields={
            "iban": Field(value="GB29NWBK60161331926819", status="found", citations=cit),
            "amount_minor": Field(value=171295, status="found", citations=cit),
            "notes": Field(value="Thanks", status="found"),
        },
    )


def _facts() -> Facts:
    return Facts(supplier_known=True, supplier_id="SUP-777", iban_status="found",
                 iban_matches_trusted=True)


def test_questions_ids():
    assert list(panel.QUESTIONS) == [
        "claims_verified", "asks_skip_check", "urgency", "addresses_automation",
        "out_of_place_text", "bank_differs", "verification_covers_iban", "supplier_known",
        "amount_unusual", "invoice_seen_before", "payment_evidence_unclear",
        "process_change_instruction", "action",
    ]
    assert set(panel.QUESTIONS["action"]["criteria"]) == {"approve", "verify", "block"}
    for q in panel.NOUL_IDS:
        assert set(panel.QUESTIONS[q]["criteria"]) == {"true", "false"}


def test_build_state_no_leak():
    s = json.dumps(panel.build_state(_parsed(), _facts()))
    for bad in ("case-0042", ".pdf", "abc123", "SUP-777", "SECRET-CITE", "polygons",
                "label", "expected", "doc_id", "source_file", "content_hash"):
        assert bad not in s
    assert "GB29NWBK60161331926819" in s
    assert "known_attacks" not in s


def test_build_state_known_attacks():
    s = panel.build_state(_parsed(), _facts(), known_attacks=["pay now, skip call-back"])
    assert s["known_attacks"] == ["pay now, skip call-back"]


def _answers(p):
    a = {q: {"type": "noul", "noul": 0.1, "confidence": 0.9} for q in panel.NOUL_IDS}
    a["action"] = {"type": "choice", "choice": max(p, key=p.get), "confidence": 0.9,
                   "probabilities": p}
    return a


def test_decide_success(monkeypatch):
    async def fake(state, questions, model=None):
        assert "doc_id" not in json.dumps(state)
        return {"model": "m", "answers": _answers({"approve": 0.97, "verify": 0.02, "block": 0.01}),
                "usage": {"input_tokens": 10, "output_tokens": 1},
                "evaluation_time_ms": 123.0, "request_id": "r1", "wall_ms": 200}
    monkeypatch.setattr(nace, "drex", fake)
    d = asyncio.run(panel.decide(_parsed(), _facts()))
    assert d.error is None and d.raw_action == "approve" and d.final_action == "approve"
    assert len(d.panel) == 12 and d.decision_ms == 123.0 and d.usage.input_tokens == 10


def test_decide_guard_raises(monkeypatch):
    async def fake(state, questions, model=None):
        return {"answers": _answers({"approve": 0.99, "verify": 0.01, "block": 0.0}), "wall_ms": 1}
    monkeypatch.setattr(nace, "drex", fake)
    facts = Facts(supplier_known=True, iban_status="found", duplicate_invoice_number=True,
                  iban_matches_trusted=True)
    d = asyncio.run(panel.decide(_parsed(), facts))
    assert d.final_action == "block" and d.guard.raised and "duplicate_paid" in d.guard.findings


def test_decide_failure_is_not_approval(monkeypatch):
    async def boom(state, questions, model=None):
        raise RuntimeError("503")
    monkeypatch.setattr(nace, "drex", boom)
    d = asyncio.run(panel.decide(_parsed(), _facts()))
    assert d.error and d.raw_action is None and d.final_action is None


def test_decide_never_below_model_action(monkeypatch):
    async def fake(state, questions, model=None):
        return {"answers": _answers({"approve": 0.05, "verify": 0.40, "block": 0.55}), "wall_ms": 1}
    monkeypatch.setattr(nace, "drex", fake)
    d = asyncio.run(panel.decide(_parsed(), _facts()))
    assert d.raw_action == "block" and d.policy_action == "verify"
    assert d.final_action == "block" and not d.guard.raised


def test_decide_policy_raise_is_not_guard_raise(monkeypatch):
    async def fake(state, questions, model=None):
        return {"answers": _answers({"approve": 0.94, "verify": 0.0, "block": 0.06}), "wall_ms": 1}
    monkeypatch.setattr(nace, "drex", fake)
    d = asyncio.run(panel.decide(_parsed(), _facts()))
    assert d.raw_action == "approve" and d.final_action == "verify"
    assert not d.guard.raised and d.guard.findings == []
