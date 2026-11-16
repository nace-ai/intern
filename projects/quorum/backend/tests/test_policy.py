"""Tests for backend/app/checks.py and backend/app/policy.py.

Covers tests 1, 6, 9, 10 from 04-checks-policy-guard.md plus guard rules.
"""

import pytest

from backend.app.checks import normalise_iban
from backend.app.contracts import Facts
from backend.app.policy import ORDER, choose, guard, risk_score


# ---------------------------------------------------------------------------
# normalise_iban (from checks.py)
# ---------------------------------------------------------------------------

def test_normalise_iban_1() -> None:
    """Test 1: remove spaces and dashes, upper-case."""
    assert normalise_iban("gb18 1790-8734 9062 1374 9599") == "GB1817908734906213749599"


def test_normalise_iban_none() -> None:
    assert normalise_iban(None) is None


def test_normalise_iban_empty() -> None:
    assert normalise_iban("") is None


def test_normalise_iban_whitespace_only() -> None:
    assert normalise_iban("   ") is None


def test_normalise_iban_no_change_on_clean() -> None:
    assert normalise_iban("GB1817908734906213749599") == "GB1817908734906213749599"


# ---------------------------------------------------------------------------
# ORDER
# ---------------------------------------------------------------------------

def test_order_values() -> None:
    assert ORDER["approve"] == 0
    assert ORDER["verify"] == 1
    assert ORDER["block"] == 2


# ---------------------------------------------------------------------------
# risk_score
# ---------------------------------------------------------------------------

def test_risk_score_approve_only() -> None:
    p = {"approve": 1.0, "verify": 0.0, "block": 0.0}
    assert risk_score(p) == 0.0


def test_risk_score_mixed() -> None:
    p = {"approve": 0.11, "verify": 0.62, "block": 0.27}
    assert risk_score(p) == pytest.approx(0.27 + 0.5 * 0.62)


def test_risk_score_block_only() -> None:
    p = {"approve": 0.0, "verify": 0.0, "block": 1.0}
    assert risk_score(p) == 1.0


# ---------------------------------------------------------------------------
# choose
# ---------------------------------------------------------------------------

def test_choose_approve_when_low_risk() -> None:
    """Low r = 0.04 should give approve at defaults."""
    # r=0.04: approve cost=4, verify cost=5, block cost=0.96*30=28.8
    p = {"approve": 0.96, "verify": 0.0, "block": 0.04}
    assert choose(p) == "approve"


def test_choose_verify_when_medium_risk() -> None:
    """r = 0.06 should give verify at defaults."""
    # r=0.06: approve cost=6, verify cost=5, block cost=0.94*30=28.2
    p = {"approve": 0.94, "verify": 0.0, "block": 0.06}
    assert choose(p) == "verify"


def test_choose_block_when_high_risk() -> None:
    """r = 0.84 should give block at defaults."""
    # r=0.84: approve cost=84, verify cost=5, block cost=0.16*30=4.8
    p = {"approve": 0.16, "verify": 0.0, "block": 0.84}
    assert choose(p) == "block"


def test_choose_sums_099() -> None:
    """Test 10: p sums to 0.99, does not crash."""
    p = {"approve": 0.5, "verify": 0.3, "block": 0.19}
    result = choose(p)
    assert result in ("approve", "verify", "block")


def test_choose_sums_101() -> None:
    """Test 10: p sums to 1.01, does not crash."""
    p = {"approve": 0.5, "verify": 0.3, "block": 0.21}
    result = choose(p)
    assert result in ("approve", "verify", "block")


def test_choose_tie_returns_stricter() -> None:
    """On a cost tie, return the stricter action."""
    # Construct a tie: e.g. set V=0 so verify cost=0, and make approve also 0
    # r=0 => approve cost=0, verify cost=0 => tie => block (strictest)
    p = {"approve": 1.0, "verify": 0.0, "block": 0.0}
    # With V=0: cost_approve=0, cost_verify=V=0, cost_block=D=30
    # Tie between approve and verify => verify is stricter
    assert choose(p, V=0) == "verify"


def test_choose_with_custom_params() -> None:
    """Changing L, V, D changes the decision."""
    p = {"approve": 0.5, "verify": 0.0, "block": 0.5}
    # r=0.5
    # defaults: approve=50, verify=5, block=15 => verify
    assert choose(p) == "verify"
    # Doubling L: approve=100, verify=5, block=15 => verify still
    assert choose(p, L=200) == "verify"
    # Lowering D: approve=50, verify=5, block=5 => tie (verify/block), block wins
    assert choose(p, D=10) == "block"
    # High L: approve=500, verify=5, block=7.5 => verify
    assert choose(p, L=1000) == "verify"


