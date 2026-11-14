"""Practice-area roles: which kind of legal team is asking, and what that changes in a trial.

A role is picked at intake by Drex in one request. Drex choice questions hold at most 5 options, so the
pick has two levels: `practice_group` (3 options) and one `area_*` question per group (2 to 4 options),
all in the same request; the answer of the chosen group's question is the role.

A role sets five things:
- `scope`:   which LawWiki codes are searched for candidate statutes, per jurisdiction ("FS:83" is
             chapter 83 of the Florida Statutes; see engine/lawstore.py);
- `anchors`: the sections that team reaches for first, per jurisdiction (every key is checked against
             the local store by tests and by `python3 -m engine.roles --check`);
- `finding` and `burden`: what the jurors are asked ("liable" on a preponderance of the evidence, or
             "guilty" beyond a reasonable doubt);
- `lens`:    what that team needs from the answer, stated in the case brief every decision reads;
- `next_steps`: the 5 options of the `next_step` decision, in a fixed order: press the position,
             take the formal step, get the missing proof, settle, escalate.

`side` is the side that team is on when the case file does not say: 0 claimant, 1 defendant.
"""
from __future__ import annotations

import sys

from . import lawstore

CIVIL = {"finding": "liable", "burden": "a preponderance of the evidence"}
CRIME = {"finding": "guilty", "burden": "beyond a reasonable doubt"}
CRIME_SCOPE = {"CA": ["PEN", "EVID", "VEH", "HSC"], "NY": ["PEN", "CPL", "VAT"], "TX": ["PE", "CR"], "US": ["USC-18", "USC-21"],
               "FL": ["FS:775", "FS:776", "FS:777", "FS:784", "FS:810", "FS:812", "FS:893", "FS:316", "FS:90"]}

GROUPS = ["Business and property", "People and work", "Criminal"]

