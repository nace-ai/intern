"""The system-lane arithmetic matches the oracle without importing it."""
import inspect
from pathlib import Path

import billproof.bench.hard.arithmetic as arithmetic
from billproof.bench.hard.family_tau import choose_from_dev_metrics, escalates
from billproof.bench.hard.runner import call_billproof_system, call_family_hybrid
from billproof.schema import Answer, DecisionResult
from billproof.synth.hard.generate import build_case
from billproof.synth.hard.oracle import correct_pr, fee_yes
from billproof.synth.hard.questions import questions_for, visible_state


def test_module_does_not_import_the_oracle():
    text = Path(arithmetic.__file__).read_text()
    assert "oracle" not in text
    assert "gold" not in text


def test_facts_match_oracle_on_generated_cases():
    for index in range(8):
        record = build_case("dev", index)
        facts = arithmetic.compute_facts(record)
        responsibility = correct_pr(record)
        assert "gold" not in facts
        for line, fact in zip(record["lines"], facts["lines"]):
            number = int(line["line_no"])
            assert fact["correct_patient_responsibility_cents"] == responsibility[number]
            if fact["threshold_cents"] is None:
                assert fee_yes(line, record) is False
            else:
                over = int(line["charge_cents"]) > fact["threshold_cents"]
                assert over is fee_yes(line, record)
        values = list(responsibility.values())
        if any(value is None for value in values):
            assert facts["correct_patient_balance_cents"] is None
        else:
            assert facts["correct_patient_balance_cents"] == sum(int(value) for value in values)


def test_family_tau_rule_escalates_weak_dev_families():
    chosen = choose_from_dev_metrics()
    assert chosen["taus"]["fee"] == 1.0
    assert chosen["taus"]["pr"] == 1.0
    assert chosen["taus"]["bill_balance"] == 1.0
    assert chosen["taus"]["dup"] == 0.95
    assert chosen["taus"]["eob"] == 0.95
    assert chosen["taus"]["mue"] == 0.95
    assert chosen["taus"]["ncci"] == 0.95


def test_escalates_respects_family_tau():
    taus = {"fee": 1.0, "pr": 1.0, "dup": 0.95}
    assert escalates("fee", 0.99, taus)
    assert escalates("pr", 1.0, taus)
    assert not escalates("dup", 0.95, taus)
    assert escalates("dup", 0.949, taus)
    assert escalates("dup", None, taus)


class _Fake:
    def __init__(self, model: str):
        self.model = model
        self.simulated = False
        self.seen = {}

    def decide(self, state, questions, *, case_id):
        self.seen = {"state": state, "questions": questions, "case_id": case_id}
        answers = {
            question.name: Answer(
                name=question.name, choice="no", confidence=0.4,
                confidence_kind="self_reported", raw={"kept": True},
            )
            for question in questions
        }
        return DecisionResult(
            lane="test", model=self.model, answers=answers, latency_ms=1.0,
            input_tokens=10, output_tokens=2, cost_usd=0.0001,
        )


def test_billproof_system_passes_computed_facts_and_asks_every_question():
    source = inspect.getsource(call_billproof_system)
    assert "oracle" not in source
    record = build_case("dev", 0)
    fake = _Fake("drex-max-v1.1")
    row = call_billproof_system(record, fake)
    assert row["model"] == "BillProof system: code + Drex"
    assert row["drex_model"] == "drex-max-v1.1"
    assert "gold" not in fake.seen["state"]
    assert fake.seen["state"]["computed"]["lines"]
    assert fake.seen["case_id"].endswith("|billproof-system")
    asked = {question.name for question in fake.seen["questions"]}
    assert asked == set(record["gold"])


def test_family_hybrid_escalates_without_a_draft():
    record = build_case("dev", 1)
    questions = questions_for(record)
    drex_answers = {
        question.name: {
            "choice": "no", "confidence": 0.99, "confidence_kind": "native", "invalid": False,
        }
        for question in questions
    }
    drex_row = {
        "case_id": record["case_id"], "model": "drex-max-v1.1", "answers": drex_answers,
        "error": None, "simulated": False, "latency_ms": 1,
    }
    taus = {
        "dup": 0.95, "mue": 0.95, "ncci": 0.95, "fee": 1.0,
        "eob": 0.95, "pr": 1.0, "bill_balance": 1.0,
    }
    fake = _Fake("openai/gpt-6.1-sol")
    row = call_family_hybrid(record, fake, taus, drex_row)
    assert fake.seen["state"] == visible_state(record)
    assert "computed" not in fake.seen["state"]
    assert fake.seen["case_id"].endswith("|family-hybrid")
    escalated = [question.name for question in questions if question.family in ("fee", "pr", "bill_balance")]
    assert [question.name for question in fake.seen["questions"]] == escalated
    dup = next(question.name for question in questions if question.family == "dup")
    fee = next(question.name for question in questions if question.family == "fee")
    assert row["answers"][dup]["route"] == "drex"
    assert row["answers"][fee]["route"] == "frontier"
    assert row["n_escalated"] == len(escalated)
    assert row["gpt_model"] == "openai/gpt-6.1-sol"
