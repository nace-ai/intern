"""Checks: compare ParsedInvoice fields to trusted supplier records.

Pure functions. No network calls.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from backend.app import config
from backend.app.contracts import Facts, ParsedInvoice, TrustedSupplier


def normalise_iban(s: str | None) -> str | None:
    """Remove every character that is not a letter or digit, then upper-case.

    Return None for empty input. No look-alike correction.
    """
    if s is None:
        return None
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", s)
    if not cleaned:
        return None
    return cleaned.upper()


def load_trusted(path: str | Path | None = None) -> dict[str, TrustedSupplier]:
    """Load trusted supplier records from a JSON file (default config.TRUSTED_FILE).

    Raises FileNotFoundError if the file is missing, and ValueError if the JSON
    is not an object keyed by supplier id with an object per supplier.
    """
    resolved = Path(path) if path is not None else config.TRUSTED_FILE
    if not resolved.is_file():
        raise FileNotFoundError(f"trusted supplier file not found: {resolved}")
    try:
        raw = json.loads(resolved.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"trusted supplier file is not valid JSON: {resolved}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"trusted supplier file must be a JSON object keyed by supplier id: {resolved}")
    bad = [k for k, v in raw.items() if not k or not isinstance(v, dict)]
    if bad:
        raise ValueError(f"trusted supplier entries must be objects keyed by supplier id: {bad[:5]}")
    return {k: TrustedSupplier(**v) for k, v in raw.items()}


def _normalise_name(name: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace."""
    # Remove everything that is not a letter, digit or whitespace
    cleaned = re.sub(r"[^a-z0-9\s]", "", name.lower())
    # Collapse multiple spaces into one and strip
    return re.sub(r"\s+", " ", cleaned).strip()


def facts(parsed: ParsedInvoice, trusted: dict[str, TrustedSupplier]) -> Facts:
    """Produce Facts from a ParsedInvoice and trusted supplier records."""

    fields = parsed.fields

    supplier_name_field = fields.get("supplier_name")
    supplier_name = supplier_name_field.value if supplier_name_field else None
    supplier_name = supplier_name if isinstance(supplier_name, str) else None

    invoice_number_field = fields.get("invoice_number")
    invoice_number = invoice_number_field.value if invoice_number_field else None
    invoice_number = invoice_number if isinstance(invoice_number, str) else None

    iban_field = fields.get("iban")
    iban_value = iban_field.value if iban_field else None
    iban_value = iban_value if isinstance(iban_value, str) else None
    iban_status = iban_field.status if iban_field else "not_found"

    amount_field = fields.get("amount_minor")
    amount_minor = amount_field.value if amount_field else None
    amount_minor = amount_minor if isinstance(amount_minor, int) else None

    # Match supplier name against trusted records
    norm_name = _normalise_name(supplier_name) if supplier_name else ""
    matched_supplier_id: str | None = None
    matched_supplier: TrustedSupplier | None = None

    if norm_name:
        for sid, ts in trusted.items():
            for tname in ts.names:
                if _normalise_name(tname) == norm_name:
                    matched_supplier_id = sid
                    matched_supplier = ts
                    break
            if matched_supplier_id:
                break

    supplier_known = matched_supplier_id is not None

    # IBAN match
    norm_invoice_iban = normalise_iban(iban_value)
    iban_matches_trusted = False
    trusted_verification_for_this_iban = False

    if supplier_known and matched_supplier is not None:
        if iban_status == "found" and norm_invoice_iban == matched_supplier.iban:
            iban_matches_trusted = True

        # Check verifications
        if norm_invoice_iban:
            for v in matched_supplier.verifications:
                if normalise_iban(v.iban) == norm_invoice_iban:
                    trusted_verification_for_this_iban = True
                    break

    # Duplicate invoice number
    duplicate_invoice_number = False
    if supplier_known and matched_supplier is not None and invoice_number:
        duplicate_invoice_number = invoice_number in matched_supplier.paid_invoice_numbers

    # Amount ratio
    amount_ratio_to_supplier_median: float | None = None
    if (
        supplier_known
        and matched_supplier is not None
        and amount_minor is not None
        and matched_supplier.median_amount_minor is not None
        and matched_supplier.median_amount_minor > 0
    ):
        amount_ratio_to_supplier_median = round(
            amount_minor / matched_supplier.median_amount_minor, 2
        )

    return Facts(
        supplier_known=supplier_known,
        supplier_id=matched_supplier_id,
        iban_status=iban_status,
        iban_matches_trusted=iban_matches_trusted,
        trusted_verification_for_this_iban=trusted_verification_for_this_iban,
        duplicate_invoice_number=duplicate_invoice_number,
        amount_ratio_to_supplier_median=amount_ratio_to_supplier_median,
    )
