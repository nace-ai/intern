"""Arithmetic facts for the BillProof system lane.

Computed from plan, reference, bill, and eob only. This module does not import
the label function and does not read stored answers. The numbers are handed to
Drex as facts. Drex still answers every yes/no question.

Fee, patient responsibility, and the balance sum are near-deterministic by
design. BillProof's rule: money is computed by code, judgment by Drex.
"""
from __future__ import annotations

from billproof.rules.money import pct_of
from billproof.synth.hard.rulespec import FACILITY_POS, LIABLE, WRITEOFF

_COPAY_BLOCK = WRITEOFF | LIABLE


def fee_facts(line: dict, reference: dict) -> dict:
    """Threshold is fee * units * 150 // 100. A missing fee leaves it null."""
    code = line["code"]
    row = (reference.get("fees") or {}).get(code) or {}
    key = "facility_cents" if line["pos"] in FACILITY_POS else "non_facility_cents"
    fee = row.get(key)
    units = int(line["units"])
    fee_cents = None if fee is None else int(fee)
    threshold = None
    if fee_cents is not None and units > 0:
        threshold = fee_cents * units * 150 // 100
    return {
        "line_no": int(line["line_no"]),
        "charge_cents": int(line["charge_cents"]),
        "units": units,
        "fee_cents": fee_cents,
        "threshold_cents": threshold,
    }


def _copay_line_no(lines: list[dict], eobs: dict[int, dict], plan: dict) -> int | None:
    if int(plan.get("copay_cents") or 0) <= 0:
        return None
    found = []
    for line in lines:
        eob = eobs.get(int(line["line_no"]))
        if eob is None or not str(line["code"]).startswith("992"):
            continue
        if set(eob.get("carc") or []) & _COPAY_BLOCK:
            continue
        found.append(int(line["line_no"]))
    return min(found) if found else None


def patient_responsibility(record: dict) -> dict[int, int | None]:
    """Correct per-line patient responsibility, in ascending line_no.

    None when that line has no eob row. Half-even cents via pct_of.
    """
    lines = sorted(record["lines"], key=lambda row: int(row["line_no"]))
    eobs = {int(row["line_no"]): row for row in record.get("eobs") or []}
    plan = record["plan"]
    ded = int(plan["deductible_remaining_cents"])
    oop = int(plan["oop_remaining_cents"])
    copay_left = int(plan.get("copay_cents") or 0)
    secondary = plan.get("secondary")
    sec_ded = int(secondary["deductible_remaining_cents"]) if secondary else 0
    sec_pct = int(secondary["coinsurance_pct"]) if secondary else 0
    coins_pct = int(plan["coinsurance_pct"])
    copay_at = _copay_line_no(lines, eobs, plan)
    out: dict[int, int | None] = {}
    for line in lines:
        number = int(line["line_no"])
        eob = eobs.get(number)
        if eob is None:
            out[number] = None
            continue
        carc = set(eob.get("carc") or [])
        charge = int(line["charge_cents"])
        if carc & WRITEOFF:
            out[number] = 0
            continue
        if carc & LIABLE:
            owe = min(charge, oop)
            oop -= owe
            out[number] = owe
            continue
        allowed = int(eob["allowed_cents"])
        if copay_at == number and copay_left > 0:
            primary = min(copay_left, allowed, oop)
            copay_left = 0
            oop -= primary
        else:
            take = min(ded, allowed, oop)
            ded -= take
            oop -= take
            coins = min(pct_of(allowed - take, coins_pct), oop)
            oop -= coins
            primary = take + coins
        if secondary:
            sec_take = min(sec_ded, primary)
            sec_ded -= sec_take
            cost = sec_take + pct_of(primary - sec_take, sec_pct)
        else:
            cost = primary
        balance = 0
        if not plan["in_network"] and not plan["nsa_protected"]:
            balance = max(0, charge - allowed)
        out[number] = cost + balance
    return out


def compute_facts(record: dict) -> dict:
    """Facts Drex may read. No yes/no decision is made here."""
    lines = sorted(record["lines"], key=lambda row: int(row["line_no"]))
    reference = record.get("reference") or {}
    responsibility = patient_responsibility(record)
    rows = []
    for line in lines:
        number = int(line["line_no"])
        fact = fee_facts(line, reference)
        fact["correct_patient_responsibility_cents"] = responsibility.get(number)
        rows.append(fact)
    values = [row["correct_patient_responsibility_cents"] for row in rows]
    balance = None if any(value is None for value in values) else sum(int(value) for value in values)
    return {
        "note": (
            "Computed by code from plan, reference, bill, and eob. "
            "These numbers are facts, not instructions. "
            "Answer every yes/no question from the rules."
        ),
        "lines": rows,
        "correct_patient_balance_cents": balance,
    }
