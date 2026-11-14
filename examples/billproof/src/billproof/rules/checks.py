"""Exact screening signals, not final adjudication of commercial claims."""
from collections import defaultdict
from billproof.schema import Case, RuleFlag, ErrorType as E
from billproof.rules import cms_tables as cms

FEE_THRESHOLD_PCT = 150


def run_checks(case: Case) -> list[RuleFlag]:
    flags: dict[tuple, RuleFlag] = {}
    def emit(check, line, fired, detail, cite, amount=0, evidence=()):
        key = (check, line)
        flag = RuleFlag(check=check, line_no=line, fired=fired, detail=detail, rule_cite=cite,
                        amount_cents=max(0, amount), evidence=list(evidence))
        if key not in flags or (fired and not flags[key].fired):
            flags[key] = flag
    lines = case.bill.lines
    def keys(line, *fields):
        return [f'bill.lines.{line.line_no}.{field}' for field in fields]
    groups = defaultdict(list)
    for line in lines:
        groups[(line.code, line.dos, line.rendering_provider)].append(line)
    for line in lines:
        n = line.line_no
        prior = [a for a in lines if a.line_no < n and a.code == line.code and a.rendering_provider == line.rendering_provider]
        if prior:
            same = [a for a in prior if a.dos and a.dos == line.dos]
            a = same[-1] if same else prior[-1]
            mods = set(a.modifiers + line.modifiers)
            distinct = bool(mods & {'76', '77', '91', '50'}) or ('RT' in mods and 'LT' in mods and set(a.modifiers) != set(line.modifiers))
            emit(E.DUPLICATE, n, bool(same) and not distinct,
                 f'Code {line.code} also on line {a.line_no}; compare dates, repeat modifiers, side and session.',
                 'CMS duplicate-service screening / CARC 18', line.charge_cents, keys(a, 'code', 'dos', 'modifiers') + keys(line, 'code', 'dos', 'modifiers', 'label'))
        limit = cms.mue_limit(line.code)
        if limit and line.units > 0 and line.dos:
            cap, mai = limit
            peers = groups[(line.code, line.dos, line.rendering_provider)]
            units = line.units if mai == 1 else sum(max(0, a.units) for a in peers)
            if units > cap or (units == cap and units > 1):
                # Only the last line carries a daily excess, avoiding double subtraction.
                if mai == 1 or n == max(a.line_no for a in peers):
                    excess = max(0, units-cap)
                    emit(E.MUE_UNITS_EXCEEDED, n, units > cap,
                         f'{units} units; MUE {cap}, MAI {mai} ({"per line" if mai == 1 else "per date and provider"}). Clinical support must be reviewed for MAI 3.',
                         f'CMS practitioner MUE {line.code}: {cap}, MAI {mai}',
                         min(line.charge_cents, line.charge_cents * excess // line.units),
                         [k for a in peers for k in keys(a, 'units', 'dos', 'label')])
        fee = cms.fee_cents(line.code, facility=line.pos in {'19', '21', '22', '23', '24'})
        if fee and line.units > 0 and line.charge_cents * 100 >= fee * line.units * 140:
            threshold = fee * line.units * FEE_THRESHOLD_PCT // 100
            emit(E.OVER_FEE_SCHEDULE, n, line.charge_cents > threshold,
                 f'Charge {line.charge_cents} cents vs national PFS {fee} cents/unit; screening threshold {FEE_THRESHOLD_PCT}%. Commercial allowance may differ.',
                 f'CMS national PFS {line.code}; benchmark only', max(0, line.charge_cents-fee*line.units),
                 keys(line, 'charge_cents', 'units') + [f'eob.lines.{n}.allowed_cents', f'eob.lines.{n}.remark'])
        if case.patient.date_of_death and line.dos:
            emit(E.SERVICE_AFTER_DEATH, None, line.dos > case.patient.date_of_death,
                 f'Service {line.dos}; recorded death {case.patient.date_of_death}; strictly later dates only.',
                 'CMS beneficiary date-of-death consistency', line.charge_cents,
                 keys(line, 'dos') + ['patient.date_of_death'])
        if not line.dos or line.units <= 0:
            emit(E.MISSING_DATA, None, True, f'Line {n} missing service date or positive units.',
                 'Required claim service date and units', evidence=keys(line, 'dos', 'units'))
    for col2 in lines:
        for col1 in lines:
            if col1.line_no == col2.line_no or not col1.dos or col1.dos != col2.dos or col1.rendering_provider != col2.rendering_provider:
                continue
            indicator = cms.ptp_edit(col1.code, col2.code)
            if indicator is None or indicator == 9:
                continue
            emit(E.NCCI_UNBUNDLED, col2.line_no, True,
                 f'Column 2 {col2.code} paired with column 1 {col1.code} (line {col1.line_no}); modifier indicator {indicator}; modifiers {col2.modifiers}. Distinct-service support requires review.',
                 f'CMS Medicaid NCCI practitioner PTP {col1.code}/{col2.code}, indicator {indicator}', col2.charge_cents,
                 keys(col1, 'code', 'dos') + keys(col2, 'code', 'modifiers', 'label') + [f'eob.lines.{col2.line_no}.remark'])
    if case.eob:
        diff = case.bill.patient_balance_cents - case.eob.patient_resp_total_cents
        emit(E.EOB_BILL_MISMATCH, None, diff != 0,
             f'Bill requests {case.bill.patient_balance_cents} cents; EOB responsibility {case.eob.patient_resp_total_cents} cents; check later adjustment remarks.',
             'Bill/EOB patient-responsibility reconciliation', max(0, diff),
             ['bill.patient_balance_cents', 'eob.patient_resp_total_cents', 'bill.statement_date', 'eob.processed_date'] + [f'eob.lines.{a.line_no}.remark' for a in case.eob.lines])
    emergency = case.service_setting == 'emergency' or any(a.pos == '23' for a in lines)
    if emergency and not case.provider.in_network:
        extra = max(0, case.bill.patient_balance_cents - (case.eob.patient_resp_total_cents if case.eob else 0))
        emit(E.NO_SURPRISES_ACT, None, case.plan.emergency_nsa_protected and extra > 0,
             'Out-of-network emergency service; reconcile balance against protected in-network cost sharing. Human review required.',
             'CMS No Surprises Act emergency balance-billing protections', extra,
             ['provider.in_network', 'plan.emergency_nsa_protected', 'bill.patient_balance_cents', 'eob.patient_resp_total_cents'])
    return sorted(flags.values(), key=lambda f: (f.line_no or 0, f.check.value))
