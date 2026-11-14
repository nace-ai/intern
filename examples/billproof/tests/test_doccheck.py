"""Document checklist: a consistent packet stays green; gaps and orphans do not."""
import json

from billproof.doccheck import check_case
from billproof.schema import Bill, BillLine, Case, Citation, Eob, EobLine, Patient, PlanSummary, Provider

_STATUSES = {"approve", "reject", "missing", "review"}
_RANK = {"missing": 0, "reject": 1, "review": 2, "approve": 3}


def _line(n, code="99213", charge=10000, dos="2026-09-15", units=1):
    return BillLine(line_no=n, code=code, units=units, dos=dos, label="Office visit", charge_cents=charge)


def _eob_line(n, code="99213", billed=10000, resp=2000):
    return EobLine(line_no=n, code=code, billed_cents=billed, allowed_cents=billed,
                   plan_paid_cents=billed - resp, patient_resp_cents=resp)


def _case(lines, eob_lines, balance, resp_total, npi="1234567890", statement="2026-10-01",
          processed="2026-10-05", citations=None):
    return Case(
        case_id="SYN-0001", split="dev", title="Office visit",
        patient=Patient(patient_id="PT-1", dob="1990-01-01", member_id="M-1"),
        provider=Provider(name="Synthetic Clinic", npi=npi),
        plan=PlanSummary(plan_name="Test PPO", deductible_cents=0, oop_max_cents=500000),
        bill=Bill(account_no="A1", statement_date=statement, lines=lines,
                  total_charges_cents=sum(line.charge_cents for line in lines),
                  patient_balance_cents=balance),
        eob=Eob(payer="Harbor Synthetic Health", claim_id="CL-1", processed_date=processed,
                lines=eob_lines, patient_resp_total_cents=resp_total),
        citations=citations or {},
    )


def _assert_shape(rows):
    assert rows
    for row in rows:
        assert set(row) == {"item", "status", "reason", "evidence"}
        assert row["status"] in _STATUSES
        assert row["reason"].endswith(".")
        assert len(row["evidence"]) <= 3
    json.dumps(rows)


def test_consistent_bill_and_eob_is_all_approve():
    case = _case([_line(1)], [_eob_line(1)], balance=2000, resp_total=2000)
    rows = check_case(case)
    _assert_shape(rows)
    assert {row["status"] for row in rows} == {"approve"}
    by_item = {row["item"]: row for row in rows}
    assert "Harbor Synthetic Health" in by_item["eob"]["reason"]
    assert "2026-10-05" in by_item["eob"]["reason"]
    assert "$20.00" in by_item["patient balance"]["reason"]


def test_orphan_eob_line_missing_npi_and_statement_date():
    citation = Citation(doc="bill", page=1, polygons=[[(0.1, 0.2), (0.3, 0.2), (0.3, 0.4)]], text="Synthetic Clinic")
    case = _case(
        [_line(1)], [_eob_line(2, code="73721", billed=5000, resp=1000)],
        balance=1000, resp_total=1000, npi=None, statement="",
        citations={"provider.name": citation},
    )
    rows = check_case(case)
    _assert_shape(rows)
    by_item = {row["item"]: row for row in rows}
    assert by_item["provider.npi"]["status"] == "missing"
    assert "provider identifier" in by_item["provider.npi"]["reason"]
    assert by_item["provider.npi"]["evidence"][0]["text"] == "Synthetic Clinic"
    assert by_item["provider.npi"]["evidence"][0]["polygons"][0][0] == [0.1, 0.2]
    assert by_item["bill.statement_date"]["status"] == "missing"
    assert by_item["eob line 2 (73721)"]["status"] == "reject"
    assert "no matching line" in by_item["eob line 2 (73721)"]["reason"]
    assert by_item["bill line 1 (99213)"]["status"] == "review"
    order = [_RANK[row["status"]] for row in rows]
    assert order == sorted(order)
