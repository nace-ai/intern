"""Supplier payment history from past invoices, saved by scripts/build_history.py.

Reads the saved file only; makes no API call.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from backend.app import config
from backend.app.checks import normalise_iban

SUPPLIER_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")
MAX_QUOTES = 3


def history_path(supplier_id: str, data_dir: Path | None = None) -> Path | None:
    """Path of the saved history file, or None for an invalid supplier id."""
    if not SUPPLIER_ID.match(supplier_id or ""):
        return None
    return (data_dir or config.DATA) / "history" / f"{supplier_id}.json"


def load_history(supplier_id: str, data_dir: Path | None = None) -> dict | None:
    """The saved history dict, or None if absent, unreadable or the id is invalid."""
    path = history_path(supplier_id, data_dir)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _int_or_none(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _ibans(structured: Any) -> list[dict[str, Any]]:
    """Distinct normalised IBANs from the structured answer, in answer order."""
    rows = structured.get("ibans") if isinstance(structured, dict) else None
    out: dict[str, dict[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        iban = normalise_iban(str(row.get("iban") or ""))
        if not iban:
            continue
        seen = _int_or_none(row.get("documents_seen"))
        if iban in out:
            prev = out[iban]["documents_seen"]
            if seen is not None:
                out[iban]["documents_seen"] = (prev or 0) + seen
            continue
        out[iban] = {
            "iban": iban,
            "bic": row.get("bic"),
            "bank_name": row.get("bank_name"),
            "documents_seen": seen,
        }
    return list(out.values())


def _file_name(source_path: Any) -> str | None:
    if not isinstance(source_path, str) or not source_path:
        return None
    return source_path.replace("\\", "/").rsplit("/", 1)[-1]


def _quotes(evidences: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in evidences if isinstance(evidences, list) else []:
        if not isinstance(e, dict) or not e.get("quote"):
            continue
        out.append({"file": _file_name(e.get("source_path")), "page": e.get("page"), "quote": e["quote"]})
        if len(out) >= MAX_QUOTES:
            break
    return out


def history_summary(h: dict) -> dict:
    """Compact, path-free summary of a saved history."""
    ibans = _ibans(h.get("structured_answer"))
    counts = [i["documents_seen"] for i in ibans if i["documents_seen"] is not None]
    return {
        "supplier_id": h.get("supplier_id"),
        "status": h.get("status", "done"),
        "ibans": ibans,
        "distinct_ibans": len(ibans),
        "documents_seen": sum(counts) if counts else None,
        "confidence": h.get("confidence"),
        "matches_dataset": h.get("matches_dataset"),
        "evidence": _quotes(h.get("evidences")),
    }
