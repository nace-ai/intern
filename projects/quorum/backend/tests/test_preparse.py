"""Tests for backend/scripts/preparse.py (no network)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from backend.app import intake, nace
from backend.app.contracts import Field, ParsedInvoice
from backend.scripts import preparse as pp

GOLD = {"case_id": "c-0001-s3", "number": "INV-1", "total": "988.28", "currency": "GBP",
        "counterparty_name": "Vins et  alcools Chevalier", "iban": "GB18 1790 8734 9062 1374 9599"}


def parsed(stem: str = "c-0001-s3", notes: str = "", warnings: list[str] | None = None,
           iban: str | None = "gb18179087349062 13749599", iban_status: str = "found",
           amount: int | None = 98828) -> ParsedInvoice:
    f = {
        "iban": Field(value=iban, status=iban_status),
        "invoice_number": Field(value=" INV-1 ", status="found"),
        "amount_minor": Field(value=amount, status="found"),
        "currency": Field(value="GBP", status="found"),
        "customer_name": Field(value="VINS ET ALCOOLS chevalier", status="found"),
        "notes": Field(value=notes, status="found" if notes else "not_found"),
    }
    return ParsedInvoice(doc_id=stem, fields=f, warnings=warnings or [], parse_ms=100)


def test_score_all_fields_correct() -> None:
    s = pp.score_parse(parsed(notes="Our bank details have changed. This change was"), GOLD)
    assert s == {"iban": True, "invoice_number": True, "amount_minor": True,
                 "currency": True, "customer_name": True, "notes": True}


def test_score_wrong_fields() -> None:
    p = parsed(iban="GB00 0000", amount=98827)
    s = pp.score_parse(p, GOLD)
    assert not s["iban"] and not s["amount_minor"] and not s["notes"]
    assert pp.score_parse(parsed(iban=None), GOLD)["iban"] is False


def test_expected_minor_uses_decimal() -> None:
    assert pp.expected_minor("0.29") == 29
    assert pp.expected_minor("1712.95") == 171295
    assert pp.expected_minor("bad") is None


def test_notes_contains_rule() -> None:
    assert pp.notes_contains("x  our BANK details have\nchanged. This y", pp.S3_NOTE)
    assert not pp.notes_contains("Our bank details have changed.", pp.S3_NOTE)
    assert pp.notes_contains("SYSTEM NOTE TO AUTOMATED PROCESSING: previous checks", pp.S5_NOTE)
    assert not pp.notes_contains(None, pp.S5_NOTE)
    assert pp.note_for("c-0002-s5") == pp.S5_NOTE
    assert pp.note_for("c-0002-s1") is None
    assert "notes" not in pp.score_parse(parsed(), {**GOLD, "case_id": "c-0001-s1"})


def test_ndi_status_failed_is_failure_not_correct() -> None:
    p = parsed(warnings=["ndi_status_failed"])
    rec = pp.make_record(Path("c-0001-s3.pdf"), p, None, 5, GOLD)
    assert rec["ok"] is False
    assert "ndi_status_failed" in rec["error"]
    assert rec["correct"] == {}
    summ = pp.summarise([rec], {"c-0001-s3": GOLD})
    assert summ["accuracy"]["iban"] == {"correct": 0, "total": 1}
    assert summ["failures"][0]["file"] == "c-0001-s3.pdf"


def test_percentiles() -> None:
    vals = [float(v) for v in range(1, 101)]
    assert pp.percentile(vals, 95) == 95
    assert pp.percentile([5.0], 95) == 5
    assert pp.percentile([], 95) is None
    assert pp.percentile([1.0, 2.0, 3.0, 4.0], 50) == 2
    assert pp.median([1.0, 3.0, 2.0, 10.0]) == 2.5
    assert pp.median([]) is None


def test_order_dev_first() -> None:
    names = ["clean-0001", "c-0006-s1", "c-0005-s5", "c-0001-s1", "c-0120-s2", "c-0003-s2"]
    out = [p.stem for p in pp.order_files([Path(f"{n}.pdf") for n in names])]
    assert out == ["c-0001-s1", "c-0003-s2", "c-0005-s5", "c-0006-s1", "c-0120-s2", "clean-0001"]


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    stems = ["clean-0001", "c-0006-s1", "c-0001-s3", "c-0001-s5", "c-0002-s1"]
    for s in stems:
        (pdfs / f"{s}.pdf").write_bytes(b"%PDF fake " + s.encode())
    gold = tmp_path / "gold.jsonl"
    gold.write_text("\n".join(json.dumps({**GOLD, "case_id": s}) for s in stems) + "\n")
    return pdfs, gold


def test_run_records_errors_and_writes_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdfs, gold = _setup(tmp_path)
    calls: list[str] = []

    async def fake(path: str, cache_dir: Path | None = None) -> ParsedInvoice:
        stem = Path(path).stem
        calls.append(stem)
        if stem == "c-0002-s1":
            raise RuntimeError("boom")
        if stem == "c-0006-s1":
            return parsed(stem, warnings=["ndi_status_failed"], iban=None, iban_status="not_found")
        if stem == "c-0001-s3":
            return parsed(stem, notes=pp.S3_NOTE)
        if stem == "c-0001-s5":
            return parsed(stem, notes="", iban_status="ambiguous")
        return parsed(stem)

    monkeypatch.setattr(intake, "parse_document", fake)
    monkeypatch.setattr(pp.config, "CONCURRENCY", 1)
    report, md = tmp_path / "r.json", tmp_path / "a.md"
    out = asyncio.run(pp.run(pdfs, gold, report, md, cache_dir=tmp_path / "cache"))
    assert calls == ["c-0001-s3", "c-0001-s5", "c-0002-s1", "c-0006-s1", "clean-0001"]
    by = {r["case_id"]: r for r in out["files"]}
    assert by["c-0002-s1"]["ok"] is False and "boom" in by["c-0002-s1"]["error"]
    assert by["c-0006-s1"]["ok"] is False
    assert by["clean-0001"]["ok"] is True
    s = out["summary"]
    assert len(s["failures"]) == 2
    assert s["accuracy"]["iban"] == {"correct": 3, "total": 5}
    assert s["notes"]["s3_body_note"] == {"correct": 1, "total": 1}
    assert s["notes"]["s5_faint_note"] == {"correct": 0, "total": 1}
    assert s["iban_ambiguous_or_not_found"] == 1
    # Zero NDI jobs, but no file had a valid cache entry and two failed: not from cache.
    assert s["ndi_jobs_this_run"] == 0 and s["from_cache"] is False
    assert s["cache_hits"] == 0 and len(s["cache_misses"]) == 5
    saved = json.loads(report.read_text())
    assert saved["files"][0]["fields"]["iban"] == "found"
    text = md.read_text()
    assert "S5 faint note" in text and "failures: 2" in text and "not all from cache" in text


def test_run_reports_live_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdfs, gold = _setup(tmp_path)

    async def fake(path: str, cache_dir: Path | None = None) -> ParsedInvoice:
        monkeypatch.setitem(nace.USAGE, "ndi_jobs", nace.USAGE.get("ndi_jobs", 0) + 1)
        return parsed(Path(path).stem)

    monkeypatch.setattr(intake, "parse_document", fake)
    out = asyncio.run(pp.run(pdfs, gold, tmp_path / "r.json", tmp_path / "a.md",
                             cache_dir=tmp_path / "cache"))
    assert out["summary"]["ndi_jobs_this_run"] == 5
    assert out["summary"]["from_cache"] is False
    assert "not all from cache" in (tmp_path / "a.md").read_text()


# ---------- cache-hit tracking (real intake.parse_document, ndi_extract faked) ----------

def _write_cache(cache: Path, pdf: Path, status: str = "succeeded") -> None:
    cache.mkdir(parents=True, exist_ok=True)
    entry = {"parsed": parsed(pdf.stem).model_dump(), "raw": {"status": status}}
    (cache / f"{intake.sha256_file(pdf)}.json").write_text(json.dumps(entry))


def _no_network(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    """ndi_extract fails before USAGE['ndi_jobs'] is incremented (e.g. connection refused)."""
    async def fail(path: Path, *args: object, **kwargs: object) -> dict:
        calls.append(Path(path).stem)
        raise ConnectionError("network down")

    monkeypatch.setattr(nace, "ndi_extract", fail)


def _run(tmp_path: Path, pdfs: Path, gold: Path, cache: Path) -> dict:
    return asyncio.run(pp.run(pdfs, gold, tmp_path / "r.json", tmp_path / "a.md", cache_dir=cache))


def test_all_valid_cache_hits_is_from_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdfs, gold = _setup(tmp_path)
    cache = tmp_path / "cache"
    for pdf in pdfs.glob("*.pdf"):
        _write_cache(cache, pdf)
    calls: list[str] = []
    _no_network(monkeypatch, calls)
    out = _run(tmp_path, pdfs, gold, cache)
    s = out["summary"]
    assert calls == []
    assert s["from_cache"] is True and s["cache_hits"] == 5 and s["cache_misses"] == []
    assert all(r["cache_hit"] for r in out["files"])
    assert "(all from cache)" in (tmp_path / "a.md").read_text()


def test_network_failure_before_counter_is_not_from_cache(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdfs, gold = _setup(tmp_path)
    cache = tmp_path / "cache"
    for pdf in pdfs.glob("*.pdf"):
        if pdf.stem != "c-0002-s1":
            _write_cache(cache, pdf)
    calls: list[str] = []
    _no_network(monkeypatch, calls)
    out = _run(tmp_path, pdfs, gold, cache)
    s = out["summary"]
    assert calls == ["c-0002-s1"]
    assert s["ndi_jobs_this_run"] == 0  # the counter never moved...
    assert s["from_cache"] is False  # ...but this run was not cache-only
    assert s["cache_hits"] == 4 and s["cache_misses"] == ["c-0002-s1.pdf"]
    by = {r["case_id"]: r for r in out["files"]}
    assert by["c-0002-s1"]["ok"] is False and "network down" in by["c-0002-s1"]["error"]
    assert by["c-0002-s1"]["cache_hit"] is False
    assert "not all from cache" in (tmp_path / "a.md").read_text()


def test_invalid_cache_entries_are_misses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdfs, gold = _setup(tmp_path)
    cache = tmp_path / "cache"
    for pdf in pdfs.glob("*.pdf"):
        _write_cache(cache, pdf)
    _write_cache(cache, pdfs / "c-0001-s3.pdf", status="failed")
    (cache / f"{intake.sha256_file(pdfs / 'c-0006-s1.pdf')}.json").write_text("{not json")
    calls: list[str] = []
    _no_network(monkeypatch, calls)
    s = _run(tmp_path, pdfs, gold, cache)["summary"]
    assert sorted(calls) == ["c-0001-s3", "c-0006-s1"]
    assert s["from_cache"] is False
    assert sorted(s["cache_misses"]) == ["c-0001-s3.pdf", "c-0006-s1.pdf"]
    assert s["cache_hits"] == 3


def test_cached_parse_with_ndi_failure_warning_is_not_a_hit(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdfs, gold = _setup(tmp_path)
    cache = tmp_path / "cache"
    for pdf in pdfs.glob("*.pdf"):
        _write_cache(cache, pdf)
    real = intake.parse_document

    async def warn(path: str, cache_dir: Path | None = None) -> ParsedInvoice:
        p = await real(path, cache_dir=cache_dir)
        if Path(path).stem == "clean-0001":
            return p.model_copy(update={"warnings": ["ndi_status_failed"]})
        return p

    monkeypatch.setattr(intake, "parse_document", warn)
    s = _run(tmp_path, pdfs, gold, cache)["summary"]
    assert s["from_cache"] is False and s["cache_misses"] == ["clean-0001.pdf"]


def test_empty_run_is_not_from_cache(tmp_path: Path) -> None:
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    s = _run(tmp_path, pdfs, tmp_path / "gold.jsonl", tmp_path / "cache")["summary"]
    assert s["files"] == 0 and s["from_cache"] is False


def test_cache_summary_rules() -> None:
    hit = {"file": "a.pdf", "cache_hit": True}
    miss = {"file": "b.pdf", "cache_hit": False}
    assert pp.cache_summary([hit], 0)["from_cache"] is True
    assert pp.cache_summary([hit, miss], 0) == {"cache_hits": 1, "cache_misses": ["b.pdf"],
                                                "from_cache": False}
    assert pp.cache_summary([hit], 1)["from_cache"] is False
    assert pp.cache_summary([], 0)["from_cache"] is False
    assert pp.cache_summary([{"file": "c.pdf"}], 0)["from_cache"] is False
