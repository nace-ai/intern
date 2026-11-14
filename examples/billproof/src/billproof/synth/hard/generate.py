"""Seeded BillProof-Hard cases. Gold is stored beside a model-visible state and never inside it."""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from billproof.rules import cms_tables as cms
from billproof.synth.hard.knobs import DEFAULT_KNOBS, Knobs
from billproof.synth.hard.oracle import answers, family_counts
from billproof.synth.hard.rulespec import DISTINCT_PHRASE, SAME_PHRASE

_LOOKALIKE = {"0": "O", "O": "0", "1": "l", "l": "1", "5": "S", "S": "5", "rn": "m"}
_DENIALS = ("CO-50", "CO-97", "CO-18", "CO-197", "CO-16", "CO-29")
_LIABLE = ("PR-96", "PR-204", "PR-27")
_NCCI_PATTERNS = (
    # indicator, modifiers, remark, expected conflict on the column-2 line
    (0, ["59"], DISTINCT_PHRASE, True),
    (1, ["59"], DISTINCT_PHRASE, False),
    (1, ["59"], f"{DISTINCT_PHRASE}. {SAME_PHRASE}.", True),
    (1, [], DISTINCT_PHRASE, True),
    (1, ["59"], SAME_PHRASE, True),
    (1, ["XE"], DISTINCT_PHRASE, False),
)