ROLES: dict[str, dict] = {
    "corporate": {
        "label": "Corporate and business", "group": 0, "side": 0, **CIVIL,
        "team": "in-house counsel or a business litigation team",
        "lens": "exposure in dollars, what the contract and the commercial code allow, and whether to sue, settle or cure",
        "scope": {"CA": ["CORP", "COM", "CIV", "BPC"], "NY": ["UCC", "BSC", "LLC", "PTR", "GBS", "GOB"], "TX": ["BC", "BO", "CP"],
                  "FL": ["FS:605", "FS:607", "FS:672", "FS:542", "FS:501"], "US": ["USC-15", "USC-11"]},
        "anchors": {"CA": "CIV/1550 CIV/3301 CORP/309 COM/2601 COM/2607 BPC/17200", "NY": "UCC/2-601 UCC/2-602 UCC/2-607 UCC/2-714 BSC/717 GOB/5-701",
                    "TX": "BC/2.601 BC/2.607 BO/21.223 CP/38.001 BC/17.46", "FL": "FS/672.601 FS/672.607 FS/607.0830 FS/605.04091 FS/542.335",
                    "US": "USC-15/1 USC-15/45 USC-11/362"},
        "next_steps": ["Send a notice of breach that cites the contract and the statute", "File suit now", "Preserve documents and gather the missing proof first",
                       "Negotiate a commercial settlement", "Escalate to outside counsel before acting"]},
    "ip": {
        "label": "Intellectual property", "group": 0, "side": 0, **CIVIL,
        "team": "an intellectual property team for a rights holder or an accused user",
        "lens": "ownership and registration, whether the use infringes or is excused, and which remedies are open",
        "scope": {"CA": ["BPC", "CIV"], "NY": ["GBS", "ACA", "CVR"], "TX": ["BC", "CP"], "FL": ["FS:495", "FS:688", "FS:540"],
                  "US": ["USC-17", "USC-35", "USC-15"]},
        "anchors": {"CA": "CIV/3426.1 CIV/3426.3 CIV/3344 BPC/14245 BPC/16600", "NY": "GBS/360-k GBS/360-l CVR/50 CVR/51",
                    "TX": "CP/134A.002 CP/134A.004 BC/16.102 BC/16.103", "FL": "FS/688.002 FS/688.004 FS/495.131 FS/495.151 FS/540.08",
                    "US": "USC-17/106 USC-17/107 USC-17/501 USC-17/504 USC-17/412 USC-35/271 USC-15/1114 USC-15/1125"},
        "next_steps": ["Send a cease-and-desist letter that cites the statute", "File an infringement action now", "Register the work and gather proof of copying first",
                       "Negotiate a license or settlement", "Get specialist counsel's review before acting"]},
    "real_estate": {
        "label": "Real estate and landlord-tenant", "group": 0, "side": 0, **CIVIL,
        "team": "a tenant, a landlord or a property manager's legal department",
        "lens": "notice dates and deadlines, what the lease and the statute require, and what the deposit or possession claim is worth",
        "scope": {"CA": ["CIV", "CCP", "BPC"], "NY": ["RPP", "RPA", "MDW", "GOB"], "TX": ["PR"],
                  "FL": ["FS:83", "FS:82", "FS:689", "FS:695", "FS:718", "FS:720"], "US": ["USC-42", "USC-12"]},
        "anchors": {"CA": "CIV/1950.5 CIV/1941.1 CIV/1942 CIV/1946.2 CCP/1161", "NY": "RPP/235-b GOB/7-103 GOB/7-108 RPA/711",
                    "TX": "PR/92.103 PR/92.109 PR/92.052 PR/92.0081 PR/24.005", "FL": "FS/83.49 FS/83.56 FS/83.51 FS/83.48 FS/83.60",
                    "US": "USC-42/3604 USC-42/3613 USC-12/2605"},
        "next_steps": ["Send a written demand that cites the statute", "File the claim now", "Gather the missing proof first",
                       "Negotiate a settlement", "Get a lawyer's review before acting"]},
    "consumer": {
        "label": "Consumer and small claims", "group": 0, "side": 0, **CIVIL,
        "team": "a consumer, often without a lawyer, or a consumer-protection clinic",
        "lens": "whether the claim fits small claims, what can be recovered, and the simplest next filing",
        "scope": {"CA": ["CIV", "CCP", "BPC"], "NY": ["GBS", "UCC", "CCA", "PEP"], "TX": ["BC", "CP", "FI"],
                  "FL": ["FS:501", "FS:559", "FS:34", "FS:672"], "US": ["USC-15"]},
        "anchors": {"CA": "CIV/1770 CIV/1793.2 BPC/17200 BPC/17500 CCP/116.220 CCP/116.221", "NY": "GBS/349 GBS/350 GBS/198-a UCC/2-314",
                    "TX": "BC/17.46 BC/17.50 FI/392.301 BC/2.314", "FL": "FS/501.204 FS/501.211 FS/559.72 FS/34.01",
                    "US": "USC-15/1692e USC-15/1681i USC-15/2310 USC-15/45"},
        "next_steps": ["Send a written demand that cites the statute", "File in small claims court now", "Gather receipts and the missing proof first",
                       "Negotiate a refund or settlement", "Get a lawyer's review before acting"]},
    "employment": {
        "label": "Employment", "group": 1, "side": 0, **CIVIL,
        "team": "an employment lawyer for a worker or for an employer",
        "lens": "what was owed and when, whether the failure was willful or retaliatory, and penalties and fees",
        "scope": {"CA": ["LAB", "GOV", "UIC"], "NY": ["LAB", "EXC", "WKC"], "TX": ["LA"],
                  "FL": ["FS:448", "FS:440", "FS:443", "FS:760"], "US": ["USC-29", "USC-42"]},
        "anchors": {"CA": "LAB/201 LAB/202 LAB/203 LAB/226 LAB/1194 LAB/2802 LAB/1102.5 GOV/12940", "NY": "LAB/191 LAB/193 LAB/198 LAB/215 LAB/740 EXC/296",
                    "TX": "LA/21.051 LA/21.055 LA/61.014 LA/62.051 LA/451.001", "FL": "FS/448.110 FS/448.102 FS/448.08 FS/760.10 FS/440.205",
                    "US": "USC-29/206 USC-29/207 USC-29/216 USC-29/2612 USC-29/623 USC-42/2000e-2 USC-42/12112"},
        "next_steps": ["Send a wage demand that cites the statute", "File a wage claim or suit now", "Gather pay records and the missing proof first",
                       "Negotiate a settlement", "Get a lawyer's review before acting"]},
    "personal_injury": {
        "label": "Personal injury", "group": 1, "side": 0, **CIVIL,
        "team": "a personal injury lawyer for an injured person or for the defense",
        "lens": "fault and its share, the filing deadline, which damages are recoverable and what limits them",
        "scope": {"CA": ["CIV", "CCP", "VEH", "EVID"], "NY": ["CVP", "GOB", "VAT", "ISC"], "TX": ["CP", "TN"],
                  "FL": ["FS:768", "FS:766", "FS:627", "FS:316", "FS:95"], "US": ["USC-28", "USC-45"]},
        "anchors": {"CA": "CIV/1714 CIV/1431.2 CIV/3333.4 CCP/335.1 EVID/669 VEH/21703", "NY": "CVP/1411 CVP/214 CVP/1601 ISC/5102 ISC/5104 VAT/1129",
                    "TX": "CP/33.001 CP/33.003 CP/16.003 CP/41.003 CP/41.008 TN/545.062", "FL": "FS/768.81 FS/768.28 FS/768.0755 FS/95.11 FS/627.737",
                    "US": "USC-28/1346 USC-28/2674 USC-28/2401 USC-45/51"},
        "next_steps": ["Send a demand letter to the insurer", "File suit before the deadline", "Gather medical records and the missing proof first",
                       "Negotiate a settlement", "Get a specialist's review before acting"]},
    "family": {
        "label": "Family", "group": 1, "side": 0, **CIVIL,
        "team": "a family lawyer or a self-represented parent or spouse",
        "lens": "the child's best interest, support guidelines, property division and any need for a protective order",
        "scope": {"CA": ["FAM", "PROB"], "NY": ["DOM", "FCT"], "TX": ["FA"], "FL": ["FS:61", "FS:741", "FS:742", "FS:63", "FS:39"],
                  "US": ["USC-28", "USC-42"]},
        "anchors": {"CA": "FAM/2310 FAM/3011 FAM/4055 FAM/4320 FAM/6203 FAM/760", "NY": "DOM/170 DOM/236 DOM/240 FCT/812 FCT/413",
                    "TX": "FA/6.001 FA/7.001 FA/153.002 FA/154.125 FA/85.001", "FL": "FS/61.13 FS/61.08 FS/61.075 FS/61.30 FS/741.30",
                    "US": "USC-28/1738A USC-28/1738B"},
        "next_steps": ["Send a written proposal to the other parent or spouse", "File the petition now", "Gather financial records and the missing proof first",
                       "Go to mediation", "Get a lawyer's review before acting"]},
    "criminal_defense": {
        "label": "Criminal defense", "group": 2, "side": 1, **CRIME,
        "team": "a defense lawyer or public defender for the accused",
        "lens": "which element the prosecution cannot prove, available defenses and motions, and the risk of trial against a plea",
        "scope": CRIME_SCOPE,
        "anchors": {"CA": "PEN/1096 PEN/1538.5 PEN/1385 PEN/484 PEN/487 PEN/459", "NY": "CPL/30.30 CPL/710.20 CPL/170.30 PEN/35.15 PEN/155.25",
                    "TX": "PE/2.01 PE/8.02 PE/9.31 PE/6.03 CR/38.23 CR/38.04", "FL": "FS/776.012 FS/812.014 FS/810.02 FS/775.082 FS/90.404",
                    "US": "USC-18/3161 USC-18/3553 USC-18/1343 USC-18/371"},
        "next_steps": ["Move to dismiss or suppress", "Set the case for trial", "Gather the missing defense evidence first",
                       "Negotiate a plea to a lesser offense", "Seek diversion or deferred adjudication"]},
    "prosecutor": {
        "label": "Prosecution", "group": 2, "side": 0, **CRIME,
        "team": "a district attorney's or state attorney's charging unit",
        "lens": "whether every element can be proven beyond a reasonable doubt, the right charge level, and whether to file, plead or decline",
        "scope": CRIME_SCOPE,
        "anchors": {"CA": "PEN/484 PEN/487 PEN/459 PEN/211 PEN/1096 VEH/23152", "NY": "PEN/155.25 PEN/155.30 PEN/140.20 PEN/120.00 VAT/1192",
                    "TX": "PE/31.03 PE/31.08 PE/6.03 PE/12.35 PE/22.01 PE/49.04", "FL": "FS/812.014 FS/812.13 FS/810.02 FS/784.03 FS/775.082",
                    "US": "USC-18/1343 USC-18/371 USC-18/1001 USC-21/841 USC-18/922"},
        "next_steps": ["File the charge as drafted", "Present the case to a grand jury", "Send it back for the missing evidence first",
                       "Offer a plea to a lesser offense", "Decline to prosecute"]},
}
DEFAULT = "real_estate"
AREA_QUESTIONS = {g: f"area_{name.split()[0].lower()}" for g, name in enumerate(GROUPS)}  # area_business, area_people, area_criminal


