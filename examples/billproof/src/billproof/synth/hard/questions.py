"""Model-visible state and typed questions. Gold never enters the state."""
from __future__ import annotations

from billproof.schema import Question
from billproof.synth.hard.rulespec import RULES

_FAMILY = {
    "dup": "Line {n}: unsupported duplicate under rules DUP? Answer yes or no.",
    "mue": "Line {n}: units over the MUE limit under rules MUE? Answer yes or no.",
    "ncci": "Line {n}: NCCI column-2 conflict under rules NCCI? Answer yes or no.",
    "fee": "Line {n}: charge over 150% of the reference fee under rules FEE? Answer yes or no.",
    "eob": "Line {n}: EOB code, units, or billed cents mismatch under rules EOB? Answer yes or no.",
    "pr": "Line {n}: is eob patient_resp_cents the correct patient responsibility under rules PR? Answer yes or no.",
}
_BILL = ("Does bill.patient_balance_cents equal the sum of correct per-line patient responsibilities "
         "under rules BILL, with an eob line for every bill line? Answer yes or no.")


def questions_for(record: dict) -> list[Question]:
    built: list[Question] = []
    for line in record["lines"]:
        n = int(line["line_no"])
        for family, template in _FAMILY.items():
            built.append(Question(
                name=f"{family}_L{n}", type="noul", family=family, line_no=n,
                instructions=template.format(n=n),
            ))
    built.append(Question(name="bill_balance", type="noul", family="bill_balance", instructions=_BILL))
    return built


def visible_state(record: dict) -> dict:
    """Same object for every lane. No gold, no family_counts, no planted-error flags."""
    lines = []
    for row in record["lines"]:
        lines.append({
            "line_no": row["line_no"],
            "dos": row["dos"],
            "code": row["code"],
            "modifiers": list(row["modifiers"]),
            "units": row["units"],
            "charge_cents": row["charge_cents"],
            "pos": row["pos"],
            "rendering_provider": row["rendering_provider"],
            "label": row.get("label_shown") or row.get("label") or "",
            "comment": row.get("comment_shown") or "",
        })
    eobs = []
    for row in record["eobs"]:
        eobs.append({
            "line_no": row["line_no"],
            "code": row["code"],
            "units": row["units"],
            "billed_cents": row["billed_cents"],
            "allowed_cents": row["allowed_cents"],
            "plan_paid_cents": row["plan_paid_cents"],
            "patient_resp_cents": row["patient_resp_cents"],
            "carc": list(row["carc"]),
            "remark": row.get("remark") or "",
        })
    return {
        "rules": RULES,
        "context": "Answer every question from rules, plan, reference, bill, and eob. Money is integer cents.",
        "plan": record["plan"],
        "reference": record["reference"],
        "bill": {
            "account_no": record["bill"]["account_no"],
            "statement_date": record["bill"]["statement_date"],
            "total_charges_cents": record["bill"]["total_charges_cents"],
            "patient_balance_cents": record["bill"]["patient_balance_cents"],
            "lines": lines,
        },
        "eob": {
            "payer": record["eob_header"]["payer"],
            "claim_id": record["eob_header"]["claim_id"],
            "processed_date": record["eob_header"]["processed_date"],
            "patient_resp_total_cents": record["eob_header"]["patient_resp_total_cents"],
            "lines": eobs,
        },
    }
