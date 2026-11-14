"""CMS reference-table lookups backed by data/cms/catalog.json (W1).

Signatures are binding (docs/CONTRACT.md §3). Values are integer cents.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from billproof.config import CMS_DIR

_CATALOG = CMS_DIR / "catalog.json"


@lru_cache(maxsize=1)
def _catalog() -> dict[str, Any]:
    return json.loads(_CATALOG.read_text())


@lru_cache(maxsize=1)
def _ptp_index() -> dict[tuple[str, str], int]:
    return {
        (p["col1"], p["col2"]): int(p["modifier_indicator"])
        for p in _catalog().get("ptp", [])
    }


def _norm(code: str) -> str:
    return code.strip().upper()


def code_info(code: str) -> dict | None:
    """The catalog entry for a CPT/HCPCS code, or None."""
    return _catalog()["codes"].get(_norm(code))


def mue_limit(code: str) -> tuple[int, int] | None:
    """(mue, mai) max units/day + adjudication indicator 1|2|3, or None if unpublished."""
    info = code_info(code)
    if info is None or info.get("mue") is None:
        return None
    return int(info["mue"]), int(info["mai"])


def ptp_edit(col1: str, col2: str) -> int | None:
    """PTP modifier indicator (0 no modifier allowed, 1 allowed, 9 n/a) for the
    ordered (col1, col2) edit pair, or None if not an edit pair."""
    return _ptp_index().get((_norm(col1), _norm(col2)))


def fee_cents(code: str, facility: bool = False) -> int | None:
    """National PFS amount in cents; non-facility by default, facility when facility=True."""
    info = code_info(code)
    if info is None:
        return None
    key = "fee_facility_cents" if facility else "fee_cents"
    fee = info.get(key)
    return int(fee) if fee is not None else None


def all_codes() -> list[str]:
    return sorted(_catalog()["codes"])