def get(role_id: str | None) -> dict:
    """The role with its id filled in; an unknown or empty id gives the default role."""
    rid = role_id if role_id in ROLES else DEFAULT
    return {"id": rid, **ROLES[rid]}


def in_group(group: int) -> list[str]:
    return [rid for rid, r in ROLES.items() if r["group"] == group]


def by_label(label: str) -> str | None:
    return next((rid for rid, r in ROLES.items() if r["label"] == label), None)


def scope(role_id: str, jur: str) -> list[str]:
    return list(ROLES[role_id]["scope"].get(jur, []))


def anchors(role_id: str, jur: str) -> list[str]:
    return [f"{jur}/{a}" for a in ROLES[role_id]["anchors"].get(jur, "").split()]


def intake_items(hint: dict | None = None) -> list[dict]:
    """The intake questions that pick a role: the group, then one question per group, each with at most 5
    options. `hint` is {qid: distribution} for the simulation only."""
    hint = hint or {}
    items = [{"qid": "practice_group", "id": "practice_group", "agent": "Clerk", "type": "choice", "options": list(GROUPS),
              "q": "Which area of law is this question about?", "hint": {"dist": hint.get("practice_group")}}]
    for g, name in enumerate(GROUPS):
        opts = [ROLES[r]["label"] for r in in_group(g)]
        ask = ("Is the asker defending the accused or bringing the charge?" if name == "Criminal" else
               f"If the question is about {name.lower()} law, which practice area fits it best?")
        items.append({"qid": AREA_QUESTIONS[g], "id": "practice_area", "agent": "Clerk", "type": "choice", "options": opts, "q": ask,
                      "hint": {"dist": hint.get(AREA_QUESTIONS[g])}})
    return [{**it, "hint": it["hint"] if it["hint"]["dist"] else None} for it in items]


