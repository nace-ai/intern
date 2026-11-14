"""Small regression checks for cents and CMS scope boundaries."""
from billproof.rules.money import fmt, pct_of
from billproof.rules.checks import run_checks
from billproof.rules import cms_tables as cms
from billproof.schema import Bill, BillLine, Case, Patient, Provider, PlanSummary, ErrorType as E


def _case(lines, death=None):
    return Case(case_id='SYN-0001', split='dev', title='Office visit', patient=Patient(patient_id='PT-1',date_of_death=death),
                provider=Provider(name='Synthetic Clinic'), plan=PlanSummary(plan_name='Test',deductible_cents=0,oop_max_cents=500000),
                bill=Bill(account_no='A1',statement_date='2026-10-01',lines=lines,total_charges_cents=sum(x.charge_cents for x in lines),patient_balance_cents=0))


def _line(n, code='11111', units=1, modifiers=(), dos='2026-09-15'):
    return BillLine(line_no=n,code=code,units=units,modifiers=list(modifiers),dos=dos,label='Synthetic service',charge_cents=1000)


def test_money():
    assert fmt(123456) == '$1,234.56'
    assert fmt(-1) == '-$0.01'
    assert pct_of(5,10) == 0
    assert pct_of(15,10) == 2
    assert pct_of(-15,10) == -2


def test_repeat_and_death_boundary(monkeypatch):
    monkeypatch.setattr(cms,'mue_limit',lambda c: None)
    monkeypatch.setattr(cms,'fee_cents',lambda *a,**k: None)
    monkeypatch.setattr(cms,'ptp_edit',lambda *a: None)
    flags=run_checks(_case([_line(1),_line(2,modifiers=['91'])],death='2026-09-15'))
    assert not any(f.fired for f in flags)
    flags=run_checks(_case([_line(1),_line(2)]))
    assert any(f.check == E.DUPLICATE and f.line_no == 2 and f.fired for f in flags)


def test_mue_scope_and_inactive_ptp(monkeypatch):
    monkeypatch.setattr(cms,'fee_cents',lambda *a,**k: None)
    monkeypatch.setattr(cms,'ptp_edit',lambda *a: 9)
    lines=[_line(1,units=2),_line(2,units=2,modifiers=['91'])]
    monkeypatch.setattr(cms,'mue_limit',lambda c: (2,1))
    assert not any(f.fired for f in run_checks(_case(lines)))
    monkeypatch.setattr(cms,'mue_limit',lambda c: (2,2))
    flags=run_checks(_case(lines))
    assert sum(f.check == E.MUE_UNITS_EXCEEDED and f.fired for f in flags) == 1
    assert not any(f.check == E.NCCI_UNBUNDLED for f in flags)


def test_compact_state_retains_rule_facts_without_identity_or_geometry():
    from billproof.decisions.questions import build_questions, build_state
    from billproof.schema import Citation, RuleFlag
    case = _case([_line(1), _line(2)])
    case.patient.member_id = 'PRIVATE-IDENTIFIER'
    case.citations['bill.lines.2.code'] = Citation(doc='bill', page=1,
        polygons=[[(0.1,0.1),(0.2,0.1),(0.2,0.2)]], text='Repeated display text')
    flags = [RuleFlag(check=E.DUPLICATE, line_no=2, fired=True, detail='Compare line 1.',
        rule_cite='Duplicate screening', evidence=['bill.lines.2.code'])]
    state = build_state(case, flags)
    assert state['case']['bill']['lines'][1]['code'] == '11111'
    assert state['flags'][0]['detail'] == 'Compare line 1.'
    assert state['flags'][0]['fired'] is True
    assert state['evidence']['bill.lines.2.code'] == {'doc':'bill','page':1}
    assert 'patient' not in state['case']
    assert 'citations' not in state['case']
    assert 'PRIVATE-IDENTIFIER' not in str(state)
    assert 'polygons' not in str(state)
    assert build_questions(case, flags)[0].evidence == ['bill.lines.2.code']


def test_demo_bills_and_clean_tamper_control(monkeypatch):
    from itertools import count
    from billproof.config import settings
    from billproof.data import load_case, load_gold
    from billproof.pipeline import analyze_case, analyze_with_gold
    from billproof.tamper import TAMPER_OPS, tamper
    monkeypatch.setenv('MOCK_MODE', '1')
    monkeypatch.setattr('billproof.tamper._ids', count(1))
    settings.cache_clear()
    try:
        for n in range(1,7):
            case = load_case(f'SYN-D{n:02d}')
            analysis = analyze_case(case, lanes=('hybrid',))
            verdict = analysis.verdicts['hybrid']
            assert all(g.difficulty == 0 for g in load_gold(case.case_id).values())
            if n < 6:
                assert verdict.case_action.value == 'DISPUTE'
                assert verdict.disputed_cents > 0
                assert analysis.letter.amount_cents == verdict.disputed_cents
                assert analysis.letter.clauses
            else:
                assert verdict.case_action.value == 'OK'
                assert not any(f.fired for f in analysis.flags)
                assert analysis.letter.letter_type == 'none'
            if n == 3:
                assert case.denial is None
                assert analysis.lanes['hybrid'].answers['mue_L1'].choice == 'yes'
            if n == 5:
                assert analysis.lanes['hybrid'].answers['nsa'].route == 'hard_cap'
        target_prefix = dict(duplicate_line='dup_L', inflate_units='mue_L',
            add_unbundled='ncci_L', change_eob_resp='eob_mismatch', shift_dos_after_death='after_death')
        original = load_case('SYN-D06')
        snapshot = original.model_dump()
        for op in TAMPER_OPS:
            changed, gold = tamper(original, [{'op':op}])
            result = analyze_with_gold(changed, gold, lanes=('hybrid',))
            assert any(a.choice == 'yes' for key,a in result.lanes['hybrid'].answers.items()
                if key.startswith(target_prefix[op]))
            assert result.verdicts['hybrid'].case_action.value != 'OK'
        assert original.model_dump() == snapshot
    finally:
        settings.cache_clear()


def test_uploaded_case_uses_lazy_loader(monkeypatch):
    import sys
    from types import ModuleType
    from billproof.data import load_case
    sentinel = _case([_line(1)])
    module = ModuleType('billproof.ingest')
    module.load_uploaded = lambda case_id: sentinel if case_id == 'UP-0042' else None
    monkeypatch.setitem(sys.modules, 'billproof.ingest', module)
    assert load_case('UP-0042') is sentinel
