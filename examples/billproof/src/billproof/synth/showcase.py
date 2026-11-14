"""Build the showcase-only synthetic cases and the demo manifest.

Everything here is a hand-picked teaching example for the live demo, never
benchmark data. The builder writes only under ``data/showcase/**`` and
``data/metrics/demo_showcase.json``; dev/test cases and labels are untouched.

Run:  cd billproof && .venv/bin/python -m billproof.synth.showcase
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from billproof.config import DATA_DIR, PKG_ROOT
from billproof.data import load_labels
from billproof.decisions.questions import build_questions
from billproof.decisions.verdicts import compute_verdict
from billproof.rules import cms_tables as cms
from billproof.rules.checks import run_checks
from billproof.rules.money import pct_of
from billproof.schema import (Action, Answer, Bill, Case, Denial, DenialCategory,
                              Eob, EobLine, Patient, PlanSummary, Provider)
from billproof.synth.generator import _line, _oracle
from billproof.synth.render import render_case, verify_render

SHOWCASE_DIR = DATA_DIR / "showcase"
MANIFEST_PATH = SHOWCASE_DIR / "cases.json"
METRICS_PATH = DATA_DIR / "metrics" / "demo_showcase.json"

PRODUCT_ROLES = {
    "drex": "types the yes/no/choice questions and returns calibrated probabilities; code, not Drex, sets every dollar",
    "ndi": "splits the packet and extracts bill/EOB/denial fields with page citations for evidence crops",
    "search": "recorded live for SC-05 and SC-04 control; other cases have no recorded Search chain",
    "browser": "recorded live for SC-05: public Carelon guideline stand-in; proposal approval pending; .gov blocked",
}
PRODUCT_STATUS = {"drex": "active", "ndi": "active", "search": "pending", "browser": "pending"}

# expected_color is the semantic action token; status_color is the literal card
# colour the UI spec uses (green approve / red reject / amber review-missing).
STATUS_COLORS = {"OK": "green", "DISPUTE": "red", "APPEAL": "amber",
                 "ASK_PATIENT": "amber", "INSUFFICIENT_EVIDENCE": "amber"}


def _plan() -> PlanSummary:
    return PlanSummary(plan_name="Cedar Choice PPO", deductible_cents=100000,
                       deductible_met_cents=100000, coinsurance_pct=20, copay_cents=0,
                       oop_max_cents=600000)


def missing_eob_case() -> Case:
    """No EOB, a denial notice with no stated reason, and one undated charge."""
    setting = "office"
    lines = [_line("99213", 1, setting), _line("85025", 2, setting), _line("80048", 3, setting)]
    lines[2].dos = None
    total = sum(line.charge_cents for line in lines)
    return Case(
        case_id="SYN-D07", split="demo", title="Statement without an EOB · denial reason missing",
        patient=Patient(patient_id="PT-D07", member_id="SYN-DEMO-07"),
        provider=Provider(name="Cedar Vale Clinic", in_network=True),
        plan=_plan(), service_setting=setting,
        bill=Bill(account_no="AC-DEMO-07", statement_date="2026-11-05", lines=lines,
                  total_charges_cents=total, insurance_paid_cents=0, adjustments_cents=0,
                  patient_balance_cents=total),
        denial=Denial(letter_date="2026-11-02", carc="CO-16", category=DenialCategory.MISSING_INFO,
                      reason_text="No explanation for this determination was included in the notice.",
                      service_code="80048", appeal_deadline="2027-03-01",
                      plan_section="Synthetic plan section 9"),
    )


def wrongful_denial_case() -> Case:
    """Emergency CT denied as not necessary while the enclosed record supports it."""
    setting = "emergency"
    lines = [_line("99283", 1, setting), _line("74177", 2, setting)]
    plan = _plan()
    visit_allowed = min(lines[0].charge_cents, cms.fee_cents("99283", facility=True) or lines[0].charge_cents)
    visit_resp = pct_of(visit_allowed, plan.coinsurance_pct)
    eob_lines = [
        EobLine(line_no=1, code="99283", units=1, billed_cents=lines[0].charge_cents,
                allowed_cents=visit_allowed, plan_paid_cents=visit_allowed - visit_resp,
                patient_resp_cents=visit_resp,
                remark="Deductible met; patient pays 20% of allowance. Emergency visit documented as completed."),
        EobLine(line_no=2, code="74177", units=1, billed_cents=lines[1].charge_cents,
                allowed_cents=0, plan_paid_cents=0, patient_resp_cents=0, carc=["CO-50"],
                remark="Denied: plan says the CT was not medically necessary. No patient liability while the appeal is open."),
    ]
    total = sum(line.charge_cents for line in lines)
    paid = sum(line.plan_paid_cents for line in eob_lines)
    resp = sum(line.patient_resp_cents for line in eob_lines)
    return Case(
        case_id="SYN-D08", split="demo", title="Emergency CT denial · appeal with the ED record",
        patient=Patient(patient_id="PT-D08", member_id="SYN-DEMO-08"),
        provider=Provider(name="Cedar Vale Medical Center", in_network=True),
        plan=plan, service_setting=setting,
        bill=Bill(account_no="AC-DEMO-08", statement_date="2026-11-05", lines=lines,
                  total_charges_cents=total, insurance_paid_cents=paid,
                  adjustments_cents=total - paid - resp, patient_balance_cents=resp),
        eob=Eob(payer="Harbor Synthetic Health", claim_id="CL-DEMO-08", processed_date="2026-10-30",
                lines=eob_lines, patient_resp_total_cents=resp),
        denial=Denial(letter_date="2026-11-01", carc="CO-50", category=DenialCategory.MEDICAL_NECESSITY,
                      reason_text="The plan declined coverage for the CT scan as not medically necessary. "
                                  "Records enclosed demonstrate the emergency indication and the treating clinician's order.",
                      service_code="74177", appeal_deadline="2027-03-15",
                      plan_section="Synthetic plan section 5.2"),
    )


def build_cases() -> list[Case]:
    return [missing_eob_case(), wrongful_denial_case()]


def _rule_verdict(case: Case):
    flags = run_checks(case)
    answers, _ = _oracle(case, flags)
    names = {question.name for question in build_questions(case, flags)}
    answered = {name: Answer(name=name, choice=value, confidence=1.0, confidence_kind="rule")
                for name, value in answers.items() if name in names}
    return compute_verdict(case, flags, answered)


def _stake_cents(case: Case, verdict) -> tuple[int, str]:
    if case.eob is None:
        return case.bill.patient_balance_cents, "unverified_balance_no_eob"
    denied = sum(line.billed_cents for line in case.eob.lines
                 if line.allowed_cents == 0 and line.patient_resp_cents == 0 and line.carc)
    if verdict.case_action == Action.APPEAL and denied:
        return denied, "denied_charge_under_appeal"
    return max(0, case.bill.patient_balance_cents - verdict.owed_cents), "patient_balance_reduction"


STORIES = {
    "SC-01": ("Two identical knee MRI lines on the same day at the same time; the visit record "
              "documents one scan. Code removes the duplicate's patient share. A human signs the dispute."),
    "SC-02": ("One facial wound repair entered as four units. CMS MUE for 12011 is 1/day (MAI 2) and "
              "the record documents one repair. Code keeps one unit and disputes the excess."),
    "SC-03": ("A routine follow-up with five lab lines; every charge matches its EOB row. The clean "
              "control stays green and nothing is disputed."),
    "SC-04": ("No EOB was supplied and the denial notice states no reason; one charge also lacks a "
              "service date. BillProof asks for the missing documents instead of guessing an amount."),
    "SC-05": ("The plan denied an emergency CT as not medically necessary, but the notice does not "
              "address the enclosed ED record. The record supports an appeal; a human signs it."),
    "SC-06": ("Out-of-network emergency clinician. The EOB applies protected in-network cost sharing, "
              "yet the bill adds $350. Balance billing goes to a human, never an auto-approval."),
}

PRODUCTS = {
    "SC-01": [("drex", "answers dup_L3 and action_L3 with calibrated probabilities"),
              ("ndi", "extracts both MRI rows and the one-scan remark with page boxes"),
              ("search", "planned (T3 pending): would quote the visit note that documents a single scan")],
    "SC-02": [("drex", "answers mue_L1 and action_L1"),
              ("ndi", "extracts the four-unit quantity and the one-repair note")],
    "SC-03": [("drex", "confirms every line OK"),
              ("ndi", "extracts all six lines and their EOB matches"),
              ("search", "planned (T3 pending): would answer whether any lab was repeated")],
    "SC-04": [("drex", "flags missing_data and refuses to price an unverified balance"),
              ("ndi", "extracts the bill and shows the denial notice has no reason text"),
              ("search", "recorded live negative control: no applicable supporting plan clause; draft stays an information request")],
    "SC-05": [("drex", "answers denial_category and appeal_strength"),
              ("ndi", "extracts the ED record and the CO-50 notice"),
              ("search", "recorded live: quotes plan section 5.2 page 2 and ED record; Drex document-support probability changes from 0.001 to 0.9996"),
              ("browser", "recorded live public Carelon guideline stand-in: two verbatim quotes, 31 seconds, $0.009; proposal approval pending")],
    "SC-06": [("drex", "answers nsa and routes to human review"),
              ("ndi", "extracts the out-of-network flag and protected cost share"),
              ("browser", "planned (T3 pending): would read the No Surprises Act emergency balance-billing page; .gov sources are blocked and no page is cached")],
}

# (showcase id, source case id) in demo order.
CASE_ORDER = [("SC-01", "SYN-D01"), ("SC-02", "SYN-D03"), ("SC-03", "SYN-D06"),
              ("SC-04", "SYN-D07"), ("SC-05", "SYN-D08"), ("SC-06", "SYN-D05")]


def _load_case(source_id: str) -> Case:
    for path in (DATA_DIR / "synthetic" / "cases" / f"{source_id}.json",
                 SHOWCASE_DIR / "cases" / f"{source_id}.json"):
        if path.exists():
            return Case.model_validate_json(path.read_text())
    raise FileNotFoundError(source_id)


def _expected(case: Case, verdict) -> tuple[str, int]:
    """Reused demo cases keep their published labels; new cases use the rule oracle."""
    try:
        gold = load_labels(case.case_id)["verdict_gold"]
        return gold["case_action"], int(gold["owed_cents"])
    except (FileNotFoundError, KeyError):
        return verdict.case_action.value, verdict.owed_cents


def _entry(showcase_id: str, case: Case) -> dict:
    verdict = _rule_verdict(case)
    outcome, owed = _expected(case, verdict)
    stake, basis = _stake_cents(case, verdict)
    color = {"OK": "ok", "DISPUTE": "dispute", "APPEAL": "appeal",
             "ASK_PATIENT": "ask", "INSUFFICIENT_EVIDENCE": "insufficient"}[outcome]
    reviewed = any(reason.startswith("human_review") for reason in verdict.human_reasons)
    status_color = "amber" if reviewed else STATUS_COLORS[outcome]
    owed_known = case.eob is not None
    return {
        "case_id": showcase_id,
        "source_case_id": case.case_id,
        "title": case.title,
        "story": STORIES[showcase_id],
        "expected_outcome": outcome,
        "expected_color": color,
        "status_color": status_color,
        "billed_cents": case.bill.patient_balance_cents,
        "expected_owed_cents": owed,
        "verified_owed_cents": owed if owed_known else None,
        "total_charges_cents": case.bill.total_charges_cents,
        "dollars_at_stake_cents": stake,
        "dollars_at_stake_basis": basis,
        "expected_owed_basis": "eob_recomputed" if owed_known else "bill_balance_unverified",
        "expected_owed_known": owed_known,
        "needs_human": verdict.needs_human,
        "human_reasons": verdict.human_reasons,
        "evidence_documents": ([{"label": label, "pdf": f"src/billproof/search/corpus/SYN-D08_{stem}.pdf"}
            for label, stem in [("Plan Evidence of Coverage", "coverage"), ("ED record", "ed_record"), ("Public guideline stand-in", "policy")]] if showcase_id == "SC-05" else []),
        "browser_proposal_status": "pending_user_approval",
        "products": [{"name": name, "role": role, "status": "active" if (showcase_id == "SC-05" and name in {"search", "browser"}) or (showcase_id == "SC-04" and name == "search") else PRODUCT_STATUS[name]}
                     for name, role in PRODUCTS[showcase_id]],
        "documents": [{"kind": doc.kind, "pdf": doc.pdf, "pages": doc.pages} for doc in case.docs],
    }


def build_manifest() -> dict:
    entries = [_entry(showcase_id, _load_case(source_id)) for showcase_id, source_id in CASE_ORDER]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "disclaimer": "Hand-picked synthetic examples for the live demo, not a benchmark. "
                      "They are excluded from every accuracy number. Dollar figures are computed "
                      "from the documents shown; no live API calls.",
        "expected_color_note": "expected_color is the semantic action token "
                               "(ok/dispute/appeal/ask/insufficient); status_color is the literal "
                               "card colour (green/red/amber). amber = human review or missing evidence.",
        "owed_note": "verified_owed_cents is null when the amount is not verified. In that case "
                     "expected_owed_cents is only the bill's current ask and must never be displayed "
                     "as the amount owed.",
        "products": PRODUCT_ROLES,
        "product_status": {**PRODUCT_STATUS, "search": "active", "browser": "active"},
        "cases": entries,
    }


def build_metrics(manifest: dict) -> dict:
    entries = manifest["cases"]
    by_outcome: dict[str, int] = {}
    by_basis: dict[str, int] = {}
    by_verification = {"verified": 0, "unverified": 0}
    by_status_color: dict[str, int] = {}
    for entry in entries:
        by_outcome[entry["expected_outcome"]] = by_outcome.get(entry["expected_outcome"], 0) + 1
        basis = entry["dollars_at_stake_basis"]
        by_basis[basis] = by_basis.get(basis, 0) + entry["dollars_at_stake_cents"]
        by_verification["verified" if entry["expected_owed_known"] else "unverified"] += entry["dollars_at_stake_cents"]
        by_status_color[entry["status_color"]] = by_status_color.get(entry["status_color"], 0) + 1
    return {
        "value": len(entries),
        "n": len(entries),
        "unit": "cases",
        "source_run": "data/showcase/cases.json",
        "simulated": True,
        "computed_at": manifest["generated_at"],
        "label": "Showcase examples (not a benchmark)",
        "total_dollars_at_stake_cents": sum(by_basis.values()),
        "dollars_at_stake_by_basis": by_basis,
        "dollars_at_stake_by_verification": by_verification,
        "by_expected_outcome": by_outcome,
        "by_status_color": by_status_color,
        "unverified_case_ids": [entry["case_id"] for entry in entries if not entry["expected_owed_known"]],
        "case_ids": [entry["case_id"] for entry in entries],
    }


def write_assets() -> dict:
    pdf_dir = SHOWCASE_DIR / "pdf"
    cases_dir = SHOWCASE_DIR / "cases"
    fixtures_dir = SHOWCASE_DIR / "ndi_fixtures"
    for path in (cases_dir, fixtures_dir, METRICS_PATH.parent):
        path.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    for case in build_cases():
        rendered = render_case(case, pdf_dir)
        problems.extend(f"{case.case_id}: {p}" for p in verify_render(rendered, pdf_dir, case.case_id))
        case.docs = rendered.docs
        case.citations = rendered.citations
        (cases_dir / f"{case.case_id}.json").write_text(case.model_dump_json(indent=2) + "\n")
        (fixtures_dir / f"{case.case_id}.json").write_text(
            json.dumps(rendered.fixture, indent=2, ensure_ascii=False) + "\n")
    if problems:
        raise RuntimeError("render verification failed: " + "; ".join(problems[:10]))
    manifest = build_manifest()
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
    METRICS_PATH.write_text(json.dumps(build_metrics(manifest), indent=2) + "\n")
    from billproof.showcase import validate_showcase

    checked = validate_showcase()
    if not checked["ok"]:
        raise RuntimeError("manifest validation failed: " + "; ".join(checked["problems"]))
    return {"manifest": str(MANIFEST_PATH.relative_to(PKG_ROOT)),
            "cases": [entry["source_case_id"] for entry in manifest["cases"]],
            "pdf_dir": str(pdf_dir.relative_to(PKG_ROOT))}


def main() -> int:
    print(json.dumps(write_assets(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
