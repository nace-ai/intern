"""OK-versus-dispute outcomes. The rule is the six answers. No threshold."""
from billproof.bench.hard.outcome_score import bill_verdict, line_verdict, score_lane
from billproof.synth.hard.oracle import answers


def _plan():
    return {
        "deductible_remaining_cents": 0, "oop_remaining_cents": 500000, "coinsurance_pct": 0,
        "copay_cents": 2000, "in_network": True, "nsa_protected": True, "secondary": None,
    }


def _line(n, code, charge):
    return {
        "line_no": n, "dos": "2026-10-01", "code": code, "modifiers": [], "units": 1,
        "charge_cents": charge, "pos": "11", "rendering_provider": "SYN-RP-1",
    }


def _eob(n, code, charge, patient):
    return {
        "line_no": n, "code": code, "units": 1, "billed_cents": charge, "allowed_cents": charge,
        "plan_paid_cents": 0, "patient_resp_cents": patient, "carc": ["CO-45"], "remark": "",
    }


def _record():
    lines = [_line(1, "11111", 10000), _line(2, "99213", 8000)]
    eobs = [_eob(1, "11111", 10000, 500), _eob(2, "99213", 8000, 2000)]
    record = {
        "case_id": "HARD-TEST-0099",
        "lines": lines, "eobs": eobs, "plan": _plan(),
        "bill": {
            "patient_balance_cents": 2000, "account_no": "T", "statement_date": "2026-11-02",
            "total_charges_cents": 18000,
        },
        "reference": {"mue": {}, "fees": {}, "ncci": []},
        "eob_header": {},
    }
    record["gold"] = answers(record)
    return record


def _row(record, overrides):
    gold = record["gold"]
    answers_out = {}
    for name, choice in gold.items():
        picked = overrides.get(name, choice)
        answers_out[name] = {"choice": picked, "invalid": picked not in ("yes", "no")}
    return {"case_id": record["case_id"], "answers": answers_out, "simulated": False}


def test_line_rule_has_no_threshold():
    ok = {family: "no" for family in ("dup", "mue", "ncci", "fee", "eob")}
    ok["pr"] = "yes"
    assert line_verdict(ok) == "ok"
    for family in ("dup", "mue", "ncci", "fee", "eob"):
        flagged = dict(ok)
        flagged[family] = "yes"
        assert line_verdict(flagged) == "dispute"
    wrong_pr = dict(ok)
    wrong_pr["pr"] = "no"
    assert line_verdict(wrong_pr) == "dispute"
    missing = dict(ok)
    missing["fee"] = None
    assert line_verdict(missing) is None
    assert bill_verdict(["ok", "ok"]) == "ok"
    assert bill_verdict(["dispute", None]) == "dispute"
    assert bill_verdict(["ok", None]) is None


def test_dollars_follow_the_line_verdict():
    record = _record()
    assert record["gold"]["pr_L1"] == "no"
    assert record["gold"]["pr_L2"] == "yes"
    assert record["gold"]["fee_L2"] == "no"
    # Line 2 is gold OK. Saying fee yes disputes it even though pr stays yes.
    scored = score_lane([_row(record, {"fee_L2": "yes"})], {record["case_id"]: record})
    assert scored["line_k"] == 1 and scored["line_n"] == 2
    assert scored["all_k"] == 0 and scored["all_n"] == 1
    assert scored["caught_cents"] == 500
    assert scored["gold_disputed_cents"] == 500
    assert scored["false_alarm_cents"] == 2000
    assert scored["bill_k"] == 1 and scored["bill_n"] == 1
    assert scored["strict_k"] == 0 and scored["strict_n"] == 1


def test_undefined_line_is_a_miss_and_catches_no_dollars():
    record = _record()
    scored = score_lane([_row(record, {"pr_L1": None})], {record["case_id"]: record})
    assert scored["undefined_lines"] == 1
    assert scored["line_k"] == 1 and scored["line_n"] == 2
    assert scored["caught_cents"] == 0
    assert scored["gold_disputed_cents"] == 500
    assert scored["false_alarm_cents"] == 0
    assert scored["bill_k"] == 0 and scored["undefined_bills"] == 1


def test_bill_verdict_ignores_a_wrong_total_when_every_line_is_ok():
    record = _record()
    # Clear the overcharge so both lines are gold OK, then flip only the total.
    record["eobs"][0]["patient_resp_cents"] = 0
    record["gold"] = answers(record)
    assert record["gold"]["pr_L1"] == "yes"
    assert record["gold"]["bill_balance"] == "yes"
    scored = score_lane([_row(record, {"bill_balance": "no"})], {record["case_id"]: record})
    assert scored["line_k"] == 2
    assert scored["all_k"] == 1
    assert scored["bill_k"] == 1
    assert scored["strict_k"] == 0
    assert scored["caught_cents"] == 0
    assert scored["false_alarm_cents"] == 0
