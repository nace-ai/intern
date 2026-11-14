"""Hand-checked cents and a few rule boundaries. One file, money and leakage only."""
from billproof.rules.money import pct_of
from billproof.synth.hard.generate import build_case
from billproof.synth.hard.knobs import Knobs
from billproof.synth.hard.oracle import answers, correct_pr
from billproof.synth.hard.questions import questions_for, visible_state
from billproof.synth.hard.rulespec import RULES, WRITEOFF, LIABLE


def _record(lines, eobs, plan, balance=0, reference=None):
    codes = sorted({row["code"] for row in lines})
    return {
        "lines": lines,
        "eobs": eobs,
        "plan": plan,
        "bill": {"patient_balance_cents": balance, "account_no": "T", "statement_date": "2026-11-02",
                 "total_charges_cents": sum(r["charge_cents"] for r in lines)},
        "reference": reference or {"mue": {}, "fees": {}, "ncci": []},
    }


def _plan(**overrides):
    base = {
        "deductible_remaining_cents": 0, "oop_remaining_cents": 500000, "coinsurance_pct": 20,
        "copay_cents": 0, "in_network": True, "nsa_protected": True, "secondary": None,
    }
    base.update(overrides)
    return base


def _line(n, code="11111", **overrides):
    row = {"line_no": n, "dos": "2026-10-01", "code": code, "modifiers": [], "units": 1,
           "charge_cents": 15000, "pos": "11", "rendering_provider": "SYN-RP-1"}
    row.update(overrides)
    return row


def _eob(n, **overrides):
    row = {"line_no": n, "code": "11111", "units": 1, "billed_cents": 15000, "allowed_cents": 10000,
           "plan_paid_cents": 0, "patient_resp_cents": 0, "carc": ["CO-45"], "remark": ""}
    row.update(overrides)
    return row


def test_half_even_examples_in_the_rules():
    assert pct_of(1, 50) == 0
    assert pct_of(3, 50) == 2
    assert "1 cent at 50% is 0" in RULES
    for code in WRITEOFF | LIABLE:
        assert code in RULES


def test_deductible_depletes_in_line_order():
    # Line 1 allowed 10000, ded 6000, coins 20% → take 6000, coins 800, owe 6800.
    # Line 2 allowed 10000, ded now 0 → coins 2000, owe 2000.
    lines = [_line(1), _line(2, code="22222")]
    eobs = [_eob(1), _eob(2, code="22222")]
    record = _record(lines, eobs, _plan(deductible_remaining_cents=6000, coinsurance_pct=20))
    got = correct_pr(record)
    assert got[1] == 6800
    assert got[2] == 2000


def test_copay_skips_deductible_on_the_first_992_line():
    lines = [_line(1, code="99213", charge_cents=20000), _line(2, code="36415")]
    eobs = [_eob(1, code="99213", allowed_cents=10000, billed_cents=20000),
            _eob(2, code="36415")]
    record = _record(lines, eobs, _plan(copay_cents=3000, deductible_remaining_cents=6000))
    got = correct_pr(record)
    assert got[1] == 3000
    # Deductible was not used on the copay line, so line 2 still takes 6000 + 20% of 4000.
    assert got[2] == 6800


def test_secondary_and_balance_bill():
    lines = [_line(1, charge_cents=15000)]
    eobs = [_eob(1, allowed_cents=10000, billed_cents=15000)]
    plan = _plan(deductible_remaining_cents=0, coinsurance_pct=20, in_network=False, nsa_protected=False,
                 secondary={"deductible_remaining_cents": 1000, "coinsurance_pct": 20})
    got = correct_pr(_record(lines, eobs, plan))
    # primary = 2000; sec_take 1000; patient_secondary = 20% of 1000 = 200; cost-share 1200; balance 5000.
    assert got[1] == 6200
    protected = _plan(deductible_remaining_cents=0, coinsurance_pct=20, in_network=False, nsa_protected=True,
                      secondary=None)
    assert correct_pr(_record(lines, eobs, protected))[1] == 2000


