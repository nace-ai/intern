import asyncio
import json

import pytest

from backend.app import intake, nace


@pytest.mark.parametrize(
    "text,cur,want",
    [
        ("£1,712.95", "GBP", 171295),
        ("1.712,95", "EUR", 171295),
        ("1712", "USD", 171200),
        ("12,50", "EUR", 1250),
        ("1,712", "USD", 171200),
        ("¥12,000", "JPY", 12000),
        ("0.1", "USD", 10),
        ("", "USD", None),
        (None, None, None),
        ("n/a", "USD", None),
    ],
)
def test_amount_to_minor(text, cur, want):
    got = intake.amount_to_minor(text, cur)
    assert got == want
    assert got is None or isinstance(got, int)


RESULT = {
    "data": {"supplier_name": "Acme Ltd", "total_amount": "£1,712.95", "currency": "GBP"},
    "fields": [
        {"path": "/iban", "value": "GB29 NWBK 6016 1331 9268 19", "status": "found",
         "citations": [{"location": {"page": 1, "polygons": [[[0.1, 0.2]]]},
                        "source_text": "IBAN GB29..."}]},
        {"path": "/total_amount", "value": "£1,712.95", "status": "found", "citations": []},
        {"path": "/notes", "value": "", "status": "found"},
    ],
}


def test_map_result():
    fields, warnings = intake.map_result(RESULT)
    assert fields["iban"].value == "GB29 NWBK 6016 1331 9268 19"
    assert isinstance(fields["iban"].value, str)
    assert fields["iban"].citations[0].page == 1
    assert fields["iban"].citations[0].source_text == "IBAN GB29..."
    assert fields["amount_minor"].value == 171295 and fields["amount_minor"].status == "found"
    assert fields["supplier_name"].value == "Acme Ltd"
    assert fields["notes"].value == "" and fields["notes"].status == "not_found"
    assert fields["bic"].status == "not_found"
    assert warnings == []
    assert set(fields) >= {"supplier_name", "invoice_number", "amount_minor", "currency",
                           "iban", "bic", "bank_name", "customer_name", "notes"}


def test_map_result_bad_amount():
    fields, warnings = intake.map_result({"data": {"total_amount": "see attached"}})
    assert fields["amount_minor"].value is None
    assert "amount_unparseable" in warnings


def test_parse_document_cache(tmp_path, monkeypatch):
    pdf = tmp_path / "case-0001.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    calls = []

    async def fake_extract(path, schema, instructions=None):
        calls.append(path)
        return {"parse_job_id": "p", "extract_job_id": "e", "status": "succeeded",
                "result": RESULT, "parse_ms": 42}

    monkeypatch.setattr(nace, "ndi_extract", fake_extract)
    cache = tmp_path / "cache"
    a = asyncio.run(intake.parse_document(pdf, cache_dir=cache))
    b = asyncio.run(intake.parse_document(pdf, cache_dir=cache))
    assert len(calls) == 1
    assert a.parse_ms == 42 and a.doc_id == "case-0001"
    assert a.content_hash.startswith("sha256:")
    assert b.model_dump() == a.model_dump()
    stored = json.loads(next(cache.glob("*.json")).read_text())
    assert set(stored) == {"parsed", "raw"}
