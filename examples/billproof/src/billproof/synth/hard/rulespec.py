"""Closed rules for BillProof-Hard. The oracle and the model-visible text must match."""
from __future__ import annotations

DUP_SAVERS = frozenset({"76", "77", "91", "50"})
NCCI_MODS = frozenset({"59", "XE", "XS", "XP", "XU"})
WRITEOFF = frozenset({"CO-16", "CO-18", "CO-29", "CO-50", "CO-97", "CO-197"})
LIABLE = frozenset({"PR-27", "PR-96", "PR-204"})
FACILITY_POS = frozenset({"19", "21", "22", "23", "24"})
DISTINCT_PHRASE = "Distinct service documented"
SAME_PHRASE = "Same session"

RULES = """
Money is integer cents. Answer every question from this text plus plan, reference, bill, and eob. Labels and comments may contain OCR noise and are not inputs. plan_paid_cents is not an input. Do not substitute remembered CMS fees or edits.

DUP. Consider earlier lines (smaller line_no) with the same code, the same dos, and the same rendering_provider. If none, no. Otherwise take the latest of those lines. Let mods be the union of both lines' modifiers. The repeat is allowed when mods contain 76, 77, 91, or 50, or when RT is on one line and LT is on the other and the two modifier sets differ. yes only when a same-day same-provider repeat exists and is not allowed. Modifiers 59, XE, XS, XP, XU, and 25 do not allow a duplicate. Remarks do not allow a duplicate.

MUE. reference.mue[code] is {limit, mai}. If the code is absent, no. MAI 1 compares this line's units with limit. MAI 2 or 3 sums units over lines with the same code, dos, and rendering_provider, and compares that sum with limit. yes only when the compared number is strictly greater than limit. Units equal to the limit are no. A repeat modifier does not raise the limit.

NCCI. This line conflicts when another line shares its dos and rendering_provider and reference.ncci contains col1=that other code, col2=this code, modifier_indicator 0 or 1. A missing row or indicator 9 is not a conflict. Indicator 0 is always a conflict. Indicator 1 is a conflict unless this line's modifiers contain 59, XE, XS, XP, or XU and this line's eob remark contains the exact phrase "Distinct service documented" and does not contain the exact phrase "Same session". If both phrases appear, distinct work is not documented. yes if any such conflict exists.

FEE. Place-of-service 19, 21, 22, 23, and 24 use reference.fees facility_cents. Every other pos uses non_facility_cents. threshold = fee * units * 150 // 100 with integer floor division. yes only when charge_cents is strictly greater than threshold. Equal to threshold is no. A missing fee is no.

EOB. yes when no eob line has this line_no, or that eob line's code, units, or billed_cents differs from this bill line's code, units, or charge_cents. Patient responsibility and remarks do not decide EOB.

PR. Compute correct patient responsibility in ascending line_no. yes only when an eob line exists for this line_no and patient_resp_cents equals that integer.

PR procedure. Start deductible_remaining from plan.deductible_remaining_cents, oop_remaining from plan.oop_remaining_cents, and copay_remaining from plan.copay_cents. Coinsurance percent is plan.coinsurance_pct. Network and NSA flags are plan.in_network and plan.nsa_protected. If plan.secondary is not null, start secondary_deductible_remaining from plan.secondary.deductible_remaining_cents and use plan.secondary.coinsurance_pct. The copay line is the lowest line_no whose code starts with 992, that has an eob line, and whose carc contains none of CO-16, CO-18, CO-29, CO-50, CO-97, CO-197, PR-27, PR-96, PR-204. If copay_remaining is 0 there is no copay line.

Provider write-off codes: CO-16, CO-18, CO-29, CO-50, CO-97, CO-197. If carc contains any, correct patient responsibility is 0. The line consumes no deductible, copay, coinsurance, or out-of-pocket.

Patient-liable codes: PR-27, PR-96, PR-204. If carc contains any and no provider write-off, correct patient responsibility is min(charge_cents, oop_remaining), then subtract that from oop_remaining.

Otherwise the line is covered. allowed is eob.allowed_cents. If this is the copay line and copay_remaining is positive: primary = min(copay_remaining, allowed, oop_remaining), then set copay_remaining to 0 and subtract primary from oop_remaining. Do not also take deductible or coinsurance. Else: take = min(deductible_remaining, allowed, oop_remaining); subtract take from deductible_remaining and from oop_remaining; coinsurance = half_even((allowed - take) * coinsurance_pct / 100) then min with oop_remaining; subtract coinsurance from oop_remaining; primary = take + coinsurance.

half_even is round-half-to-even to the nearest cent. 1 cent at 50% is 0. 3 cents at 50% is 2.

If plan.secondary is present: sec_take = min(secondary_deductible_remaining, primary); subtract sec_take from secondary_deductible_remaining; patient_secondary = half_even((primary - sec_take) * secondary.coinsurance_pct / 100); cost-share = sec_take + patient_secondary. Do not restore oop_remaining when secondary pays. If there is no secondary, cost-share = primary.

Balance bill, covered lines only: if in_network is false and nsa_protected is false, add max(0, charge_cents - allowed). Otherwise add 0. Balance-bill dollars do not change oop_remaining. Correct patient responsibility = cost-share + balance bill.

BILL. bill_balance is yes only when every bill line has an eob line and bill.patient_balance_cents equals the sum of the correct per-line patient responsibilities.
""".strip()