def test_choose_default_thresholds_exact() -> None:
    """Test 9 explicitly: r thresholds at defaults."""
    # r = 0.04 -> approve (cost: 4 < 5 verify, < 0.96*30=28.8 block)
    assert choose({"approve": 0.96, "verify": 0.0, "block": 0.04}) == "approve"
    # r = 0.06 -> verify (cost: 6 > 5 verify, but verify=5 < 0.94*30=28.2)
    assert choose({"approve": 0.94, "verify": 0.0, "block": 0.06}) == "verify"
    # r = 0.84 -> block (cost: 84 approve, 5 verify, 0.16*30=4.8 block)
    assert choose({"approve": 0.16, "verify": 0.0, "block": 0.84}) == "block"


# ---------------------------------------------------------------------------
# guard
# ---------------------------------------------------------------------------

def _clean_facts(**overrides: object) -> Facts:
    """Helper: Facts with clean/default values, overridden by kwargs."""
    defaults: dict[str, object] = {
        "supplier_known": True,
        "supplier_id": "cobalt-wholesale-ltd",
        "iban_status": "found",
        "iban_matches_trusted": True,
        "trusted_verification_for_this_iban": False,
        "duplicate_invoice_number": False,
        "amount_ratio_to_supplier_median": 1.13,
    }
    defaults.update(overrides)
    return Facts(**defaults)  # type: ignore[arg-type]


def test_guard_passes_clean_approve() -> None:
    """Clean invoice: guard leaves approve alone; findings empty."""
    facts = _clean_facts()
    new_action, findings = guard("approve", facts)
    assert new_action == "approve"
    assert findings == []


def test_guard_never_lowers() -> None:
    """Test 6: guard never lowers. Input block with clean facts returns block."""
    facts = _clean_facts()
    new_action, findings = guard("block", facts)
    assert new_action == "block"
    assert findings == []


def test_guard_iban_not_found_raises_to_verify() -> None:
    """iban_status not 'found' -> at least verify."""
    facts = _clean_facts(iban_status="not_found")
    new_action, findings = guard("approve", facts)
    assert new_action == "verify"
    assert "iban_unreadable" in findings


def test_guard_iban_ambiguous_raises_to_verify() -> None:
    """iban_status 'ambiguous' -> at least verify (test 7)."""
    facts = _clean_facts(iban_status="ambiguous")
    new_action, findings = guard("approve", facts)
    assert new_action == "verify"
    assert "iban_unreadable" in findings


def test_guard_supplier_unknown_raises_to_verify() -> None:
    """Supplier unknown -> at least verify."""
    facts = _clean_facts(supplier_known=False, supplier_id=None)
    new_action, findings = guard("approve", facts)
    assert new_action == "verify"
    assert "supplier_unknown" in findings


def test_guard_supplier_one_char_change() -> None:
    """Test 8: supplier unknown after one-char change -> at least verify."""
    facts = _clean_facts(supplier_known=False, supplier_id=None)
    new_action, findings = guard("approve", facts)
    assert new_action == "verify"
    assert "supplier_unknown" in findings


def test_guard_iban_mismatch_no_verification_raises_to_verify() -> None:
    """Test 3: changed IBAN, no verification -> verify with unverified_bank_change."""
    facts = _clean_facts(
        iban_matches_trusted=False,
        trusted_verification_for_this_iban=False,
    )
    new_action, findings = guard("approve", facts)
    assert new_action == "verify"
    assert "unverified_bank_change" in findings


def test_guard_iban_mismatch_with_verification_passes() -> None:
    """Test 4: changed IBAN with matching verification -> guard leaves approve alone."""
    facts = _clean_facts(
        iban_matches_trusted=False,
        trusted_verification_for_this_iban=True,
    )
    new_action, findings = guard("approve", facts)
    assert new_action == "approve"
    assert "unverified_bank_change" not in findings


def test_guard_duplicate_invoice_blocks() -> None:
    """Duplicate invoice number -> block."""
    facts = _clean_facts(duplicate_invoice_number=True)
    new_action, findings = guard("approve", facts)
    assert new_action == "block"
    assert "duplicate_paid" in findings


def test_guard_duplicate_input_verify_raises_to_block() -> None:
    """Duplicate with input verify -> still block."""
    facts = _clean_facts(duplicate_invoice_number=True)
    new_action, findings = guard("verify", facts)
    assert new_action == "block"
    assert "duplicate_paid" in findings


