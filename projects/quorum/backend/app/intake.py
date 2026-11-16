"""NDI intake: PDF -> ParsedInvoice, with a content-hash cache.

A cached file in backend/data/ndi_cache/{sha256}.json makes no API call.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from backend.app import config
from backend.app.contracts import Field, FieldCitation, ParsedInvoice

TEXT_FIELDS: tuple[str, ...] = (
    "supplier_name",
    "invoice_number",
    "currency",
    "iban",
    "bic",
    "bank_name",
    "customer_name",
    "notes",
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "supplier_name": {"type": "string", "description": "Name of the company issuing the invoice (the seller)."},
        "invoice_number": {"type": "string", "description": "Invoice number exactly as printed."},
        "total_amount": {"type": "string", "description": "Total amount due, exactly as printed, without the currency symbol if separate."},
        "currency": {"type": "string", "description": "Currency of the total as an ISO 4217 code, e.g. GBP, EUR, USD."},
        "iban": {"type": "string", "description": "IBAN of the payee bank account, exactly as printed."},
        "bic": {"type": "string", "description": "BIC / SWIFT code of the payee bank, as printed."},
        "bank_name": {"type": "string", "description": "Name of the payee bank."},
        "customer_name": {"type": "string", "description": "Name of the customer being billed (the buyer)."},
        "notes": {
            "type": "string",
            "description": (
                "All free text, remarks, payment instructions and messages outside the "
                "standard invoice fields, including small or faint text. Copy verbatim."
            ),
        },
    },
}

INSTRUCTIONS: str = (
    "Extract the invoice fields exactly as printed. Do not correct or normalise values. "
    "Leave a field empty if it is not on the document."
)

ZERO_DECIMAL: frozenset[str] = frozenset({"JPY", "KRW", "VND", "CLP", "ISK", "HUF"})


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def amount_to_minor(text: str | None, currency: str | None = None) -> int | None:
    """Convert a printed amount ("£1,712.95", "1.712,95") to integer minor units via Decimal."""
    if not text:
        return None
    s = re.sub(r"[^0-9.,\-]", "", str(text))
    if not re.search(r"\d", s):
        return None
    last_dot, last_comma = s.rfind("."), s.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        dec_sep = "." if last_dot > last_comma else ","
    elif last_comma >= 0:
        # "1,712" thousands vs "12,50" decimal: decimal when 1-2 digits follow the last comma
        dec_sep = "," if len(s) - last_comma - 1 in (1, 2) else ""
    elif last_dot >= 0:
        dec_sep = "." if len(s) - last_dot - 1 != 3 or s.count(".") == 1 else ""
    else:
        dec_sep = ""
    if dec_sep:
        whole, _, frac = s.rpartition(dec_sep)
        whole = re.sub(r"[.,]", "", whole)
        s = f"{whole}.{frac}"
    else:
        s = re.sub(r"[.,]", "", s)
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    exp = 0 if (currency or "").upper() in ZERO_DECIMAL else 2
    return int((value * (Decimal(10) ** exp)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _citations(raw: list[dict[str, Any]] | None) -> list[FieldCitation]:
    out: list[FieldCitation] = []
    for c in raw or []:
        loc = c.get("location") or {}
        page = loc.get("page", c.get("page", 1))
        out.append(
            FieldCitation(
                page=int(page or 1),
                polygons=loc.get("polygons") or c.get("polygons") or [],
                source_text=c.get("source_text") or "",
            )
        )
    return out


def _status(raw_status: str | None, value: Any) -> str:
    if raw_status in ("found", "not_found", "ambiguous"):
        if raw_status == "found" and (value is None or value == ""):
            return "not_found"
        return raw_status
    return "found" if value not in (None, "") else "not_found"


def map_result(result: dict[str, Any]) -> tuple[dict[str, Field], list[str]]:
    """Map a raw NDI extract result to ParsedInvoice fields. Pure function."""
    data: dict[str, Any] = result.get("data") or {}
    by_path: dict[str, dict[str, Any]] = {}
    for f in result.get("fields") or []:
        key = str(f.get("path", "")).lstrip("/")
        if key:
            by_path[key] = f
    warnings: list[str] = []

    def one(key: str) -> Field:
        meta = by_path.get(key, {})
        value = meta.get("value", data.get(key))
        if isinstance(value, str):
            value = value.strip()
        value = value if value not in ("",) else None
        return Field(
            value=value if value is None else str(value),
            status=_status(meta.get("status"), value),
            citations=_citations(meta.get("citations")),
        )

    fields = {k: one(k) for k in TEXT_FIELDS}
    total = one("total_amount")
    currency = fields["currency"].value
    minor = amount_to_minor(total.value if isinstance(total.value, str) else None,
                            currency if isinstance(currency, str) else None)
    if total.value is not None and minor is None:
        warnings.append("amount_unparseable")
    fields["amount_minor"] = Field(
        value=minor,
        status=total.status if minor is not None else ("ambiguous" if total.value else "not_found"),
        citations=total.citations,
    )
    if fields["notes"].value is None:
        fields["notes"] = Field(value="", status="not_found", citations=[])
    return fields, warnings


def build_parsed(path: Path, digest: str, raw: dict[str, Any], parse_ms: int) -> ParsedInvoice:
    fields, warnings = map_result(raw.get("result") or {})
    if raw.get("status") not in (None, "succeeded"):
        warnings.append(f"ndi_status_{raw.get('status')}")
    return ParsedInvoice(
        doc_id=path.stem,
        source_file=str(path),
        content_hash=f"sha256:{digest}",
        fields=fields,
        warnings=warnings,
        parse_ms=parse_ms,
        stub=False,
    )


class IntakeError(Exception):
    """An NDI job that did not succeed. Carries the job status and any error text."""

    def __init__(self, status: str | None, error: str | None = None) -> None:
        self.status = status
        self.error = error
        msg = f"NDI job status {status!r}"
        if error:
            msg += f": {error}"
        super().__init__(msg)


def _read_cache(cache_file: Path) -> dict[str, Any] | None:
    """Cached entry, or None if missing, unreadable or not from a succeeded job."""
    if not cache_file.exists():
        return None
    try:
        cached = json.loads(cache_file.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(cached, dict) or "parsed" not in cached:
        return None
    raw = cached.get("raw")
    if not isinstance(raw, dict) or raw.get("status") != "succeeded":
        return None
    return cached


async def parse_document(path: str | Path, cache_dir: Path | None = None) -> ParsedInvoice:
    """Parse a PDF with NDI; return the cached result if present.

    Raises IntakeError when the NDI job did not succeed. Only succeeded results
    are cached; a cache entry whose raw status is not succeeded is ignored
    (treated as a miss, the file is left in place).
    """
    p = Path(path)
    digest = sha256_file(p)
    cdir = cache_dir or config.NDI_CACHE
    cache_file = cdir / f"{digest}.json"
    cached = _read_cache(cache_file)
    if cached is not None:
        parsed = ParsedInvoice(**cached["parsed"])
        return parsed.model_copy(update={"doc_id": p.stem, "source_file": str(p)})

    from backend.app.nace import ndi_extract

    raw = await ndi_extract(p, SCHEMA, INSTRUCTIONS)
    status = raw.get("status")
    if status != "succeeded":
        err = raw.get("error")
        raise IntakeError(status, str(err) if err else None)
    parsed = build_parsed(p, digest, raw, int(raw.get("parse_ms", 0)))
    cdir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps({"parsed": parsed.model_dump(), "raw": raw}, default=str, indent=1)
    )
    return parsed
