"""Line verdict and patient-overcharge dollars from recorded answers."""
from billproof.bench.hard.list_price import list_cost, row_list_cost
from billproof.bench.hard.outcomes import score_lane
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
            "patient_balance_cents": 0, "account_no": "T", "statement_date": "2026-11-02",
            "total_charges_cents": 18000,
        },
        "reference": {"mue": {}, "fees": {}, "ncci": []},
        "eob_header": {},
    }
    record["gold"] = answers(record)
    record["bill"]["patient_balance_cents"] = 2000
    record["gold"] = answers(record)
    return record


def _row(record, pr1, pr2):
    gold = record["gold"]
    correct = {}
    answers_out = {}
    for name, choice in gold.items():
        picked = choice
        if name == "pr_L1":
            picked = pr1
        elif name == "pr_L2":
            picked = pr2
        answers_out[name] = {"choice": picked, "invalid": False}
        correct[name] = picked == choice
    return {
        "case_id": record["case_id"], "gold": gold, "answers": answers_out,
        "correct": correct, "invalid": {name: False for name in gold},
        "case_correct": all(correct.values()), "simulated": False,
    }


def test_overcharge_caught_and_wrongly_disputed():
    record = _record()
    assert record["gold"]["pr_L1"] == "no"
    assert record["gold"]["pr_L2"] == "yes"
    assert record["gold"]["bill_balance"] == "yes"
    scored = score_lane([_row(record, "no", "no")], {record["case_id"]: record})
    assert scored["caught_cents"] == 500
    assert scored["wrong_cents"] == 2000
    assert scored["gold_overcharge_cents"] == 500
    assert scored["line_k"] == 1
    assert scored["line_n"] == 2
    assert scored["bill_k"] == 1 and scored["bill_n"] == 1
    assert scored["strict_k"] == 0 and scored["strict_n"] == 1


def test_list_price_is_two_and_ten():
    assert list_cost(1_000_000, 1_000_000) == 12.0
    assert list_cost(20_000, 8_000) == 0.12
    assert row_list_cost({"input_tokens": 500, "output_tokens": 0}) == 0.001
    assert row_list_cost({"cost_usd": 1}) is None
