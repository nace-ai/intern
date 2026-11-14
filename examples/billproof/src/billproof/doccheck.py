"""Deterministic completeness and cross-document checks for one case."""

from __future__ import annotations

from billproof.schema import Case

_RANK = {"missing": 0, "reject": 1, "review": 2, "approve": 3}


def _usd(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(int(cents)), 100)
    return f"{sign}${whole:,}.{fraction:02d}"


def _blank(value: str | None) -> bool:
    return value is None or not str(value).strip()


def _shown_code(code: str | None) -> str:
    text = (code or "").strip()
    return text or "?"


def _evidence(case: Case, paths: list[str], *, skip: set[str] | None = None) -> list[dict]:
    skipped = skip or set()
    chosen: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path.endswith("*"):
            prefix = path[:-1]
            matches = sorted(key for key in case.citations if key.startswith(prefix))
        else:
            matches = [path]
        for key in matches:
            if key in skipped or key in seen or key not in case.citations:
                continue
            seen.add(key)
            chosen.append(key)
            if len(chosen) == 3:
                return [case.citations[key].model_dump(mode="json") for key in chosen]
    return [case.citations[key].model_dump(mode="json") for key in chosen]


def _row(item: str, status: str, reason: str, evidence: list[dict]) -> dict:
    return {"item": item, "status": status, "reason": reason, "evidence": evidence}