def test_writeoff_owes_zero_and_fee_boundary():
    lines = [_line(1, charge_cents=100)]
    eobs = [_eob(1, carc=["CO-97"], allowed_cents=0, patient_resp_cents=50)]
    record = _record(lines, eobs, _plan(), reference={"mue": {}, "fees": {"11111": {"non_facility_cents": 100, "facility_cents": 80}}, "ncci": []})
    assert correct_pr(record)[1] == 0
    # threshold = 100 * 1 * 150 // 100 = 150. Charge 100 is not over. Charge 151 is over.
    lines[0]["charge_cents"] = 150
    assert answers(record)["fee_L1"] == "no"
    lines[0]["charge_cents"] = 151
    assert answers(record)["fee_L1"] == "yes"
    lines[0]["pos"] = "22"
    # Facility fee 80 → threshold 120. Charge 151 is still over.
    assert answers(record)["fee_L1"] == "yes"


def test_dup_mue_ncci_boundaries():
    lines = [
        _line(1, code="AAAA"),
        _line(2, code="AAAA", modifiers=["59"]),
        _line(3, code="AAAA", modifiers=["76"]),
        _line(4, code="BBBB", modifiers=["59"]),
    ]
    eobs = [_eob(1, code="AAAA"), _eob(2, code="AAAA"), _eob(3, code="AAAA", remark="Distinct service documented"),
            _eob(4, code="BBBB", remark="Distinct service documented")]
    reference = {
        "mue": {"AAAA": {"limit": 1, "mai": 2}, "BBBB": {"limit": 2, "mai": 3}},
        "fees": {},
        "ncci": [{"col1": "AAAA", "col2": "BBBB", "modifier_indicator": 1}],
    }
    record = _record(lines, eobs, _plan(), reference=reference)
    gold = answers(record)
    assert gold["dup_L2"] == "yes"  # 59 does not save a duplicate
    assert gold["dup_L3"] == "no"   # 76 does
    assert gold["mue_L1"] == "yes"  # three units of AAAA, cap 1, MAI 2
    assert gold["mue_L3"] == "yes"
    assert gold["ncci_L4"] == "no"  # indicator 1, modifier 59, distinct phrase only
    eobs[3]["remark"] = "Distinct service documented. Same session."
    assert answers(record)["ncci_L4"] == "yes"
    reference["ncci"][0]["modifier_indicator"] = 0
    eobs[3]["remark"] = "Distinct service documented"
    assert answers(record)["ncci_L4"] == "yes"


def test_gpt_prompt_does_not_redefine_yes():
    from billproof.bench.hard.policy import GPT_SYSTEM
    assert "supported error" not in GPT_SYSTEM
    assert "Each question's own text defines what yes and no mean." in GPT_SYSTEM


def test_generated_case_is_self_contained_and_hides_gold():
    case = build_case("dev", 0, Knobs())
    assert case["n_lines"] >= 30
    assert case["n_questions"] >= 100
    assert case["gold"] == answers(case)
    state = visible_state(case)
    blob = str(state)
    assert "gold" not in state and "family_counts" not in blob
    assert "patient_balance_cents" in blob
    qs = questions_for(case)
    assert len(qs) == case["n_questions"]
    assert qs[0].options() == ["yes", "no"]
    # Reference rows match the codes on the bill and do not name a fired line.
    codes = {row["code"] for row in case["lines"]}
    assert set(state["reference"]["fees"]) == codes
    assert "fired" not in blob
    assert "plan.deductible_remaining_cents" in state["rules"]
    # Different provider on the unsupported-duplicate anchor is not part of that duplicate.
    by_code = {}
    for row in case["lines"]:
        by_code.setdefault((row["code"], row["dos"]), []).append(row)
    foreign = [row for row in case["lines"] if row["rendering_provider"] == "SYN-RP-2"]
    assert len(foreign) == 1
    peers = by_code[(foreign[0]["code"], foreign[0]["dos"])]
    assert any(row["rendering_provider"] == "SYN-RP-1" for row in peers)
    assert case["gold"][f"dup_L{foreign[0]['line_no']}"] == "no"
