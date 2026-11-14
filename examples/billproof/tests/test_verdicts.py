from billproof.schema import Answer, Case, Eob, EobLine, ErrorType, RuleFlag
from billproof.decisions.verdicts import compute_verdict


def make_case(balance: int, responsibility: int) -> Case:
    return Case.model_validate({
        "case_id": "SYN-T01", "split": "test", "title": "Synthetic claim",
        "patient": {"patient_id": "PT-T01"},
        "provider": {"name": "Sample Clinic"},
        "bill": {
            "account_no": "ACCT-T01", "statement_date": "2026-01-10", "lines": [
                {"line_no": 1, "dos": "2026-01-02", "code": "X0001", "label": "Clinic visit",
                 "units": 1, "charge_cents": 8000}
            ], "total_charges_cents": 8000, "patient_balance_cents": balance,
        },
        "eob": {
            "payer": "Sample Plan", "claim_id": "CLM-T01", "processed_date": "2026-01-08",
            "lines": [{"line_no": 1, "code": "X0001", "billed_cents": 8000, "allowed_cents": 6000,
                       "plan_paid_cents": 4000, "patient_resp_cents": responsibility}],
            "patient_resp_total_cents": responsibility,
        },
        "plan": {"plan_name": "Sample PPO", "deductible_cents": 10000, "oop_max_cents": 500000},
    })


def test_charge_stake_reduces_owed_by_eob_patient_share():
    case = make_case(4500, 4200)
    flag = RuleFlag(check=ErrorType.DUPLICATE, line_no=1, fired=True, detail="duplicate",
                    rule_cite="duplicate screening", amount_cents=1500)
    answers = {
        "dup_L1": Answer(name="dup_L1", choice="yes", confidence=0.99),
        "action_L1": Answer(name="action_L1", choice="DISPUTE", confidence=0.99),
    }
    verdict = compute_verdict(case, [flag], answers)
    assert verdict.disputed_cents == 787
    assert verdict.owed_cents == 3413
    assert verdict.case_action == "DISPUTE"
    assert verdict.needs_human is True


def test_eob_mismatch_does_not_subtract_twice_from_eob_responsibility():
    case = make_case(5000, 3200)
    flag = RuleFlag(check=ErrorType.EOB_BILL_MISMATCH, fired=True, detail="over balance",
                    rule_cite="EOB reconciliation", amount_cents=1800)
    answer = Answer(name="eob_mismatch", choice="yes", confidence=0.91)
    verdict = compute_verdict(case, [flag], {"eob_mismatch": answer})
    assert verdict.disputed_cents == 1800
    assert verdict.owed_cents == 3200
    assert verdict.case_action == "DISPUTE"
    assert "EOB_BILL_MISMATCH" in verdict.human_reasons


def test_no_confirmed_issue_uses_eob_responsibility():
    case = make_case(4100, 3900)
    verdict = compute_verdict(case, [], {})
    assert verdict.disputed_cents == 0
    assert verdict.owed_cents == 3900
    assert verdict.case_action == "OK"
