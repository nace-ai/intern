"""Deterministic labels. Reads the case's stored reference rows, not live memory of CMS."""
from __future__ import annotations

from billproof.rules.money import pct_of
from billproof.synth.hard.rulespec import (
    DISTINCT_PHRASE, DUP_SAVERS, FACILITY_POS, LIABLE, NCCI_MODS, SAME_PHRASE, WRITEOFF,
)


def _eob_map(record: dict) -> dict[int, dict]:
    return {int(row["line_no"]): row for row in record["eobs"]}


def _mue(record: dict, code: str) -> tuple[int, int] | None:
    row = (record.get("reference") or {}).get("mue", {}).get(code)
    if not row:
        return None
    return int(row["limit"]), int(row["mai"])


def _fee(record: dict, code: str, facility: bool) -> int | None:
    row = (record.get("reference") or {}).get("fees", {}).get(code)
    if not row:
        return None
    key = "facility_cents" if facility else "non_facility_cents"
    if row.get(key) is None:
        return None
    return int(row[key])


def _ncci_index(record: dict) -> dict[tuple[str, str], int]:
    out = {}
    for row in (record.get("reference") or {}).get("ncci", []):
        out[(row["col1"], row["col2"])] = int(row["modifier_indicator"])
    return out


def duplicate_yes(lines: list[dict], line: dict) -> bool:
    prior = [a for a in lines if a["line_no"] < line["line_no"] and a["code"] == line["code"]
             and a["dos"] == line["dos"] and a["rendering_provider"] == line["rendering_provider"]]
    if not prior:
        return False
    other = max(prior, key=lambda a: a["line_no"])
    mods = set(other["modifiers"]) | set(line["modifiers"])
    sides = ("RT" in mods and "LT" in mods and set(other["modifiers"]) != set(line["modifiers"]))
    allowed = bool(mods & DUP_SAVERS) or sides
    return not allowed


def mue_yes(lines: list[dict], line: dict, record: dict) -> bool:
    limit = _mue(record, line["code"])
    if limit is None:
        return False
    cap, mai = limit
    if mai == 1:
        return int(line["units"]) > cap
    peers = [a for a in lines if a["code"] == line["code"] and a["dos"] == line["dos"]
             and a["rendering_provider"] == line["rendering_provider"]]
    return sum(int(a["units"]) for a in peers) > cap


def _distinct(remark: str) -> bool:
    text = remark or ""
    return DISTINCT_PHRASE in text and SAME_PHRASE not in text


def ncci_yes(lines: list[dict], line: dict, eob: dict | None, record: dict) -> bool:
    index = _ncci_index(record)
    remark = (eob or {}).get("remark") or ""
    for other in lines:
        if other["line_no"] == line["line_no"]:
            continue
        if other["dos"] != line["dos"] or other["rendering_provider"] != line["rendering_provider"]:
            continue
        indicator = index.get((other["code"], line["code"]))
        if indicator is None or indicator == 9:
            continue
        if indicator == 0:
            return True
        if indicator == 1:
            saved = bool(set(line["modifiers"]) & NCCI_MODS) and _distinct(remark)
            if not saved:
                return True
    return False


def fee_yes(line: dict, record: dict) -> bool:
    facility = line["pos"] in FACILITY_POS
    fee = _fee(record, line["code"], facility)
    units = int(line["units"])
    if fee is None or units <= 0:
        return False
    threshold = fee * units * 150 // 100
    return int(line["charge_cents"]) > threshold


def eob_yes(line: dict, eob: dict | None) -> bool:
    if eob is None:
        return True
    return (eob["code"] != line["code"] or int(eob["units"]) != int(line["units"])
            or int(eob["billed_cents"]) != int(line["charge_cents"]))


def _copay_line_no(lines: list[dict], eobs: dict[int, dict], plan: dict) -> int | None:
    if int(plan.get("copay_cents") or 0) <= 0:
        return None
    blocked = WRITEOFF | LIABLE
    found = []
    for line in lines:
        eob = eobs.get(int(line["line_no"]))
        if eob is None or not str(line["code"]).startswith("992"):
            continue
        if set(eob.get("carc") or []) & blocked:
            continue
        found.append(int(line["line_no"]))
    return min(found) if found else None


def correct_pr(record: dict) -> dict[int, int | None]:
    """Per-line correct patient responsibility. None when that line has no eob row."""
    lines = sorted(record["lines"], key=lambda a: int(a["line_no"]))
    eobs = _eob_map(record)
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
        n = int(line["line_no"])
        eob = eobs.get(n)
        if eob is None:
            out[n] = None
            continue
        carc = set(eob.get("carc") or [])
        billed = int(line["charge_cents"])
        if carc & WRITEOFF:
            out[n] = 0
            continue
        if carc & LIABLE:
            owe = min(billed, oop)
            oop -= owe
            out[n] = owe
            continue
        allowed = int(eob["allowed_cents"])
        if copay_at == n and copay_left > 0:
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
            balance = max(0, billed - allowed)
        out[n] = cost + balance
    return out


def pr_yes(line: dict, eob: dict | None, computed: int | None) -> bool:
    if eob is None or computed is None:
        return False
    return int(eob["patient_resp_cents"]) == int(computed)


def bill_balance_yes(record: dict, computed: dict[int, int | None]) -> bool:
    if any(value is None for value in computed.values()):
        return False
    if len(computed) != len(record["lines"]):
        return False
    return int(record["bill"]["patient_balance_cents"]) == sum(int(v) for v in computed.values())


def answers(record: dict) -> dict[str, str]:
    lines = list(record["lines"])
    eobs = _eob_map(record)
    computed = correct_pr(record)
    gold: dict[str, str] = {}
    for line in lines:
        n = int(line["line_no"])
        eob = eobs.get(n)
        gold[f"dup_L{n}"] = "yes" if duplicate_yes(lines, line) else "no"
        gold[f"mue_L{n}"] = "yes" if mue_yes(lines, line, record) else "no"
        gold[f"ncci_L{n}"] = "yes" if ncci_yes(lines, line, eob, record) else "no"
        gold[f"fee_L{n}"] = "yes" if fee_yes(line, record) else "no"
        gold[f"eob_L{n}"] = "yes" if eob_yes(line, eob) else "no"
        gold[f"pr_L{n}"] = "yes" if pr_yes(line, eob, computed[n]) else "no"
    gold["bill_balance"] = "yes" if bill_balance_yes(record, computed) else "no"
    return gold


def family_counts(gold: dict[str, str]) -> dict[str, dict[str, int]]:
    buckets: dict[str, dict[str, int]] = {}
    for name, answer in gold.items():
        family = name.rsplit("_L", 1)[0]
        row = buckets.setdefault(family, {"yes": 0, "no": 0, "n": 0})
        row[answer] += 1
        row["n"] += 1
    return buckets
