"""In-memory synthetic mutations and an intentionally limited bill-checker baseline."""
from __future__ import annotations

from datetime import date, timedelta
from itertools import count

from billproof.schema import Case, GoldItem

TAMPER_OPS = [
    "duplicate_line", "inflate_units", "add_unbundled", "change_eob_resp",
    "shift_dos_after_death",
]
_ids = count(1)


def _invalidate(case: Case, *prefixes: str) -> None:
    for key in list(case.citations):
        if any(key == p or key.startswith(p + ".") for p in prefixes):
            del case.citations[key]


def tamper(case: Case, ops: list[dict]) -> tuple[Case, dict[str, GoldItem]]:
    """Return an independent case and labels for its newly built question set.

    Optional parameters: line_no (defaults to first line), units, code,
    delta_cents / patient_resp_cents, dos / date_of_death.
    Changed fields lose old document citations: a source PDF cannot prove an edit.
    """
    from billproof.rules.cms_tables import all_codes, code_info, fee_cents, mue_limit, ptp_edit

    changed = case.model_copy(deep=True)
    changed.case_id = f"{case.case_id}-T{next(_ids)}"
    for spec in ops:
        op = spec.get("op")
        if op not in TAMPER_OPS:
            raise ValueError(f"Unknown tamper op: {op!r}")
        if not changed.bill.lines:
            raise ValueError("Tampering requires at least one bill line")
        line_no = int(spec.get("line_no", changed.bill.lines[0].line_no))
        line = next((line for line in changed.bill.lines if line.line_no == line_no), None)
        if line is None:
            raise ValueError(f"No bill line {line_no}")
        if op in ("duplicate_line", "add_unbundled"):
            new = line.model_copy(deep=True)
            new.line_no = max(x.line_no for x in changed.bill.lines) + 1
            if op == "add_unbundled":
                candidates = [str(spec["code"])] if "code" in spec else all_codes()
                code = next((c for c in candidates if c != line.code and ptp_edit(line.code, c) in (0, 1)), None)
                if code is None:
                    raise ValueError(f"No active NCCI secondary code for {line.code}; select another line")
                new.code = code
                new.label = (code_info(code) or {}).get("label", "Additional service")
                new.units = 1
                new.modifiers = []
                new.charge_cents = fee_cents(code, facility=new.pos in {"19", "21", "22", "23", "24"}) or line.charge_cents
            changed.bill.lines.append(new)
            changed.bill.total_charges_cents += new.charge_cents
            changed.bill.patient_balance_cents += new.charge_cents
            _invalidate(changed, "bill.total_charges_cents", "bill.patient_balance_cents")
        elif op == "inflate_units":
            limit = mue_limit(line.code)
            units = int(spec.get("units", max(line.units + 1, (limit[0] + 1) if limit else line.units * 3)))
            if units <= line.units:
                raise ValueError("Inflated units must exceed current units")
            if line.units <= 0:
                raise ValueError("inflate_units requires a positive original unit count")
            old_charge = line.charge_cents
            line.charge_cents = (old_charge * units + line.units // 2) // line.units
            line.units = units
            delta = line.charge_cents - old_charge
            changed.bill.total_charges_cents += delta
            changed.bill.patient_balance_cents += delta
            _invalidate(changed, f"bill.lines.{line_no}.units", f"bill.lines.{line_no}.charge_cents", "bill.total_charges_cents", "bill.patient_balance_cents")
        elif op == "change_eob_resp":
            if changed.eob is None:
                raise ValueError("change_eob_resp requires an EOB")
            eobline = next((x for x in changed.eob.lines if x.line_no == line_no), None)
            if eobline is None:
                raise ValueError(f"No EOB line {line_no}")
            amount = int(spec.get("patient_resp_cents", eobline.patient_resp_cents + int(spec.get("delta_cents", 5000))))
            if amount < 0:
                raise ValueError("Patient responsibility cannot be negative")
            eobline.patient_resp_cents = amount
            changed.eob.patient_resp_total_cents = sum(x.patient_resp_cents for x in changed.eob.lines)
            _invalidate(changed, f"eob.lines.{line_no}.patient_resp_cents", "eob.patient_resp_total_cents")
        elif op == "shift_dos_after_death":
            if line.dos is None:
                raise ValueError("shift_dos_after_death requires a service date")
            death = spec.get("date_of_death") or changed.patient.date_of_death or (date.fromisoformat(line.dos) - timedelta(days=1)).isoformat()
            changed.patient.date_of_death = death
            line.dos = spec.get("dos") or (date.fromisoformat(death) + timedelta(days=1)).isoformat()
            if line.dos <= death:
                raise ValueError("Service date must follow date of death")
            _invalidate(changed, "patient.date_of_death", f"bill.lines.{line_no}.dos")
    return changed, gold_for_case(changed)


def gold_for_case(case: Case) -> dict[str, GoldItem]:
    """Use the same evidence-aware oracle as the synthetic benchmark labels."""
    from billproof.rules.checks import run_checks
    from billproof.decisions.questions import build_questions, LINE_FAMILIES, CASE_FAMILIES
    from billproof.synth.generator import _oracle

    flags = run_checks(case)
    answers, difficulties = _oracle(case, flags)
    gold = {}
    for q in build_questions(case, flags):
        error = next((e for e, family in LINE_FAMILIES.items()
                      if q.name.startswith(family + "_L")), None)
        error = error or next((e for e, family in CASE_FAMILIES.items() if q.name == family), None)
        gold[q.name] = GoldItem(
            answer=answers[q.name], difficulty=difficulties.get(q.name, 0.4),
            error_type=error, rule_id=f"BP-{error.value}" if error else None,
        )
    return gold


def naive_check(case: Case) -> dict:
    """Only exact duplicate rows and the bill/EOB responsibility totals."""
    seen = set()
    issues = []
    for line in case.bill.lines:
        key = (line.dos, line.code, tuple(line.modifiers), line.units, line.charge_cents, line.rendering_provider)
        if key in seen:
            issues.append({"check": "DUPLICATE", "line_no": line.line_no, "detail": "Exact duplicate bill row"})
        seen.add(key)
    if case.eob and case.bill.patient_balance_cents != sum(x.patient_resp_cents for x in case.eob.lines):
        issues.append({"check": "EOB_BILL_MISMATCH", "line_no": None, "detail": "Bill balance differs from EOB responsibility sum"})
    caught = bool(issues)
    return {"case_action": "DISPUTE" if caught else "OK", "caught": caught,
            "passed": not caught, "issues": issues, "flags": issues}