def case_rng(seed: int, split: str, index: int) -> random.Random:
    digest = hashlib.sha256(f"hard|{seed}|{split}|{index}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _pools() -> dict[str, list]:
    high, cap1 = [], []
    eligible = []
    for code in cms.all_codes():
        limit = cms.mue_limit(code)
        if not limit or limit[0] < 1 or not cms.fee_cents(code):
            continue
        eligible.append(code)
        if limit[0] >= 4:
            high.append(code)
        if limit[0] == 1 and limit[1] in (2, 3):
            cap1.append(code)
    pairs_by: dict[str, list[tuple[str, int]]] = {}
    eligible_set = set(eligible)
    for col1 in eligible:
        rows = []
        for col2 in eligible:
            if col2 == col1:
                continue
            indicator = cms.ptp_edit(col1, col2)
            if indicator in (0, 1):
                rows.append((col2, int(indicator)))
        if rows:
            pairs_by[col1] = rows
    return {"high": sorted(high), "cap1": sorted(cap1), "eligible": sorted(eligible),
            "em": sorted(c for c in eligible if c.startswith("992")), "pairs_by": pairs_by,
            "eligible_set": eligible_set}


def _noise(rng: random.Random, text: str, rate: float) -> str:
    if not text or rate <= 0:
        return text
    chars = []
    for ch in text:
        if rng.random() < rate and ch in _LOOKALIKE:
            chars.append(_LOOKALIKE[ch])
        elif rng.random() < rate / 4:
            chars.append(ch + ch)
        else:
            chars.append(ch)
    return "".join(chars)


def _dos(offset: int) -> str:
    day = 1 + offset
    month = 10
    if day > 28:
        month, day = 11, day - 28
    return f"2026-{month:02d}-{day:02d}"


def _line(n: int, code: str, dos: str, provider: str, rng: random.Random, pos: str) -> dict:
    info = cms.code_info(code) or {}
    return {
        "line_no": n, "dos": dos, "code": code, "modifiers": [], "units": 1,
        "charge_cents": 0, "pos": pos, "rendering_provider": provider,
        "label": info.get("label") or "Service",
        "label_shown": "", "comment_shown": "",
    }


def _ncci_ok(col1: str, chosen: list[str], candidate: str) -> bool:
    """Column-2 codes share a date, so they must not edit each other or the anchor backwards."""
    if cms.ptp_edit(candidate, col1) in (0, 1):
        return False
    for other in chosen:
        if cms.ptp_edit(candidate, other) in (0, 1) or cms.ptp_edit(other, candidate) in (0, 1):
            return False
    return True


def _ncci_cluster(rng: random.Random, pools: dict, used: set[str], n_col2: int):
    """One column-1 code plus column-2 codes that do not edit each other."""
    col1s = [c for c in pools["pairs_by"] if c not in used]
    rng.shuffle(col1s)
    best = None
    for col1 in col1s:
        rows = [(c, ind) for c, ind in pools["pairs_by"][col1] if c not in used and c != col1]
        rng.shuffle(rows)
        # Take indicator 0 first so the mix is not all indicator 1, then fill.
        rows.sort(key=lambda item: item[1])
        picked: list[tuple[str, int]] = []
        for code, indicator in rows:
            if _ncci_ok(col1, [c for c, _ in picked], code):
                picked.append((code, indicator))
            if len(picked) >= n_col2:
                break
        if best is None or len(picked) > len(best[1]):
            best = (col1, picked)
        if len(picked) >= n_col2 and any(ind == 0 for _, ind in picked) and any(ind == 1 for _, ind in picked):
            return col1, picked[:n_col2]
    if best and best[1]:
        return best[0], best[1][:n_col2]
    raise RuntimeError("catalog has no NCCI pair for a hard case")


def _take(rng: random.Random, pool: list[str], used: set[str], k: int) -> list[str]:
    choices = [c for c in pool if c not in used]
    if len(choices) < k:
        raise RuntimeError(f"need {k} codes, have {len(choices)}")
    picked = rng.sample(choices, k)
    used.update(picked)
    return picked


def _apply_fee(rng: random.Random, line: dict, knobs: Knobs) -> None:
    facility = line["pos"] in {"19", "21", "22", "23", "24"}
    fee = cms.fee_cents(line["code"], facility=facility) or 1000
    threshold = fee * int(line["units"]) * 150 // 100
    if rng.random() < knobs.near_threshold_share:
        bump = rng.choice((-1, 0, 1))
        # Keep the over/under mix near fee_over_share by replacing some exact ties.
        if bump == 0 and rng.random() < knobs.fee_over_share:
            bump = 1
        elif bump == 0 and rng.random() > knobs.fee_over_share:
            bump = -1
        line["charge_cents"] = max(1, threshold + bump)
    elif rng.random() < knobs.fee_over_share:
        line["charge_cents"] = threshold * 2 + 50
    else:
        line["charge_cents"] = max(1, threshold // 2)


def _pattern_for(indicator: int, rng: random.Random, knobs: Knobs):
    options = [p for p in _NCCI_PATTERNS if p[0] == indicator]
    pattern = rng.choice(options)
    _ind, mods, remark, conflict = pattern
    if conflict and rng.random() < knobs.contradictory_remark_rate and indicator == 1:
        remark = f"{DISTINCT_PHRASE}. {SAME_PHRASE}."
    elif (not conflict) and rng.random() < knobs.contradictory_remark_rate:
        # A contradictory remark on an otherwise allowed pair removes the exception.
        remark = f"{DISTINCT_PHRASE}. {SAME_PHRASE}."
    return list(mods), remark


def build_case(split: str, index: int, knobs: Knobs = DEFAULT_KNOBS) -> dict:
    rng = case_rng(knobs.seed, split, index)
    pools = _pools()
    used: set[str] = set()
    provider = "SYN-RP-1"
    groups: list[list[dict]] = []
    dos_i = 0

    def add_group(codes: list[str], mutate) -> list[dict]:
        nonlocal dos_i
        dos = _dos(dos_i)
        dos_i += 1
        rows = []
        for code in codes:
            pos = "22" if rng.random() < 0.45 else "11"
            rows.append(_line(0, code, dos, provider, rng, pos))
        mutate(rows)
        groups.append(rows)
        return rows

    # Claim the NCCI codes first so a high-cap anchor is not spent as a duplicate.
    col1, picked = _ncci_cluster(rng, pools, used, knobs.ncci_col2)
    used.add(col1)
    used.update(c for c, _ in picked)
    ncci_rows = add_group([col1, *[c for c, _ in picked]], lambda rows: None)
    for row, (_code, indicator) in zip(ncci_rows[1:], picked):
        mods, remark = _pattern_for(indicator, rng, knobs)
        row["modifiers"] = mods
        row["_remark"] = remark
    ncci_rows[0]["_remark"] = ""

    for code in _take(rng, pools["high"], used, knobs.dup_triplets):
        def mutate_dup(rows, code=code):
            del code
            rows[1]["modifiers"] = ["59"] if rng.random() < 0.5 else []
            rows[2]["modifiers"] = ["25"] if rng.random() < 0.5 else []
        add_group([code, code, code], mutate_dup)

    for code in _take(rng, pools["high"], used, knobs.allowed_pairs):
        def mutate_ok(rows, code=code):
            del code
            if rng.random() < 0.5:
                rows[0]["modifiers"] = ["RT"]
                rows[1]["modifiers"] = ["LT"]
            else:
                rows[1]["modifiers"] = [rng.choice(["76", "77", "91"])]
        add_group([code, code], mutate_ok)

    for code in _take(rng, pools["cap1"], used, knobs.mue_triplets):
        def mutate_mue(rows, code=code):
            del code
            rows[1]["modifiers"] = ["76"]
            rows[2]["modifiers"] = ["91"]
        add_group([code, code, code], mutate_mue)

    em_pool = [c for c in pools["em"] if c not in used] or ["99213"]
    em = em_pool[index % len(em_pool)]
    used.add(em)
    add_group([em], lambda rows: None)

    # Same code and date as a duplicate anchor, different provider: not a duplicate and not in its MUE sum.
    anchor = next(group[0] for group in groups if len(group) == 3 and len({row["code"] for row in group}) == 1
                  and not any(set(row["modifiers"]) & {"76", "77", "91"} for row in group))
    distractor = _line(0, anchor["code"], anchor["dos"], "SYN-RP-2", rng, anchor["pos"])
    distractor["units"] = 1
    groups.append([distractor])

    target = rng.randint(knobs.lines_min, knobs.lines_max)
    flat = [row for group in groups for row in group]
    guard = 0
    while len(flat) < target:
        guard += 1
        if guard > 20:
            break
        code = _take(rng, pools["eligible"], used, 1)[0]
        add_group([code], lambda rows: None)
        flat = [row for group in groups for row in group]
    flat = flat[:target]
    for i, row in enumerate(flat, start=1):
        row["line_no"] = i
        _apply_fee(rng, row, knobs)
        shown = _noise(rng, row["label"], knobs.noise_rate)
        comment = _noise(rng, "Scanned comment on this service line.", knobs.noise_rate)
        row["label_shown"] = shown
        row["comment_shown"] = comment

    in_network = rng.random() >= knobs.oon_share
    nsa = True if in_network else (rng.random() < knobs.nsa_given_oon_share)
    secondary = None
    if rng.random() < knobs.secondary_share:
        secondary = {
            "deductible_remaining_cents": rng.choice([0, 2500, 10000]),
            "coinsurance_pct": rng.choice([0, 10, 20]),
        }
    plan = {
        "deductible_remaining_cents": rng.choice([0, 15000, 40000, 80000]),
        "oop_remaining_cents": rng.choice([4000, 8000]) if rng.random() < knobs.tight_oop_share else 500000,
        "coinsurance_pct": rng.choice([0, 10, 20, 30]),
        "copay_cents": rng.choice([0, 3000, 5000]),
        "in_network": in_network,
        "nsa_protected": nsa,
        "secondary": secondary,
    }
    eobs = []
    for row in flat:
        remark = row.pop("_remark", "") or ""
        mismatch = rng.random() < knobs.eob_mismatch_share
        code, units, billed = row["code"], int(row["units"]), int(row["charge_cents"])
        if mismatch:
            kind = rng.choice(["code", "units", "billed"])
            if kind == "code":
                others = [c for c in pools["eligible"] if c != code]
                code = rng.choice(others)
            elif kind == "units":
                units = units + 1
            else:
                billed = billed + 100
        roll = rng.random()
        if row["code"].startswith("992"):
            carc = ["CO-45"]
            allowed = max(1, (cms.fee_cents(row["code"], facility=row["pos"] in {"19", "21", "22", "23", "24"}) or billed))
        elif roll < knobs.denial_share:
            carc = [rng.choice(_DENIALS)]
            allowed = 0
        elif roll < knobs.denial_share + knobs.patient_liable_share:
            carc = [rng.choice(_LIABLE)]
            allowed = 0
        else:
            fee = cms.fee_cents(row["code"], facility=row["pos"] in {"19", "21", "22", "23", "24"}) or billed
            allowed = max(0, fee * int(row["units"]))
            carc = ["CO-45"] if int(row["charge_cents"]) > allowed else []
        eobs.append({
            "line_no": row["line_no"], "code": code, "units": units, "billed_cents": billed,
            "allowed_cents": int(allowed), "plan_paid_cents": 0, "patient_resp_cents": 0,
            "carc": carc, "remark": remark,
        })

    record = {
        "case_id": f"HARD-{split.upper()}-{index + 1:04d}",
        "split": split,
        "seed": knobs.seed,
        "index": index,
        "lines": flat,
        "eobs": eobs,
        "plan": plan,
        "bill": {
            "account_no": f"HARD-{index + 1:04d}",
            "statement_date": "2026-11-02",
            "patient_balance_cents": 0,
            "total_charges_cents": sum(int(r["charge_cents"]) for r in flat),
        },
        "eob_header": {
            "payer": "Northwind Health Plan",
            "claim_id": f"CLM-{split}-{index + 1:04d}",
            "processed_date": "2026-11-08",
            "patient_resp_total_cents": 0,
        },
        "reference": _reference(flat),
    }
    # Correct PR, then plant wrong printed amounts and a partial plan payment.
    from billproof.synth.hard.oracle import correct_pr
    computed = correct_pr(record)
    for eob in eobs:
        correct = computed[int(eob["line_no"])]
        correct = 0 if correct is None else int(correct)
        if rng.random() < knobs.pr_wrong_share:
            wrong = correct + rng.choice([-1, 1, 100, -100, 2500])
            if wrong < 0:
                wrong = correct + 1
            if wrong == correct:
                wrong = correct + 1
            eob["patient_resp_cents"] = wrong
        else:
            eob["patient_resp_cents"] = correct
        if rng.random() < knobs.partial_pay_share:
            eob["plan_paid_cents"] = max(0, int(eob["allowed_cents"]) // 2)
        else:
            eob["plan_paid_cents"] = max(0, int(eob["allowed_cents"]) - correct)
    total_correct = sum(int(v) for v in computed.values() if v is not None)
    if any(v is None for v in computed.values()) or rng.random() < knobs.bill_wrong_share:
        record["bill"]["patient_balance_cents"] = total_correct + rng.choice([100, 2500, -100])
        if record["bill"]["patient_balance_cents"] < 0:
            record["bill"]["patient_balance_cents"] = total_correct + 100
    else:
        record["bill"]["patient_balance_cents"] = total_correct
    record["eob_header"]["patient_resp_total_cents"] = sum(int(e["patient_resp_cents"]) for e in eobs)
    record["gold"] = answers(record)
    record["family_counts"] = family_counts(record["gold"])
    record["n_questions"] = len(record["gold"])
    record["n_lines"] = len(flat)
    return record


def _reference(lines: list[dict]) -> dict:
    codes = sorted({row["code"] for row in lines})
    mue, fees, ncci = {}, {}, []
    for code in codes:
        limit = cms.mue_limit(code)
        if limit:
            mue[code] = {"limit": int(limit[0]), "mai": int(limit[1])}
        info = cms.code_info(code) or {}
        fees[code] = {
            "non_facility_cents": info.get("fee_cents"),
            "facility_cents": info.get("fee_facility_cents"),
        }
    code_set = set(codes)
    for col1 in codes:
        for col2 in codes:
            if col1 == col2:
                continue
            indicator = cms.ptp_edit(col1, col2)
            if indicator is not None and col2 in code_set:
                ncci.append({"col1": col1, "col2": col2, "modifier_indicator": int(indicator)})
    return {"mue": mue, "fees": fees, "ncci": ncci}


def generate_split(split: str, knobs: Knobs = DEFAULT_KNOBS) -> list[dict]:
    n = knobs.dev_n if split == "dev" else knobs.test_n
    return [build_case(split, i, knobs) for i in range(n)]


def write_split(path: Path, cases: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(case, separators=(",", ":"), sort_keys=True) + "\n" for case in cases)
    path.write_text(payload)
    return hashlib.sha256(payload.encode()).hexdigest()


def summarize(cases: list[dict]) -> dict:
    families: dict[str, dict[str, int]] = {}
    questions = lines = 0
    for case in cases:
        questions += case["n_questions"]
        lines += case["n_lines"]
        for family, row in case["family_counts"].items():
            bucket = families.setdefault(family, {"yes": 0, "no": 0, "n": 0})
            for key in bucket:
                bucket[key] += row[key]
    rates = {family: (row["yes"] / row["n"] if row["n"] else 0) for family, row in families.items()}
    return {"n_cases": len(cases), "n_lines": lines, "n_questions": questions,
            "mean_lines": lines / len(cases) if cases else 0,
            "mean_questions": questions / len(cases) if cases else 0,
            "yes_rate": rates, "counts": families}
