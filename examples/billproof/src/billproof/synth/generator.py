"""Seeded, document-grounded cases. Gold never enters the Case wire format.

The judgement oracle here describes synthetic evidence, not clinical adjudication.
CMS PFS is only a binding cap where an explicit PFS-linked contract is documented.
"""
from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from billproof.config import CMS_DIR, SYN_DIR
from billproof.schema import (Action, Answer, Bill, BillLine, Case, Denial, DenialCategory,
                              Eob, EobLine, ErrorType as E, GoldItem, Patient, PlanSummary, Provider)
from billproof.rules import cms_tables as cms
from billproof.rules.checks import run_checks
from billproof.rules.money import pct_of
from billproof.decisions.questions import build_questions, LINE_FAMILIES, CASE_FAMILIES


def _seed_file(name: str):
    path = SYN_DIR / 'seed' / f'{name}.json'
    return json.loads(path.read_text()) if path.exists() else None


def _name(seed, key, fallback, index=0):
    rows = seed.get(key, []) if isinstance(seed, dict) else seed
    if isinstance(rows, list) and rows:
        row = rows[index % len(rows)]
        return row if isinstance(row, str) else row.get('name', fallback)
    return fallback


def _line(code, n, setting, dos='2026-10-15'):
    info = cms.code_info(code)
    pos = {'office': '11', 'emergency': '23', 'imaging': '11', 'lab': '81', 'outpatient': '22'}[setting]
    fee = cms.fee_cents(code, facility=pos in {'22', '23'}) or info.get('fee_cents') or 5000
    return BillLine(line_no=n, dos=dos, code=code, label=info['label'], units=1,
                    charge_cents=max(1, pct_of(fee, 125)), pos=pos, rendering_provider='SYN-CLINICIAN-01')