def detect(recs: dict) -> dict:
    """The role Drex picked: {"id", "p_group", "p_area", "needs_person"} from the intake answers."""
    g = recs["practice_group"]
    gi = g["dist"].index(max(g["dist"]))
    a = recs[AREA_QUESTIONS[gi]]
    rid = in_group(gi)[a["dist"].index(max(a["dist"]))]
    return {"id": rid, "p_group": max(g["dist"]), "p_area": max(a["dist"]),
            "needs_person": g["route"] == "person" or a["route"] == "person"}


def check() -> list[str]:
    """Problems with the role tables: a choice with more than 5 options, a scope that covers no section,
    an anchor that is not in the local store. Scope and anchor checks need the store."""
    bad = [f"{name}: more than 5 options" for g, name in enumerate(GROUPS) if len(in_group(g)) > 5]
    bad += ["more than 5 groups"] * (len(GROUPS) > 5)
    for rid, r in ROLES.items():
        if len(r["next_steps"]) != 5:
            bad.append(f"{rid}: next_steps must hold 5 options")
        if not lawstore.available():
            continue
        for jur, specs in r["scope"].items():
            bad += [f"{rid}: scope {jur} {spec} covers no section" for spec in specs if not lawstore.count(jur, [spec])]
            bad += [f"{rid}: anchor {key} is not in the store" for key in anchors(rid, jur) if not lawstore.get(key)]
    return bad


if __name__ == "__main__":
    problems = check()
    print("\n".join(problems) or f"{len(ROLES)} roles ok" + ("" if lawstore.available() else " (store absent: scopes and anchors not checked)"))
    if "--list" in sys.argv and lawstore.available():
        for rid in ROLES:
            for jur in ROLES[rid]["anchors"]:
                for key in anchors(rid, jur):
                    rec = lawstore.get(key)
                    print(f"{rid:16} {key:22} {(rec['title'] if rec else 'MISSING')[:70]}")
    sys.exit(1 if problems else 0)
