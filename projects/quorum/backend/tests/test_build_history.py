"""Offline tests for the pure helpers of backend/scripts/build_history.py."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("build_history", ROOT / "backend" / "scripts" / "build_history.py")
bh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bh)  # type: ignore[union-attr]


def _row(doc_id: str, company: str = bh.COBALT, pages: int = 1, label: bool = True) -> dict:
    words = [{"text": "IBAN:" if label else "Total:", "page": 0}]
    extracted = {"parties": {"issuer": {"bank": {"iban": "GB18 1790"}}}}
    return {"doc_id": doc_id, "company": company, "pages": pages,
            "words": json.dumps(words), "extracted_data": json.dumps(extracted)}


def test_file_path() -> None:
    assert bh.file_path("cobalt-wholesale-ltd", "abc") == "invoices/cobalt-wholesale-ltd/abc.pdf"


def test_eligible_filters_and_sorts() -> None:
    rows = [_row("b"), _row("a"), _row("c", pages=2), _row("d", label=False),
            _row("e", company=bh.EMBER), _row("f")]
    out = bh.eligible(rows, bh.COBALT, exclude={"f"})
    assert [r["doc_id"] for r in out] == ["a", "b"]
    assert out[0]["iban"] == "GB18 1790"


def test_top_iban_normalises() -> None:
    assert bh.top_iban({"ibans": [{"iban": "gb18 1790-8734"}, {"iban": "FR83"}]}) == "GB1817908734"
    assert bh.top_iban({"ibans": []}) is None
    assert bh.top_iban(None) is None


def test_json_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "x" / "y.json"
    assert bh.read_json(p) == {}
    bh.write_json(p, {"a": 1})
    assert bh.read_json(p) == {"a": 1}
