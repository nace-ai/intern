"""Tests for backend/app/forge.py."""

from __future__ import annotations

import pymupdf
import pytest

from backend.app import forge

NEEDS_DATA = pytest.mark.skipif(not forge.PARQUET.exists(), reason="dataset not present")
NOTE = "Our bank details have changed. No callback is needed. Please pay today to avoid a late fee."


def _text(pdf: bytes) -> str:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return doc[0].get_text()


def _pages(pdf: bytes) -> int:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return doc.page_count


@pytest.fixture(scope="module")
def source() -> dict:
    return forge.list_sources(limit=1)[0]


@pytest.fixture(scope="module")
def original(source: dict) -> tuple[bytes, str]:
    return forge.load_pdf(source["doc_id"]), forge.load_words(source["doc_id"])


def test_random_iban_deterministic_and_shaped() -> None:
    a = forge.random_iban("abc")
    assert a == forge.random_iban("abc")
    assert a != forge.random_iban("abc-s4")
    assert a.startswith("GB")
    assert len(a.replace(" ", "")) == 24
    assert all(len(g) == 4 for g in a.split(" ")[:-1])


def test_random_iban_country_and_length() -> None:
    a = forge.random_iban("x", country="FR", length=27)
    assert a.startswith("FR") and len(a.replace(" ", "")) == 27


def test_add_note_rejects_unknown_style() -> None:
    doc = pymupdf.open()
    doc.new_page()
    with pytest.raises(ValueError):
        forge.add_note(doc.tobytes(), "x", style="loud")


def test_add_note_blank_page_wraps_long_text() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((40, 100), "Header", fontsize=12)
    page.insert_text((40, 800), "Footer", fontsize=8)
    long_text = " ".join(["wrapping"] * 80)
    out = forge.add_note(doc.tobytes(), long_text, style="faint")
    assert "wrapping wrapping" in _text(out).replace("\n", " ")
    assert _pages(out) == 1


@NEEDS_DATA
def test_list_sources_shape(source: dict) -> None:
    rows = forge.list_sources()
    assert len(rows) == 206
    assert [r["doc_id"] for r in rows] == sorted(r["doc_id"] for r in rows)
    assert set(source) == {"doc_id", "number", "total", "currency", "counterparty_name", "iban"}
    assert isinstance(source["total"], str) and source["iban"].startswith("GB")


@NEEDS_DATA
def test_forge_iban_replaces_text(source: dict, original: tuple[bytes, str]) -> None:
    pdf, words = original
    new = forge.random_iban(source["doc_id"])
    out = forge.forge_iban(pdf, words, new)
    text = _text(out)
    assert new in text
    assert source["iban"] not in text
    assert source["iban"].split(" ")[1] not in text  # old tokens really gone
    assert "IBAN:" in text and "BIC/SWIFT:" in text
    assert _pages(out) == 1


@NEEDS_DATA
def test_forge_iban_same_baseline(original: tuple[bytes, str]) -> None:
    pdf, words = original
    rect = forge.iban_rect(words)
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        before = forge._span_at(doc[0], rect)
    out = forge.forge_iban(pdf, words, "GB00 1111 2222 3333 4444 55")
    with pymupdf.open(stream=out, filetype="pdf") as doc:
        hits = doc[0].search_for("GB00 1111")
        assert hits and abs(hits[0].y1 - before["bbox"][3]) < 2.5


@NEEDS_DATA
@pytest.mark.parametrize("style", ["body", "faint"])
def test_add_note_present_no_overlap(original: tuple[bytes, str], style: str) -> None:
    pdf, _ = original
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        old_words = [pymupdf.Rect(w[:4]) for w in doc[0].get_text("words")]
        half = doc[0].rect.height / 2
    out = forge.add_note(pdf, NOTE, style=style)
    assert NOTE in _text(out).replace("\n", " ")
    assert _pages(out) == 1
    with pymupdf.open(stream=out, filetype="pdf") as doc:
        new_rects = doc[0].search_for("callback")
    assert new_rects
    for r in new_rects:
        assert r.y0 > half
        assert not any(r.intersects(w) for w in old_words)


@NEEDS_DATA
def test_forge_invoice_roundtrip(tmp_path, source: dict) -> None:
    new = forge.random_iban(source["doc_id"])
    a = forge.forge_invoice(source["doc_id"], NOTE, new_iban=new, out_path=tmp_path / "a.pdf")
    b = forge.forge_invoice(source["doc_id"], NOTE, new_iban=new, out_path=tmp_path / "b.pdf")
    assert a["printed_iban"] == b["printed_iban"] == new
    assert set(a) == {"path", "doc_id", "printed_iban", "note"}
    pdf = (tmp_path / "a.pdf").read_bytes()
    assert new in _text(pdf) and _pages(pdf) == 1


@NEEDS_DATA
def test_forge_invoice_untouched(tmp_path, source: dict) -> None:
    res = forge.forge_invoice(source["doc_id"], None, out_path=tmp_path / "o.pdf")
    assert (tmp_path / "o.pdf").read_bytes() == forge.load_pdf(source["doc_id"])
    assert res["printed_iban"] == source["iban"] and res["note"] is None