def _oracle(case: Case, flags):
    """Answer from visible dates, adjudication remarks and explicit synthetic notes."""
    answers = {}
    lines = {a.line_no: a for a in case.bill.lines}
    eobs = {a.line_no: a for a in case.eob.lines} if case.eob else {}
    errors = {}
    difficulty = {}
    for f in flags:
        if f.check not in LINE_FAMILIES:
            continue
        family = LINE_FAMILIES[f.check]
        name = f'{family}_L{f.line_no}'
        line = lines[f.line_no]
        remark = (eobs.get(f.line_no).remark or '') if f.line_no in eobs else ''
        answer = f.fired
        hard = .3
        if family == 'dup':
            answer = f.fired and 'Separate session documented' not in line.label
            hard = .72 if line.modifiers or 'session' in line.label.lower() else .2
        elif family == 'ncci':
            indicator_zero = 'indicator 0' in f.rule_cite
            allowed = bool(set(line.modifiers) & {'59', 'XE', 'XS', 'XP', 'XU'}) and 'Distinct service documented' in remark
            answer = indicator_zero or not allowed
            hard = .82 if line.modifiers else .35
        elif family == 'mue':
            limit = cms.mue_limit(line.code)
            exception = limit and limit[1] == 3 and 'Clinical exception documented' in remark
            answer = f.fired and not exception
            hard = .8 if limit and limit[1] == 3 else .4
        elif family == 'fee':
            answer = f.fired and 'PFS-linked contract' in remark
            hard = .75 if abs(line.charge_cents * 100 // max(1, (cms.fee_cents(line.code, facility=line.pos in {'22','23'}) or 1) * line.units)-150) < 4 else .55
        answers[name] = 'yes' if answer else 'no'
        difficulty[name] = hard
        if answer:
            errors.setdefault(f.line_no, []).append(f.check)
    for check, name in CASE_FAMILIES.items():
        fs = [f for f in flags if f.check == check]
        yes = any(f.fired for f in fs)
        if name == 'eob_mismatch':
            # A later corrected adjudication replaces the prior EOB's displayed total.
            corrected = any('Later corrected responsibility' in (a.remark or '') for a in eobs.values())
            yes = yes and not corrected
        answers[name] = 'yes' if yes else 'no'
        difficulty[name] = .78 if name == 'eob_mismatch' else (.9 if name == 'nsa' else .25)
    answers['received'] = 'yes'
    difficulty['received'] = .45
    for n in lines:
        answers[f'action_L{n}'] = 'DISPUTE' if errors.get(n) else 'OK'
        difficulty[f'action_L{n}'] = max([difficulty[k] for k in difficulty if k.endswith(f'_L{n}')] or [.3])
    if answers['missing_data'] == 'yes':
        for n, line in lines.items():
            if not line.dos or line.units <= 0:
                answers[f'action_L{n}'] = 'INSUFFICIENT_EVIDENCE'
    if case.denial:
        answers['denial_category'] = case.denial.category.value
        answers['appeal_strength'] = 'strong' if 'Records enclosed demonstrate' in case.denial.reason_text else ('moderate' if 'Additional records requested' in case.denial.reason_text else 'weak')
        difficulty['denial_category'] = .35
        difficulty['appeal_strength'] = .7
    # Stage fixtures (including their in-memory tampered copies) are explicit
    # teaching examples; held-out dev/test difficulty remains unchanged.
    if case.split == 'demo' and case.case_id.startswith('SYN-D'):
        difficulty = {name: 0.0 for name in answers}
    return answers, difficulty


def _make(index, split, scenario, rng, catalog, demo=None):
    codes = catalog['codes']
    # Encounter bundles use plain-language CMS catalog labels. Fillers use later DOS
    # (e.g. follow-up labs / PT) to avoid accidentally planting extra PTP edits.
    settings = ['office', 'emergency', 'imaging', 'lab', 'outpatient', 'outpatient']
    setting = settings[index % len(settings)]
    if scenario == 'nsa' or demo == 1:
        setting = 'emergency'
    available = [c for c, info in codes.items() if setting in info.get('setting', []) and cms.fee_cents(c) and (not cms.mue_limit(c) or cms.mue_limit(c)[0] > 0)]
    if not available:
        available = [c for c in codes if cms.fee_cents(c)]
    rng.shuffle(available)
    themes = {
        'office': ['99213','36415','85025','80048','81003','93000','84443'],
        'emergency': ['99283','36415','85025','80048','73560','93000','96372'],
        'lab': ['36415','85025','80048','84443','83036','81003','87086'],
        'imaging': ['73721','73560','72148','71046','77080','76700','70450'],
        'outpatient': (['97110','97140','97530','97010','97112','97116','97035'] if index % 2 else ['10060','12001','11102','36415','85025','96372','99213']),
    }
    preferred = [c for c in themes[setting] if c in codes and cms.fee_cents(c) and (not cms.mue_limit(c) or cms.mue_limit(c)[0] > 0)]
    compatible = []
    for c in preferred + available:
        if c not in compatible and not any(cms.ptp_edit(c,a) in (0,1) or cms.ptp_edit(a,c) in (0,1) for a in compatible):
            compatible.append(c)
    count = min(rng.randint(3, 8), len(compatible))
    lines = [_line(compatible[j], j+1, setting) for j in range(count)]
    if scenario in {'repeat76','repeat77','repeat91','sides','bilateral','session'}:
        repeatable = [c for c in available if not cms.mue_limit(c) or cms.mue_limit(c)[0] >= 2 or cms.mue_limit(c)[1] == 1]
        if repeatable:
            lines[0] = _line(rng.choice(repeatable), 1, setting)
    remarks = {}
    if scenario in {'duplicate', 'repeat76', 'repeat77', 'repeat91', 'sides', 'bilateral', 'different_day', 'session'}:
        if demo == 1 and '73721' in codes:
            lines[0] = _line('73721', 1, setting)
            lines[0].label = 'Knee MRI scan'
            lines[0].charge_cents = 184000
            remarks[1] = 'Commercial negotiated allowance; facility imaging charge.'
        second = lines[0].model_copy(deep=True)
        second.line_no = len(lines)+1
        if scenario.startswith('repeat'):
            second.modifiers = [scenario[-2:]]
            second.label += ' — Separate session documented; repeat assessment later that day'
        elif scenario == 'sides':
            lines[0].modifiers = ['RT']
            second.modifiers = ['LT']
        elif scenario == 'bilateral':
            second.modifiers = ['50']
        elif scenario == 'different_day':
            second.dos = '2026-10-25'
        elif scenario == 'session':
            second.label += ' — Separate session documented at 16:00 following 09:00 service'
        lines.append(second)
        if demo == 1:
            remarks[second.line_no] = remarks[1]
    elif scenario.startswith('ncci'):
        indicator = 0 if scenario == 'ncci0' else 1
        pairs = [p for p in catalog['ptp'] if p['modifier_indicator'] == indicator and p['col1'] != p['col2'] and p['col1'] in codes and p['col2'] in codes]
        if not pairs:
            raise ValueError(f'Catalog lacks indicator {indicator} PTP pairs')
        p = pairs[index % len(pairs)]
        lines[0], lines[1] = _line(p['col1'], 1, setting), _line(p['col2'], 2, setting)
        if scenario in {'ncci_allowed', 'ncci_unsupported', 'ncci0'}:
            lines[1].modifiers = [['59'], ['XU'], ['XS']][index % 3]
        if scenario == 'ncci_allowed':
            remarks[2] = 'Distinct service documented: separate anatomical site and non-overlapping work at a later session.'
        elif scenario == 'ncci_unsupported':
            remarks[2] = 'Same site and same session; no separate work documented for the modifier.'
    elif scenario.startswith('mue'):
        mai = int(scenario[-1]) if scenario[-1].isdigit() else 3
        choices = [c for c in codes if cms.mue_limit(c) and cms.mue_limit(c)[1] == mai and cms.mue_limit(c)[0] > 0 and cms.fee_cents(c)]
        if not choices:
            raise ValueError(f'Catalog lacks MAI {mai} codes')
        c = choices[index % len(choices)]
        lines[0] = _line(c, 1, setting)
        cap, _ = cms.mue_limit(c)
        lines[0].units = cap+1
        lines[0].charge_cents *= cap+1
        if scenario == 'mue_exception':
            remarks[1] = 'Clinical exception documented: repeat testing for an acute change, with orders and timed results enclosed.'
        if scenario == 'mue_split1':
            lines[0].units = cap
            lines[0].charge_cents = _line(c, 1, setting).charge_cents * cap
            extra = lines[0].model_copy(deep=True)
            extra.line_no = len(lines)+1
            extra.modifiers = ['91']
            lines.append(extra)
    elif scenario.startswith('fee'):
        fee = cms.fee_cents(lines[0].code, facility=lines[0].pos in {'22','23'})
        lines[0].charge_cents = pct_of(fee, 149 if scenario == 'fee_below' else 151 if scenario == 'fee_near' else 230)
        remarks[1] = 'PFS-linked contract: provider agreed to national PFS rate.' if scenario != 'fee_commercial' else 'Commercial negotiated allowance explicitly covers this charge; national PFS is not the contract.'
    elif scenario == 'missing_dos':
        lines[0].dos = None
    elif scenario == 'missing_units':
        lines[0].units = 0
    # Scenario services can replace a bundle item. Move unrelated conflicting
    # follow-ups to their documented later date rather than plant accidental errors.
    for j, line in enumerate(lines[1:], 1):
        if j == 1 and scenario.startswith('ncci'):
            continue
        if any(line.code == a.code or cms.ptp_edit(line.code,a.code) in (0,1) or cms.ptp_edit(a.code,line.code) in (0,1) for a in lines[:j]):
            if not (j == len(lines)-1 and scenario in {'duplicate','repeat76','repeat77','repeat91','sides','bilateral','different_day','session','mue_split1'}):
                line.dos = f'2026-10-{16+j:02d}'
    plan = PlanSummary(plan_name='Synthetic Choice PPO', deductible_cents=rng.choice([50000, 100000, 200000]),
                       coinsurance_pct=rng.choice([10,20,30]), oop_max_cents=600000)
    plan_seeds = _seed_file('plans')
    if isinstance(plan_seeds, list) and plan_seeds:
        row = plan_seeds[index % len(plan_seeds)]
        plan = PlanSummary.model_validate({k:v for k,v in row.items() if k in PlanSummary.model_fields})
        # These procedural services use the deductible/coinsurance benefit.
        plan.copay_cents = 0
    plan.deductible_met_cents = max(0, plan.deductible_cents-rng.choice([0,5000,15000]))
    remaining = plan.deductible_cents-plan.deductible_met_cents
    eob_lines = []
    for line in lines:
        fee = cms.fee_cents(line.code, facility=line.pos in {'22','23'}) or 5000
        allowed = min(line.charge_cents, fee*max(1,line.units))
        if 'Commercial negotiated' in remarks.get(line.line_no, ''):
            allowed = line.charge_cents
        deductible = min(remaining, allowed)
        remaining -= deductible
        resp = deductible + pct_of(allowed-deductible, plan.coinsurance_pct)
        note = f'Deductible {deductible} cents; coinsurance {plan.coinsurance_pct}% of remaining allowance. ' + remarks.get(line.line_no, '')
        eob_lines.append(EobLine(line_no=line.line_no, code=line.code, units=max(1,line.units), billed_cents=line.charge_cents,
                                allowed_cents=allowed, plan_paid_cents=allowed-resp, patient_resp_cents=resp, remark=note))
    total = sum(a.charge_cents for a in lines)
    resp = sum(a.patient_resp_cents for a in eob_lines)
    paid = sum(a.plan_paid_cents for a in eob_lines)
    balance = resp
    if scenario in {'eob', 'eob_adjustment', 'nsa'}:
        balance += rng.choice([3500,7500,18500])
    if scenario == 'eob_adjustment':
        eob_lines[0].remark += f' Later corrected responsibility {balance} cents on 2026-11-03 replaces this prior EOB; provider statement reflects corrected amount.'
    patient = Patient(patient_id=f'PT-{index:04d}', member_id=f'SYN-M{index:06d}', dob='1978-04-12')
    if scenario in {'after_death', 'same_day_death'}:
        patient.date_of_death = '2026-10-15' if scenario == 'same_day_death' else '2026-10-14'
        # All other services precede death; only the selected line tests the boundary.
        for line in lines[1:]:
            line.dos = f'2026-10-{line.line_no:02d}'
    case_id = f'SYN-D{demo:02d}' if demo else f'SYN-{index:04d}'
    facility = _name(_seed_file('facilities'), 'facilities', 'Cedar Vale Synthetic Clinic', index)
    payer = _name(_seed_file('payers'), 'payers', 'Harbor Synthetic Health', index)
    case = Case(case_id=case_id, split=split, title=f'{setting.capitalize()} visit · itemized statement', patient=patient,
                provider=Provider(name=facility, in_network=scenario != 'nsa'), plan=plan, service_setting=setting,
                bill=Bill(account_no=f'AC-{index:06d}', statement_date='2026-11-05', lines=lines,
                          total_charges_cents=total, insurance_paid_cents=paid,
                          adjustments_cents=total-paid-balance, patient_balance_cents=balance),
                eob=Eob(payer=payer, claim_id=f'CL-{index:06d}', processed_date='2026-10-30', lines=eob_lines, patient_resp_total_cents=resp))
    if rng.random() < .3 and demo not in {1,4,5} and scenario != 'eob_adjustment':
        fallback = {'CO-50': 'MEDICAL_NECESSITY', 'CO-197': 'PRIOR_AUTH', 'CO-97': 'CODING_BUNDLING', 'CO-16': 'MISSING_INFO', 'CO-29': 'TIMELY_FILING'}
        carc_path = CMS_DIR / 'carc_rarc.json'
        if carc_path.exists():
            raw = json.loads(carc_path.read_text()).get('carc', {})
            entries = [(k,v['category']) for k,v in raw.items() if isinstance(v,dict) and v.get('category') in DenialCategory._value2member_map_]
        else:
            entries = list(fallback.items())
        carc, category = rng.choice(entries or list(fallback.items()))
        wording = rng.choice(['Additional records requested before reconsideration.', 'Records enclosed demonstrate the service satisfies the stated requirement.', 'No supporting rebuttal is included in this packet.'])
        word_seeds = _seed_file('denial_wording') or {}
        reasons = word_seeds.get(category, {}).get('reasons', [])
        reason = rng.choice(reasons).format(service=lines[0].label, date=lines[0].dos or 'date not supplied') if reasons else f'The plan declined the claim for {category.lower().replace("_", " ")}.'
        case.denial = Denial(letter_date='2026-11-01', carc=carc, category=category,
                             reason_text=f'{reason} {wording}',
                             service_code=lines[0].code, appeal_deadline='2027-03-30', plan_section='Synthetic plan section 7')
        delta = case.bill.patient_balance_cents-case.eob.patient_resp_total_cents
        remaining = plan.deductible_cents-plan.deductible_met_cents
        for j, el in enumerate(case.eob.lines):
            if j == 0:
                el.carc = [carc]
                el.allowed_cents = el.patient_resp_cents = el.plan_paid_cents = 0
                el.remark = 'Denied line: zero allowance; no patient liability while provider resolves denial. ' + remarks.get(el.line_no, '')
                continue
            deductible = min(remaining, el.allowed_cents)
            remaining -= deductible
            el.patient_resp_cents = deductible+pct_of(el.allowed_cents-deductible, plan.coinsurance_pct)
            el.plan_paid_cents = el.allowed_cents-el.patient_resp_cents
            el.remark = f'Deductible {deductible} cents; coinsurance {plan.coinsurance_pct}% of remaining allowance. ' + remarks.get(el.line_no, '')
        case.eob.patient_resp_total_cents = sum(el.patient_resp_cents for el in case.eob.lines)
        case.bill.patient_balance_cents = case.eob.patient_resp_total_cents+delta
        case.bill.insurance_paid_cents = sum(el.plan_paid_cents for el in case.eob.lines)
        case.bill.adjustments_cents = total-case.bill.insurance_paid_cents-case.bill.patient_balance_cents
    return case



def stage_case(number: int) -> Case:
    """Six simple, deliberately separated stage stories; no random denial overlays."""
    settings = {1: 'emergency', 2: 'lab', 3: 'office', 4: 'office', 5: 'emergency', 6: 'office'}
    codes = {
        1: ['99283', '73721', '73721'],
        2: ['80053', '80048', '36415'],
        3: ['12011', '85025', '80048'],
        4: ['99213', '85025', '80048'],
        5: ['99283', '85025', '80048'],
        6: ['99213', '36415', '85025', '80053', '84443', '81003'],
    }
    setting = settings[number]
    lines = [_line(code, i+1, setting) for i,code in enumerate(codes[number])]
    notes = {}
    if number == 1:
        for line in lines[1:]:
            line.label = 'Right knee MRI scan — one scan performed at 14:00'
            line.charge_cents = 184000
            notes[line.line_no] = ('Commercial negotiated allowance covers this facility imaging charge. '
                'Visit record documents one right knee MRI at 14:00; no repeat scan or second side.')
    elif number == 2:
        assert cms.ptp_edit('80053', '80048') == 0
        notes[1] = 'One comprehensive chemistry panel on one blood sample at 09:00.'
        notes[2] = ('Basic panel repeats component tests already included in the comprehensive panel '
            'on the same sample, same session and same provider. No additional test order or second sample.')
    elif number == 3:
        assert cms.mue_limit('12011') == (1, 2)
        lines[0].units = 4
        lines[0].charge_cents *= 4
        lines[0].label = 'One small facial wound repaired — quantity entered as four'
        notes[1] = ('Visit record: exactly one wound and one repair. Quantity on bill is four. '
            'No other wounds or repeat repairs were performed. The insurer has not issued a denial.')
    elif number == 4:
        notes[1] = ('Final EOB issued 2026-10-30. No later claim adjustment, additional service, '
            'prior balance or patient payment changes this responsibility.')
    elif number == 5:
        notes[1] = ('Emergency department visit, not elective or ground ambulance. '
            'Out-of-network emergency provider; EOB applies protected in-network cost sharing. '
            'No subsequent adjustment or other service explains the additional provider balance.')
    else:
        notes[1] = 'Office follow-up visit documented as completed; all listed lab services were performed.'
    plan = PlanSummary(plan_name='Cedar Choice PPO', deductible_cents=100000,
        deductible_met_cents=100000, coinsurance_pct=20, copay_cents=0, oop_max_cents=600000)
    eob_lines = []
    for line in lines:
        fee = cms.fee_cents(line.code, facility=line.pos in {'22','23'})
        allowed = min(line.charge_cents, fee*line.units)
        if number == 1 and line.code == '73721':
            allowed = line.charge_cents
        resp = pct_of(allowed, plan.coinsurance_pct)
        eob_lines.append(EobLine(line_no=line.line_no, code=line.code, units=line.units,
            billed_cents=line.charge_cents, allowed_cents=allowed,
            plan_paid_cents=allowed-resp, patient_resp_cents=resp,
            remark='Deductible met; patient pays 20% of allowance. ' + notes.get(line.line_no, 'Service documented as completed.')))
    total = sum(line.charge_cents for line in lines)
    resp = sum(line.patient_resp_cents for line in eob_lines)
    paid = sum(line.plan_paid_cents for line in eob_lines)
    balance = resp + (18500 if number == 4 else 35000 if number == 5 else 0)
    titles = {1:'ER visit · one knee MRI', 2:'Lab visit · comprehensive chemistry panel',
        3:'Office visit · one facial wound repair', 4:'Office visit · final insurer responsibility',
        5:'Emergency visit · out-of-network clinician', 6:'Office follow-up · routine labs'}
    return Case(case_id=f'SYN-D{number:02d}', split='demo', title=titles[number],
        patient=Patient(patient_id=f'PT-D{number:02d}', member_id=f'SYN-DEMO-{number:02d}'),
        provider=Provider(name='Cedar Vale Medical Center' if setting == 'emergency' else 'Cedar Vale Clinic', in_network=number != 5),
        plan=plan, service_setting=setting,
        bill=Bill(account_no=f'AC-DEMO-{number:02d}', statement_date='2026-11-05', lines=lines,
            total_charges_cents=total, insurance_paid_cents=paid, adjustments_cents=total-paid-balance,
            patient_balance_cents=balance),
        eob=Eob(payer='Harbor Synthetic Health', claim_id=f'CL-DEMO-{number:02d}', processed_date='2026-10-30',
            lines=eob_lines, patient_resp_total_cents=resp))


def _stage_label(case: Case) -> dict:
    from billproof.decisions.verdicts import compute_verdict
    flags = run_checks(case)
    answers, difficulties = _oracle(case, flags)
    gold = {}
    for q in build_questions(case, flags):
        check = next((e for e,f in LINE_FAMILIES.items() if q.name.startswith(f+'_L')), None)
        check = check or next((e for e,f in CASE_FAMILIES.items() if q.name == f), None)
        gold[q.name] = GoldItem(answer=answers[q.name], difficulty=difficulties.get(q.name,0.0),
            error_type=check, rule_id=f'BP-{check.value}' if check else None)
    planted = []
    for flag in flags:
        name = f'{LINE_FAMILIES[flag.check]}_L{flag.line_no}' if flag.check in LINE_FAMILIES else CASE_FAMILIES.get(flag.check)
        if name in gold and gold[name].answer == 'yes':
            planted.append(dict(error_type=flag.check.value, line_no=flag.line_no,
                detail=flag.detail, rule_id=gold[name].rule_id))
    verdict = compute_verdict(case, flags, {name: Answer(name=name, choice=g.answer,
        confidence=1, confidence_kind='rule') for name,g in gold.items()})
    return dict(case_id=case.case_id, split='demo', planted=planted,
        gold={k:v.model_dump(mode='json') for k,v in gold.items()},
        verdict_gold=dict(case_action=verdict.case_action.value, owed_cents=verdict.owed_cents))


def refresh_demos(output_dir: Path = SYN_DIR) -> dict:
    """Replace only demo cases/label rows; keep every dev/test byte untouched.

    Call render_pdfs.py --case for each changed demo immediately afterwards.
    Stale citations are intentionally cleared until that authorized render runs.
    """
    path = output_dir / 'labels.jsonl'
    rows = path.read_text().splitlines(keepends=True)
    demos = [stage_case(n) for n in range(1,7)]
    replacements = {c.case_id: json.dumps(_stage_label(c), sort_keys=True, separators=(',',':'))+'\n' for c in demos}
    payload = []
    for row in rows:
        case_id = json.loads(row)['case_id']
        payload.append(replacements.pop(case_id, row))
    payload.extend(replacements.values())
    for case in demos:
        (output_dir / 'cases' / f'{case.case_id}.json').write_text(case.model_dump_json(indent=2)+'\n')
    path.write_text(''.join(payload))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (output_dir / 'labels.sha256').write_text(digest+'\n')
    return {'demo_cases': [c.case_id for c in demos], 'label_sha256':digest,
            'render_required': [c.case_id for c in demos]}


def generate(seed: int = 20261114, output_dir: Path = SYN_DIR) -> dict:
    catalog = json.loads((CMS_DIR / 'catalog.json').read_text())
    rng = random.Random(seed)
    negatives = ['clean','repeat76','repeat77','repeat91','sides','bilateral','different_day','session',
                 'ncci_allowed','mue_exception','mue_split1','fee_below','fee_commercial','eob_adjustment','same_day_death']
    positives = ['duplicate','ncci0','ncci_unsupported','ncci_plain','mue1','mue2','mue3','fee_near','fee_high','eob','after_death','missing_dos','missing_units']
    recipes = [(negatives[i % len(negatives)], True) for i in range(44)]
    recipes += [(positives[i % len(positives)], False) for i in range(129)] + [('nsa',False)] * 2
    rng.shuffle(recipes)
    cases = [_make(i+1, 'dev' if i < 60 else 'test', scenario, rng, catalog) for i,(scenario,_) in enumerate(recipes)]
    cases += [stage_case(n) for n in range(1,7)]
    # A subset has a second independently supported error, still at most two.
    for i, case in enumerate(cases[:175]):
        current_flags = run_checks(case)
        current_answers, _ = _oracle(case, current_flags)
        current_count = sum(current_answers.get(f'{LINE_FAMILIES[f.check]}_L{f.line_no}' if f.check in LINE_FAMILIES else CASE_FAMILIES.get(f.check)) == 'yes' for f in current_flags)
        if current_count != 1 or recipes[i][1] or recipes[i][0] in {'nsa','eob','after_death','missing_dos','missing_units'} or i % 5:
            continue
        case.patient.date_of_death = '2026-10-28'
        case.bill.lines[2].dos = '2026-10-29'
    from billproof.decisions.verdicts import compute_verdict
    case_dir = output_dir / 'cases'
    case_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    counts = Counter()
    prepared = []
    for case_index, case in enumerate(cases):
        flags = run_checks(case)
        questions = build_questions(case, flags)
        answers, difficulties = _oracle(case, flags)
        gold = {}
        for q in questions:
            check = next((e for e,f in LINE_FAMILIES.items() if q.name.startswith(f+'_L')), None)
            check = check or next((e for e,f in CASE_FAMILIES.items() if q.name == f), None)
            gold[q.name] = GoldItem(answer=answers[q.name], difficulty=difficulties.get(q.name,.4),
                                     error_type=check, rule_id=f'BP-{check.value}' if check else None)
        planted = []
        for f in flags:
            name = f'{LINE_FAMILIES[f.check]}_L{f.line_no}' if f.check in LINE_FAMILIES else CASE_FAMILIES.get(f.check)
            if name in gold and gold[name].answer == 'yes':
                planted.append({'error_type':f.check.value,'line_no':f.line_no,'detail':f.detail,'rule_id':gold[name].rule_id})
                counts[f.check.value] += 1
        if len(planted) > 2:
            raise ValueError(f'{case.case_id}: accidental third error: {planted}')
        if case_index < 175 and recipes[case_index][1] and planted:
            raise ValueError(f'{case.case_id}: clean control has an accidental error: {planted}')
        if case_index < 175 and not recipes[case_index][1] and not planted:
            raise ValueError(f'{case.case_id}: intended error did not survive generation')
        verdict = compute_verdict(case, flags, {name: Answer(name=name, choice=g.answer, confidence=1, confidence_kind='rule') for name,g in gold.items()})
        rows.append({'case_id':case.case_id,'split':case.split,'planted':planted,
                     'gold':{k:v.model_dump(mode='json') for k,v in gold.items()},
                     'verdict_gold':{'case_action':verdict.case_action.value,'owed_cents':verdict.owed_cents}})
        path = case_dir / f'{case.case_id}.json'
        # W4 exclusively owns backfilled document fields. Regeneration preserves
        # those when all source fields are unchanged, and refuses stale fixtures.
        if path.exists():
            old = Case.model_validate_json(path.read_text())
            if old.docs or old.citations:
                if old.model_dump(exclude={'docs','citations'}) != case.model_dump(exclude={'docs','citations'}):
                    raise RuntimeError(f'{case.case_id}: source changed after W4 rendering; coordinate regeneration')
                case.docs, case.citations = old.docs, old.citations
        prepared.append((path, case.model_dump_json(indent=2)+'\n'))
    for path, payload in prepared:
        path.write_text(payload)
    payload = ''.join(json.dumps(row, sort_keys=True, separators=(',',':'))+'\n' for row in rows)
    (output_dir / 'labels.jsonl').write_text(payload)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    (output_dir / 'labels.sha256').write_text(digest+'\n')
    difficulties = Counter('easy' if g['difficulty'] < .4 else 'medium' if g['difficulty'] < .7 else 'hard' for row in rows for g in row['gold'].values())
    return {'cases':len(cases),'splits':dict(Counter(c.split for c in cases)), 'planted':dict(counts),
            'clean':sum(not row['planted'] for row in rows), 'difficulty':dict(difficulties),'label_sha256':digest}
