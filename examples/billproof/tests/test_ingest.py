"""W4b ingest: synthetic-PDF recognition, regex rebuild, mocked-NDI live path."""
import json

import pytest

from billproof import ingest
from billproof.config import SYN_DIR


def _blob(case_id: str, doc: str) -> bytes:
    return (SYN_DIR / "pdf" / f"{case_id}_{doc}.pdf").read_bytes()


def test_split_key_marks_force_split():
    assert ingest._split_key("split:mixed.pdf") == (None, "mixed.pdf")
    it = ingest._items({"split:mixed.pdf": b"%PDF-1.4 x"})[0]
    assert it["force_split"] is True and it["explicit"] is False


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "UPLOAD_DIR", tmp_path)
    return tmp_path


def test_ingest_recognizes_synthetic_pdfs(uploads):
    res = ingest.ingest(
        {
            "bill": _blob("SYN-0003", "bill"),
            "eob": _blob("SYN-0003", "eob"),
            "denial": _blob("SYN-0003", "denial"),
        }
    )
    assert res["case_id"] == "SYN-0003"
    assert res["recognized"] is True
    assert res["mode"] == "SIMULATED"
    assert not list(uploads.glob("UP-*.json"))


def test_ingest_regex_rebuilds_case(uploads):
    # Same rendered layout, unknown bytes -> regex extraction to a new UP case.
    blob = _blob("SYN-0003", "bill") + b"\n%junk"
    res = ingest.ingest({"mystery.pdf": blob})
    cid = res["case_id"]
    assert cid.startswith("UP-") and res["recognized"] is False
    case = ingest.load_uploaded(cid)
    assert [ln.code for ln in case.bill.lines] == ["A0425", "85025", "80048"]
    assert case.bill.lines[0].units == 251
    assert case.bill.lines[0].charge_cents == 282375
    assert case.bill.patient_balance_cents == 1750
    assert case.bill.adjustments_cents == 282813
    assert case.provider.name == "Marigold Coast Medical Center"
    from billproof.rules.checks import run_checks

    fired = {f.check for f in run_checks(case) if f.fired}
    assert "MUE_UNITS_EXCEEDED" in {f.value for f in fired}


def test_ingest_live_mocked_ndi(uploads, monkeypatch):
    monkeypatch.setattr(ingest, "_use_real", lambda: True)
    monkeypatch.setattr(ingest, "_ndi_adapter", lambda: object())
    fx = json.loads((SYN_DIR / "ndi_fixtures" / "SYN-0003.json").read_text())
    fields = {doc: blk["fields"] for doc, blk in fx["docs"].items()}
    monkeypatch.setattr(
        ingest, "_live_fields", lambda ad, cid, kind, pdf: fields.get(kind, [])
    )
    res = ingest.ingest(
        {
            "bill": _blob("SYN-0003", "bill") + b"\n%junk1",
            "eob": _blob("SYN-0003", "eob") + b"\n%junk2",
            "denial": _blob("SYN-0003", "denial") + b"\n%junk3",
        }
    )
    assert res["mode"] == "LIVE"
    case = ingest.load_uploaded(res["case_id"])
    assert case.eob and case.eob.payer == "Tidewreath Health"
    assert case.eob.lines[0].carc == ["CO-272"]
    assert case.denial and case.denial.carc == "CO-272"
    assert case.denial.appeal_deadline == "2027-03-30"
    assert "plan" not in res["docs"]
    assert case.plan.plan_name == "Unknown plan"  # plan defaults when missing