def check_case(case: Case) -> list[dict]:
    """Checklist rows for document presence, required fields, and bill/EOB consistency."""
    rows: list[dict] = []
    bill = case.bill
    eob = case.eob
    bill_by_no = {line.line_no: line for line in bill.lines}

    if eob is None:
        rows.append(_row(
            "eob", "missing",
            "No explanation of benefits was provided, so insurer payments can't be verified.",
            [],
        ))
    else:
        when = "" if _blank(eob.processed_date) else f" processed {eob.processed_date.strip()}"
        rows.append(_row(
            "eob", "approve",
            f"EOB provided by {eob.payer}{when}.",
            _evidence(case, ["eob.processed_date", "eob.patient_resp_total_cents"]),
        ))

    if _blank(bill.statement_date):
        neighbors = ["bill.account_no", "bill.total_charges_cents", "bill.patient_balance_cents"]
        if bill.lines:
            neighbors.append(f"bill.lines.{bill.lines[0].line_no}.*")
        rows.append(_row(
            "bill.statement_date", "missing",
            "The bill has no statement date.",
            _evidence(case, neighbors, skip={"bill.statement_date"}),
        ))

    for line in bill.lines:
        label = f"Bill line {line.line_no} ({_shown_code(line.code)})"
        prefix = f"bill.lines.{line.line_no}."
        if _blank(line.dos):
            rows.append(_row(
                f"bill.lines.{line.line_no}.dos", "missing",
                f"{label} has no date of service.",
                _evidence(case, [prefix + "*"], skip={prefix + "dos"}),
            ))
        if line.units <= 0:
            rows.append(_row(
                f"bill.lines.{line.line_no}.units", "missing",
                f"{label} does not list a positive number of units.",
                _evidence(case, [prefix + "*"], skip={prefix + "units"}),
            ))
        if (line.code or "").strip() in {"?", ""}:
            rows.append(_row(
                f"bill.lines.{line.line_no}.code", "missing",
                f"{label} has no procedure code.",
                _evidence(case, [prefix + "*"], skip={prefix + "code"}),
            ))

    if _blank(case.provider.npi):
        rows.append(_row(
            "provider.npi", "missing",
            "The provider identifier is missing, so network status can't be verified and disputes can't be submitted.",
            _evidence(case, ["provider.name", "provider.address", "provider.in_network"]),
        ))

    if eob is not None and _blank(eob.processed_date):
        neighbors = ["eob.patient_resp_total_cents", "eob.claim_id"]
        if eob.lines:
            neighbors.append(f"eob.lines.{eob.lines[0].line_no}.*")
        rows.append(_row(
            "eob.processed_date", "missing",
            "The explanation of benefits has no processed date.",
            _evidence(case, neighbors, skip={"eob.processed_date"}),
        ))

    if eob is not None:
        eob_nos = {eline.line_no for eline in eob.lines}
        for eline in eob.lines:
            code = _shown_code(eline.code)
            item = f"eob line {eline.line_no} ({code})"
            prefix = f"eob.lines.{eline.line_no}."
            bline = bill_by_no.get(eline.line_no)
            if bline is None:
                rows.append(_row(
                    item, "reject",
                    f"EOB line {eline.line_no} ({code}) has no matching line on the bill.",
                    _evidence(case, [prefix + "*"]),
                ))
            elif eline.billed_cents != bline.charge_cents:
                bill_code = _shown_code(bline.code)
                rows.append(_row(
                    f"bill line {bline.line_no} ({bill_code})", "review",
                    f"The explanation of benefits lists {_usd(eline.billed_cents)} billed for line "
                    f"{bline.line_no} ({bill_code}) and the bill charges {_usd(bline.charge_cents)}.",
                    _evidence(case, [
                        f"bill.lines.{bline.line_no}.charge_cents",
                        f"eob.lines.{eline.line_no}.billed_cents",
                        f"bill.lines.{bline.line_no}.*",
                        prefix + "*",
                    ]),
                ))
            if eline.patient_resp_cents > eline.billed_cents:
                rows.append(_row(
                    item, "reject",
                    f"Patient responsibility of {_usd(eline.patient_resp_cents)} on EOB line "
                    f"{eline.line_no} ({code}) is higher than the billed amount of {_usd(eline.billed_cents)}.",
                    _evidence(case, [
                        f"eob.lines.{eline.line_no}.patient_resp_cents",
                        f"eob.lines.{eline.line_no}.billed_cents",
                        prefix + "*",
                    ]),
                ))
        for bline in bill.lines:
            if bline.line_no in eob_nos:
                continue
            code = _shown_code(bline.code)
            rows.append(_row(
                f"bill line {bline.line_no} ({code})", "review",
                f"Bill line {bline.line_no} ({code}) is not on the EOB — insurer may not have processed it.",
                _evidence(case, [f"bill.lines.{bline.line_no}.*"]),
            ))
        totals = ["bill.patient_balance_cents", "eob.patient_resp_total_cents"]
        if bill.patient_balance_cents == eob.patient_resp_total_cents:
            rows.append(_row(
                "patient balance", "approve",
                f"The bill's patient balance of {_usd(bill.patient_balance_cents)} matches the explanation of benefits.",
                _evidence(case, totals),
            ))
        else:
            rows.append(_row(
                "patient balance", "review",
                f"The bill's patient balance is {_usd(bill.patient_balance_cents)} and the explanation of "
                f"benefits lists patient responsibility of {_usd(eob.patient_resp_total_cents)}.",
                _evidence(case, totals),
            ))

    denial = case.denial
    if denial is not None:
        carc = (denial.carc or "").strip()
        if carc == "" or carc.upper() == "CO-?":
            rows.append(_row(
                "denial.carc", "missing",
                "The denial letter is missing its reason code.",
                _evidence(case, ["denial.reason_text", "denial.letter_date", "denial.category"], skip={"denial.carc"}),
            ))
        if _blank(denial.appeal_deadline):
            rows.append(_row(
                "denial.appeal_deadline", "review",
                "The appeal deadline is unknown, so the appeal window can't be computed.",
                _evidence(case, ["denial.letter_date", "denial.carc", "denial.reason_text"], skip={"denial.appeal_deadline"}),
            ))

    if _blank(case.patient.dob) and _blank(case.patient.member_id):
        rows.append(_row(
            "patient.identity", "review",
            "The patient's date of birth and member ID are both missing, so identity is hard to confirm.",
            _evidence(case, ["patient.patient_id", "patient.display_name"]),
        ))

    rows.sort(key=lambda row: _RANK[row["status"]])
    return rows
