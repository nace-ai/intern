"""Tests for the hardening brief: items 1, 3, 4, 5 and 7."""
import asyncio
import json

import pytest

from backend.app import checks, config, forge, intake, nace, panel, policy
from backend.app.contracts import Facts
from backend.tests.test_intake import RESULT
from backend.tests.test_panel import _answers, _facts, _parsed


# --- item 1: failed NDI job ------------------------------------------------

def _pdf(tmp_path):
    pdf = tmp_path / "case-0001.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    return pdf


def test_failed_ndi_raises_and_no_cache(tmp_path, monkeypatch):
    async def fake_extract(path, schema, instructions=None):
        return {"parse_job_id": "p", "extract_job_id": "e", "status": "failed",
                "result": None, "parse_ms": 5, "error": "engine crashed"}

    monkeypatch.setattr(nace, "ndi_extract", fake_extract)
    cache = tmp_path / "cache"
    with pytest.raises(intake.IntakeError) as ei:
        asyncio.run(intake.parse_document(_pdf(tmp_path), cache_dir=cache))
    assert ei.value.status == "failed" and ei.value.error == "engine crashed"
    assert not cache.exists() or not list(cache.glob("*.json"))


def test_failed_cache_entry_ignored_not_deleted(tmp_path, monkeypatch):
    pdf = _pdf(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    bad = cache / f"{intake.sha256_file(pdf)}.json"
    bad.write_text(json.dumps({"parsed": {"bogus": 1}, "raw": {"status": "failed"}}))
    calls = []

    async def fake_extract(path, schema, instructions=None):
        calls.append(path)
        return {"parse_job_id": "p", "extract_job_id": "e", "status": "succeeded",
                "result": RESULT, "parse_ms": 42}

    monkeypatch.setattr(nace, "ndi_extract", fake_extract)
    out = asyncio.run(intake.parse_document(pdf, cache_dir=cache))
    assert len(calls) == 1 and out.parse_ms == 42
    assert bad.exists()
    assert json.loads(bad.read_text())["raw"]["status"] == "succeeded"


# --- item 3: load_trusted --------------------------------------------------

def test_load_trusted_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        checks.load_trusted(tmp_path / "nope.json")


@pytest.mark.parametrize("content", ["[1, 2]", '{"SUP-1": [1]}', '"x"', "{not json"])
def test_load_trusted_bad_shape(tmp_path, content):
    f = tmp_path / "trusted.json"
    f.write_text(content)
    with pytest.raises(ValueError):
        checks.load_trusted(f)


def test_load_trusted_valid_and_default(tmp_path, monkeypatch):
    f = tmp_path / "trusted.json"
    f.write_text(json.dumps({"SUP-1": {"supplier_id": "SUP-1", "names": ["Acme"],
                                       "iban": "GB29NWBK60161331926819"}}))
    got = checks.load_trusted(f)
    assert "SUP-1" in got
    monkeypatch.setattr(config, "TRUSTED_FILE", f)
    assert set(checks.load_trusted()) == set(got)


# --- item 4: usage across processes ---------------------------------------

@pytest.fixture
def usage_file(tmp_path, monkeypatch):
    f = tmp_path / "usage.json"
    monkeypatch.setattr(config, "USAGE_FILE", f)
    monkeypatch.setattr(nace, "USAGE", {k: 0 for k in nace.USAGE_KEYS})
    return f


def _lines(*entries):
    return "\n".join(json.dumps(e) for e in entries) + "\n"


def test_usage_totals_last_per_run(usage_file):
    usage_file.write_text(_lines(
        {"run_id": "a", "drex_calls": 1}, {"run_id": "b", "drex_calls": 10},
        {"run_id": "a", "drex_calls": 3, "ndi_pages": 2},
    ))
    t = nace.usage_totals()
    assert t["drex_calls"] == 13 and t["ndi_pages"] == 2
    assert set(t) == set(nace.USAGE_KEYS)


def test_usage_totals_legacy_lines_last_only(usage_file):
    usage_file.write_text(_lines({"drex_calls": 2}, {"drex_calls": 5},
                                 {"run_id": "x", "drex_calls": 1}) + "garbage\n")
    assert nace.usage_totals(usage_file)["drex_calls"] == 6


def test_usage_totals_old_single_dict(usage_file):
    usage_file.write_text(json.dumps({"drex_calls": 4, "ndi_jobs": 2}, indent=2))
    t = nace.usage_totals()
    assert t["drex_calls"] == 4 and t["ndi_jobs"] == 2


def test_usage_totals_missing_file(tmp_path):
    t = nace.usage_totals(tmp_path / "none.json")
    assert t and all(v == 0 for v in t.values())


def test_bump_writes_run_id(usage_file):
    nace.bump(drex_calls=1)
    nace.bump(drex_calls=1)
    lines = [json.loads(x) for x in usage_file.read_text().splitlines() if x.strip()]
    assert all(e["run_id"] == nace.RUN_ID for e in lines)
    assert nace.usage_totals()["drex_calls"] == 2


# --- item 5: policy validation --------------------------------------------

def test_guard_rejects_unknown_action():
    with pytest.raises(ValueError):
        policy.guard("maybe", Facts())


def test_choose_rejects_unknown_key():
    with pytest.raises(ValueError):
        policy.choose({"bogus": 1.0})


def test_decide_invalid_choice_is_error(monkeypatch):
    async def fake(state, questions, model=None):
        a = _answers({"approve": 0.9, "verify": 0.05, "block": 0.05})
        a["action"] = {"type": "choice", "choice": "maybe", "confidence": 0.9}
        return {"answers": a, "wall_ms": 1}

    monkeypatch.setattr(nace, "drex", fake)
    d = asyncio.run(panel.decide(_parsed(), _facts()))
    assert d.error and d.raw_action is None and d.final_action is None


# --- item 7: IBAN length ---------------------------------------------------

def test_random_iban_default_24_groups_of_four():
    a = forge.random_iban("seed")
    groups = a.split(" ")
    assert len(a.replace(" ", "")) == 24
    assert [len(g) for g in groups] == [4, 4, 4, 4, 4, 4]
