"""Tests for backend/app/checks.py.

policy.choose and policy.guard rules are covered in test_policy.py; here guard
is only used to check how checks.facts() output flows into it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.checks import facts, load_trusted, normalise_iban
from backend.app.contracts import (
    Facts,
    Field,
    ParsedInvoice,
    TrustedSupplier,
    Verification,
)
from backend.app.policy import ORDER, guard

TRUSTED_IBAN = "GB18179087349062137495"  # 22-char GB IBAN, contains 0s
FORGED_IBAN = "GB99000011112222333344"
OTHER_IBAN = "GB55666677778888999900"
SUPPLIER_ID = "cobalt-wholesale-ltd"


def _supplier(**overrides: object) -> TrustedSupplier:
    data: dict[str, object] = {
        "supplier_id": SUPPLIER_ID,
        "names": ["Cobalt Wholesale Ltd"],
        "iban": TRUSTED_IBAN,
        "bic": "COBAGB2L",
        "median_amount_minor": 100_000,
        "paid_invoice_numbers": ["INV-001", "INV-002"],
        "verifications": [],
    }
    data.update(overrides)
    return TrustedSupplier(**data)  # type: ignore[arg-type]


def _trusted(**overrides: object) -> dict[str, TrustedSupplier]:
    return {SUPPLIER_ID: _supplier(**overrides)}


def _invoice(
    supplier_name: str | None = "Cobalt Wholesale Ltd",
    iban: str | None = TRUSTED_IBAN,
    iban_status: str = "found",
    invoice_number: str | None = "INV-100",
    amount_minor: int | None = 113_000,
) -> ParsedInvoice:
    fields: dict[str, Field] = {}
    if supplier_name is not None:
        fields["supplier_name"] = Field(value=supplier_name, status="found")
    if iban is not None or iban_status != "not_found":
        fields["iban"] = Field(value=iban, status=iban_status)
    if invoice_number is not None:
        fields["invoice_number"] = Field(value=invoice_number, status="found")
    if amount_minor is not None:
        fields["amount_minor"] = Field(value=amount_minor, status="found")
    return ParsedInvoice(doc_id="t-1", fields=fields)


# ---------------------------------------------------------------------------
# normalise_iban
# ---------------------------------------------------------------------------


def test_normalise_iban_strips_spaces_hyphens_and_uppercases() -> None:
    assert normalise_iban(" gb18 1790-8734\t9062-1374 95 ") == "GB18179087349062137495"


def test_normalise_iban_none_and_empty() -> None:
    assert normalise_iban(None) is None
    assert normalise_iban("") is None
    assert normalise_iban(" - ") is None


def test_normalise_iban_does_not_correct_lookalikes() -> None:
    swapped = TRUSTED_IBAN.replace("0", "O")
    assert swapped != TRUSTED_IBAN
    assert normalise_iban(swapped) == swapped
    assert normalise_iban(swapped) != normalise_iban(TRUSTED_IBAN)


def test_lookalike_swapped_iban_mismatches_trusted() -> None:
    swapped = TRUSTED_IBAN.replace("0", "O")
    f = facts(_invoice(iban=swapped), _trusted())
    assert f.supplier_known is True
    assert f.iban_matches_trusted is False


# ---------------------------------------------------------------------------
# supplier matching
# ---------------------------------------------------------------------------


def test_supplier_exact_name_after_normalising_is_known() -> None:
    f = facts(_invoice(supplier_name="  COBALT   wholesale, ltd. "), _trusted())
    assert f.supplier_known is True
    assert f.supplier_id == SUPPLIER_ID


def test_supplier_one_changed_character_is_unknown() -> None:
    f = facts(_invoice(supplier_name="Cobalt Wholesa1e Ltd"), _trusted())
    assert f.supplier_known is False
    assert f.supplier_id is None
    assert f.iban_matches_trusted is False


@pytest.mark.parametrize("name", ["", "   ", None])
def test_supplier_empty_or_missing_is_unknown(name: str | None) -> None:
    f = facts(_invoice(supplier_name=name), _trusted())
    assert f.supplier_known is False
    assert f.supplier_id is None


# ---------------------------------------------------------------------------
# iban_matches_trusted
# ---------------------------------------------------------------------------


def test_iban_matches_when_known_found_and_equal() -> None:
    f = facts(_invoice(iban="gb18 1790 8734 9062 1374 95"), _trusted())
    assert f.iban_status == "found"
    assert f.iban_matches_trusted is True


def test_iban_ambiguous_does_not_match_even_if_text_equal() -> None:
    f = facts(_invoice(iban=TRUSTED_IBAN, iban_status="ambiguous"), _trusted())
    assert f.iban_status == "ambiguous"
    assert f.iban_matches_trusted is False


def test_iban_missing_field_is_not_found_and_no_match() -> None:
    inv = _invoice(iban=None, iban_status="not_found")
    assert "iban" not in inv.fields
    f = facts(inv, _trusted())
    assert f.iban_status == "not_found"
    assert f.iban_matches_trusted is False


def test_iban_unknown_supplier_does_not_match() -> None:
    f = facts(_invoice(supplier_name="Kobalt Wholesale Ltd"), _trusted())
    assert f.iban_matches_trusted is False


def test_iban_different_does_not_match() -> None:
    f = facts(_invoice(iban=FORGED_IBAN), _trusted())
    assert f.iban_matches_trusted is False


# ---------------------------------------------------------------------------
# trusted_verification_for_this_iban
# ---------------------------------------------------------------------------


def test_verification_counts_when_iban_equal_after_normalising() -> None:
    trusted = _trusted(
        verifications=[Verification(iban="gb99-0000 1111 2222 3333 44", method="call")]
    )
    f = facts(_invoice(iban=FORGED_IBAN), trusted)
    assert f.trusted_verification_for_this_iban is True
    assert f.iban_matches_trusted is False


def test_verification_for_different_iban_does_not_count() -> None:
    trusted = _trusted(verifications=[Verification(iban=OTHER_IBAN, method="call")])
    f = facts(_invoice(iban=FORGED_IBAN), trusted)
    assert f.trusted_verification_for_this_iban is False


def test_verification_unknown_supplier_gives_false() -> None:
    trusted = _trusted(verifications=[Verification(iban=FORGED_IBAN, method="call")])
    f = facts(_invoice(supplier_name="Cobalt Wholesale Ltd2", iban=FORGED_IBAN), trusted)
    assert f.supplier_known is False
    assert f.trusted_verification_for_this_iban is False


# ---------------------------------------------------------------------------
# duplicate_invoice_number
# ---------------------------------------------------------------------------


def test_duplicate_invoice_number_true_when_paid() -> None:
    assert facts(_invoice(invoice_number="INV-002"), _trusted()).duplicate_invoice_number is True


def test_duplicate_invoice_number_false_when_new_or_missing() -> None:
    assert facts(_invoice(invoice_number="INV-003"), _trusted()).duplicate_invoice_number is False
    assert facts(_invoice(invoice_number=None), _trusted()).duplicate_invoice_number is False


def test_duplicate_invoice_number_false_for_unknown_supplier() -> None:
    f = facts(_invoice(supplier_name="Other Ltd", invoice_number="INV-002"), _trusted())
    assert f.duplicate_invoice_number is False


# ---------------------------------------------------------------------------
# amount_ratio_to_supplier_median
# ---------------------------------------------------------------------------


def test_amount_ratio_rounded_to_two_places() -> None:
    f = facts(_invoice(amount_minor=123_456), _trusted(median_amount_minor=100_000))
    assert f.amount_ratio_to_supplier_median == 1.23
    f2 = facts(_invoice(amount_minor=2), _trusted(median_amount_minor=3))
    assert f2.amount_ratio_to_supplier_median == 0.67


def test_amount_ratio_none_when_amount_missing() -> None:
    f = facts(_invoice(amount_minor=None), _trusted())
    assert f.amount_ratio_to_supplier_median is None


def test_amount_ratio_none_when_median_missing_or_zero() -> None:
    f_none = facts(_invoice(), _trusted(median_amount_minor=None))
    assert f_none.amount_ratio_to_supplier_median is None
    f_zero = facts(_invoice(), _trusted(median_amount_minor=0))
    assert f_zero.amount_ratio_to_supplier_median is None


# ---------------------------------------------------------------------------
# load_trusted
# ---------------------------------------------------------------------------


def test_load_trusted_reads_dict_keyed_by_supplier_id(tmp_path: Path) -> None:
    path = tmp_path / "trusted.json"
    path.write_text(json.dumps({SUPPLIER_ID: _supplier().model_dump()}))
    loaded = load_trusted(str(path))
    assert list(loaded) == [SUPPLIER_ID]
    ts = loaded[SUPPLIER_ID]
    assert isinstance(ts, TrustedSupplier)
    assert ts.iban == TRUSTED_IBAN
    assert ts.median_amount_minor == 100_000
    assert ts.paid_invoice_numbers == ["INV-001", "INV-002"]


def test_load_trusted_missing_file_raises(tmp_path: Path) -> None:
    # A missing trusted.json must fail loudly rather than return {}, which
    # would silently make every supplier unknown.
    missing = tmp_path / "does-not-exist.json"
    with pytest.raises(FileNotFoundError, match="does-not-exist.json"):
        load_trusted(str(missing))


# ---------------------------------------------------------------------------
# facts() scenario table for the five benchmark scenarios, then guard
# ---------------------------------------------------------------------------

_S4_TRUSTED = _trusted(
    verifications=[Verification(iban=FORGED_IBAN, method="callback", verified_on="2026-10-01")]
)

SCENARIOS = [
    # id, invoice, trusted, expected iban_matches_trusted, expected verification
    ("S1", _invoice(), _trusted(), True, False),
    ("S2", _invoice(iban=FORGED_IBAN), _trusted(), False, False),
    ("S3", _invoice(iban=FORGED_IBAN), _trusted(), False, False),
    ("S4", _invoice(iban=FORGED_IBAN), _S4_TRUSTED, False, True),
    ("S5", _invoice(iban=FORGED_IBAN), _trusted(), False, False),
]


@pytest.mark.parametrize(
    "sid,invoice,trusted,match,verified", SCENARIOS, ids=[s[0] for s in SCENARIOS]
)
def test_facts_scenario_table(
    sid: str,
    invoice: ParsedInvoice,
    trusted: dict[str, TrustedSupplier],
    match: bool,
    verified: bool,
) -> None:
    f = facts(invoice, trusted)
    assert f.supplier_known is True
    assert f.supplier_id == SUPPLIER_ID
    assert f.iban_status == "found"
    assert f.iban_matches_trusted is match
    assert f.trusted_verification_for_this_iban is verified
    assert f.duplicate_invoice_number is False

    action, _ = guard("approve", f)
    if sid in ("S1", "S4"):
        assert action == "approve"
    else:
        assert ORDER[action] >= ORDER["verify"]


def _clean_facts() -> Facts:
    return facts(_invoice(), _trusted())


def test_guard_unknown_action_is_not_approve() -> None:
    with pytest.raises(ValueError):
        guard(None, _clean_facts())  # type: ignore[arg-type]