def test_guard_multiple_findings() -> None:
    """Multiple conditions trigger -> all findings listed, action is max."""
    facts = _clean_facts(
        supplier_known=False,
        supplier_id=None,
        iban_status="not_found",
        iban_matches_trusted=False,
        trusted_verification_for_this_iban=False,
        duplicate_invoice_number=True,
    )
    new_action, findings = guard("approve", facts)
    assert new_action == "block"
    assert "iban_unreadable" in findings
    assert "supplier_unknown" in findings
    assert "unverified_bank_change" in findings
    assert "duplicate_paid" in findings


def test_guard_accepts_dict() -> None:
    """guard accepts a plain dict with Facts fields."""
    d = {
        "supplier_known": True,
        "supplier_id": "test",
        "iban_status": "found",
        "iban_matches_trusted": True,
        "trusted_verification_for_this_iban": False,
        "duplicate_invoice_number": False,
        "amount_ratio_to_supplier_median": 1.0,
    }
    new_action, findings = guard("approve", d)
    assert new_action == "approve"
    assert findings == []


def test_guard_accepts_facts_object() -> None:
    """guard also accepts a Facts object."""
    facts = _clean_facts()
    new_action, findings = guard("approve", facts)
    assert new_action == "approve"
    assert findings == []


def test_guard_verify_input_without_findings_stays_verify() -> None:
    """Input verify with some findings that only need verify stays verify."""
    facts = _clean_facts(
        iban_matches_trusted=False,
        trusted_verification_for_this_iban=False,
    )
    new_action, findings = guard("verify", facts)
    assert new_action == "verify"
    assert "unverified_bank_change" in findings


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------

from decimal import Decimal  # noqa: E402

from backend.app.policy import resolve  # noqa: E402

_GUARD_FACTS: dict[str, Facts] = {
    "clean": _clean_facts(),
    "iban_unreadable": _clean_facts(iban_status="not_found"),
    "supplier_unknown": _clean_facts(supplier_known=False, supplier_id=None),
    "unverified_bank_change": _clean_facts(iban_matches_trusted=False),
    "duplicate_paid": _clean_facts(duplicate_invoice_number=True),
}


def _grid() -> list[dict[str, float]]:
    out = []
    for a in range(11):
        for v in range(11 - a):
            b = 10 - a - v
            out.append({k: float(Decimal(n) / 10) for k, n in
                        (("approve", a), ("verify", v), ("block", b))})
    return out


@pytest.mark.parametrize("name", list(_GUARD_FACTS))
def test_resolve_final_never_below_raw_grid(name: str) -> None:
    facts = _GUARD_FACTS[name]
    for p in _grid():
        res = resolve(p, facts)
        final = ORDER[res["final_action"]]
        assert final >= ORDER[res["raw_action"]], (p, res)
        assert final >= ORDER[res["policy_action"]], (p, res)
        assert final >= ORDER[guard(res["policy_action"], facts)[0]], (p, res)
        assert final >= ORDER[guard(res["raw_action"], facts)[0]], (p, res)
        if name != "clean":
            assert name in res["findings"]
        if res["final_action"] == res["raw_action"]:
            assert res["raised_by"] == []


def test_resolve_block_majority_stays_block() -> None:
    p = {"approve": 0.05, "verify": 0.40, "block": 0.55}
    assert choose(p) == "verify"  # the policy alone would lower it
    res = resolve(p, _clean_facts())
    assert res["raw_action"] == "block"
    assert res["policy_action"] == "verify"
    assert res["final_action"] == "block"
    assert res["raised_by"] == []


def test_resolve_policy_raises() -> None:
    res = resolve({"approve": 0.94, "verify": 0.0, "block": 0.06}, _clean_facts())
    assert res["raw_action"] == "approve" and res["final_action"] == "verify"
    assert res["raised_by"] == ["policy"]


def test_resolve_guard_raises() -> None:
    res = resolve({"approve": 0.99, "verify": 0.01, "block": 0.0},
                  _clean_facts(duplicate_invoice_number=True))
    assert res["final_action"] == "block"
    assert res["raised_by"] == ["guard"]
    assert res["findings"] == ["duplicate_paid"]


def test_resolve_raw_tie_goes_higher() -> None:
    res = resolve({"approve": 0.5, "verify": 0.0, "block": 0.5}, _clean_facts())
    assert res["raw_action"] == "block" and res["final_action"] == "block"


def test_resolve_unknown_action_raises() -> None:
    with pytest.raises(ValueError):
        resolve({"approve": 0.5, "pay": 0.5}, _clean_facts())
