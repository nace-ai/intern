"""Code-owned verdicts and money calculations."""

from __future__ import annotations

from billproof.schema import Action, Answer, Case, ErrorType, RuleFlag, Verdict, LineVerdict

_SEVERITY = {
    Action.OK: 0,
    Action.INSUFFICIENT_EVIDENCE: 1,
    Action.ASK_PATIENT: 2,
    Action.APPEAL: 3,
    Action.DISPUTE: 4,
}


def _yes(answers: dict[str, Answer], name: str) -> bool:
    answer = answers.get(name)
    return bool(answer and answer.choice.lower() == "yes")


def compute_verdict(case: Case, flags: list[RuleFlag], answers: dict[str, Answer]) -> Verdict:
    fired = [flag for flag in flags if flag.fired]
    line_actions: dict[int, LineVerdict] = {
        line.line_no: LineVerdict(line_no=line.line_no, action=Action.OK) for line in case.bill.lines
    }
    line_flag_amounts: dict[int, int] = {}

    for flag in fired:
        if flag.line_no is None:
            continue
        prefix = {
            ErrorType.DUPLICATE: "dup",
            ErrorType.MUE_UNITS_EXCEEDED: "mue",
            ErrorType.NCCI_UNBUNDLED: "ncci",
            ErrorType.OVER_FEE_SCHEDULE: "fee",
        }.get(flag.check)
        if not prefix or not _yes(answers, f"{prefix}_L{flag.line_no}"):
            continue
        verdict = line_actions.get(flag.line_no)
        if verdict is None:
            continue
        verdict.action = Action.DISPUTE
        verdict.reasons.append(flag.check.value)
        verdict.rule_cites.append(flag.rule_cite)
        line_flag_amounts[flag.line_no] = line_flag_amounts.get(flag.line_no, 0) + max(0, flag.amount_cents)

    # The action question is retained for benchmark scoring. It cannot manufacture
    # a dispute or dollar amount without a confirmed deterministic billing signal.
    eob_lines = {line.line_no: line for line in case.eob.lines} if case.eob else {}
    for line in case.bill.lines:
        cap = max(0, line.charge_cents)
        charge_stake = min(cap, line_flag_amounts.get(line.line_no, 0))
        eob_line = eob_lines.get(line.line_no)
        if eob_line:
            billed = max(0, eob_line.billed_cents)
            share_base = billed if billed else cap
            patient_share = min(max(0, eob_line.patient_resp_cents), share_base)
            patient_impact = charge_stake * patient_share // share_base if share_base else 0
        elif case.eob:
            # An unmatched line cannot safely be assumed to reduce patient liability.
            patient_impact = 0
        else:
            total_charges = max(0, case.bill.total_charges_cents)
            patient_impact = charge_stake * max(0, case.bill.patient_balance_cents) // total_charges if total_charges else 0
        line_actions[line.line_no].disputed_cents = patient_impact

    case_reasons: list[str] = []
    case_actions = [line.action for line in line_actions.values()]
    after_death = case.patient.date_of_death and (
        _yes(answers, "after_death") or any(f.check == ErrorType.SERVICE_AFTER_DEATH for f in fired)
    )
    if after_death:
        case_actions.append(Action.ASK_PATIENT)
        case_reasons.append("service_after_death")
        for line in case.bill.lines:
            if line.dos and line.dos > case.patient.date_of_death:
                line_actions[line.line_no].action = Action.ASK_PATIENT
                line_actions[line.line_no].reasons.append("SERVICE_AFTER_DEATH")
    if _yes(answers, "received") is False and "received" in answers:
        case_actions.append(Action.ASK_PATIENT)
        case_reasons.append("service_receipt_needs_patient_confirmation")
        for line in case.bill.lines:
            line_actions[line.line_no].action = Action.ASK_PATIENT
            line_actions[line.line_no].reasons.append("service_receipt_needs_patient_confirmation")
    if _yes(answers, "missing_data") or any(f.check == ErrorType.MISSING_DATA for f in fired):
        case_actions.append(Action.INSUFFICIENT_EVIDENCE)
        case_reasons.append("missing_data")
        for line in case.bill.lines:
            if not line.dos or line.units <= 0:
                line_actions[line.line_no].action = Action.INSUFFICIENT_EVIDENCE
                line_actions[line.line_no].reasons.append("MISSING_DATA")

    strength = answers.get("appeal_strength")
    if case.denial and strength and strength.choice.lower() in {"moderate", "strong"}:
        case_actions.append(Action.APPEAL)
        case_reasons.append("denial_appeal_strength_" + strength.choice.lower())
        for line in case.bill.lines:
            if not case.denial.service_code or line.code == case.denial.service_code:
                if line_actions[line.line_no].action != Action.DISPUTE:
                    line_actions[line.line_no].action = Action.APPEAL
                    line_actions[line.line_no].reasons.append("denial_appeal_strength_" + strength.choice.lower())

    confirmed_mismatch = _yes(answers, "eob_mismatch") and any(
        f.check == ErrorType.EOB_BILL_MISMATCH for f in fired
    )
    confirmed_nsa = _yes(answers, "nsa") and any(f.check == ErrorType.NO_SURPRISES_ACT for f in fired)
    case_dispute = 0
    if confirmed_mismatch or confirmed_nsa:
        case_actions.append(Action.DISPUTE)
        if confirmed_mismatch and case.eob:
            case_dispute = max(
                0, case.bill.patient_balance_cents - case.eob.patient_resp_total_cents
            )
            if case_dispute:
                case_reasons.append(ErrorType.EOB_BILL_MISMATCH.value)
        if confirmed_nsa and case.eob:
            # The EOB patient responsibility is the in-network cost-share baseline;
            # NSA billing above it is disputed, but is not subtracted from that baseline again.
            nsa_dispute = max(
                0, case.bill.patient_balance_cents - case.eob.patient_resp_total_cents
            )
            case_dispute = max(case_dispute, nsa_dispute)
            if nsa_dispute:
                case_reasons.append(ErrorType.NO_SURPRISES_ACT.value)

    case_action = max(case_actions, key=lambda action: _SEVERITY[action], default=Action.OK)
    # The EOB amount is already the correct baseline for reconciliation and NSA
    # overages, so those flags never get subtracted a second time.
    line_disputed = sum(line.disputed_cents for line in line_actions.values())
    disputed = line_disputed + case_dispute
    eob_resp = case.eob.patient_resp_total_cents if case.eob else case.bill.patient_balance_cents
    owed = max(0, eob_resp - line_disputed)
    routes = {a.route for a in answers.values()}
    human_reasons = []
    if case_action in {Action.DISPUTE, Action.APPEAL}:
        human_reasons.append("human_signature_required_for_money_movement_or_appeal")
    if "human" in routes or "hard_cap" in routes:
        human_reasons.append("decision_escalated_to_human")
    if _yes(answers, "nsa") or any(f.check == ErrorType.NO_SURPRISES_ACT for f in fired):
        # Record the review requirement without exposing an unsupported letter ground.
        human_reasons.append("human_review_balance_billing")
    human_reasons.extend(reason for reason in case_reasons if reason in {
        ErrorType.EOB_BILL_MISMATCH.value,
        ErrorType.NO_SURPRISES_ACT.value,
    })
    return Verdict(
        case_action=case_action,
        lines=list(line_actions.values()),
        billed_balance_cents=case.bill.patient_balance_cents,
        owed_cents=owed,
        disputed_cents=disputed,
        needs_human=bool(human_reasons),
        human_reasons=human_reasons,
        deadline=case.denial.appeal_deadline if case.denial else None,
    )
