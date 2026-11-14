"""The Fool-the-desk clean control and duplicate mutation must stay distinguishable."""
from itertools import count

from billproof.config import settings
from billproof.pipeline import analyze_with_gold
from billproof.schema import Action, Bill, BillLine, Case, Eob, EobLine, Patient, PlanSummary, Provider
from billproof.tamper import gold_for_case, naive_check, tamper


def test_clean_control_and_duplicate(monkeypatch):
    monkeypatch.setenv('MOCK_MODE', '1')
    settings.cache_clear()
    monkeypatch.setattr('billproof.tamper._ids', count(1))
    try:
        case = Case(
            case_id='SYN-0001', split='demo', title='Clean office visit',
            patient=Patient(patient_id='PT-0007'), provider=Provider(name='Synthetic Clinic'),
            plan=PlanSummary(plan_name='Synthetic Plan', deductible_cents=0, oop_max_cents=500000),
            bill=Bill(account_no='TEST-7', statement_date='2026-10-12',
                      lines=[BillLine(line_no=1, dos='2026-10-01', code='99213',
                                      label='Office visit', charge_cents=100, pos='11')],
                      total_charges_cents=100, patient_balance_cents=100),
            eob=Eob(payer='Synthetic Payer', claim_id='TEST-7', processed_date='2026-10-05',
                    lines=[EobLine(line_no=1, code='99213', billed_cents=100, allowed_cents=100,
                                   plan_paid_cents=0, patient_resp_cents=100)],
                    patient_resp_total_cents=100),
        )
        original = case.model_dump()
        clean = analyze_with_gold(case, gold_for_case(case))
        assert clean.verdicts['hybrid'].case_action == Action.OK
        assert naive_check(case)['passed']
        changed, gold = tamper(case, [{'op': 'duplicate_line', 'line_no': 1}])
        assert case.model_dump() == original
        assert changed.case_id != case.case_id
        analysis = analyze_with_gold(changed, gold)
        assert any(v.line_no == 2 and v.action == Action.DISPUTE for v in analysis.verdicts['hybrid'].lines)
        assert analysis.verdicts['hybrid'].case_action == Action.DISPUTE
        assert set(gold) == {q.name for q in analysis.questions}
    finally:
        settings.cache_clear()


def test_verification_keeps_comparison_evidence_and_accounts_for_smaller_request(monkeypatch):
    import json
    import httpx
    import pytest
    from billproof.adapters.frontier import FrontierAdapter, _sim_usage, verification_state
    from billproof.adapters.simulate import simulate_call_meta
    from billproof.schema import Answer, Question

    monkeypatch.setenv('FRONTIER_MODEL', 'gpt-4.1')
    monkeypatch.setenv('FRONTIER_BASE_URL', 'https://router.test/v1')
    monkeypatch.setenv('FRONTIER_API_KEY', 'test-key')
    monkeypatch.delenv('FRONTIER_PRICE_PER_MTOK_INPUT', raising=False)
    monkeypatch.delenv('FRONTIER_PRICE_PER_MTOK_OUTPUT', raising=False)
    settings.cache_clear()
    q = Question(name='dup_L2', type='noul', family='dup', line_no=2,
                 instructions='Is line 2 an unsupported duplicate?',
                 evidence=['bill.lines.1.label', 'bill.lines.2.label'])
    other = Question(name='fee_L3', type='noul', family='fee', line_no=3, instructions='Fee question')
    state = {'case': {'bill': {'lines': [
        {'line_no': 1, 'code': '99213', 'dos': '2026-10-01', 'label': 'Office visit'},
        {'line_no': 2, 'code': '99213', 'dos': '2026-10-01', 'label': 'Separate session documented'},
        {'line_no': 3, 'code': 'X0001', 'dos': '2026-10-02', 'label': 'UNRELATED'}]},
        'patient': {'member_id': 'OMIT-IDENTIFIER'}},
        'flags': [{'line_no': 2, 'check': 'DUPLICATE', 'fired': True}],
        'evidence': {'bill.lines.2.label': {'doc': 'bill', 'page': 1,
                     'text': 'Separate session documented', 'polygons': [[(0, 0)]]}}}
    draft = Answer(name=q.name, choice='no', confidence=0.999)
    drafts = {q.name: draft}
    sliced = verification_state(state, [q])
    assert [line['line_no'] for line in sliced['case']['bill']['lines']] == [1, 2]
    assert sliced['evidence']['bill.lines.2.label']['text'] == 'Separate session documented'
    assert 'polygons' not in json.dumps(sliced)
    full_questions = [q, other] + [other.model_copy(update={'name': f'fee_L{i}', 'line_no': i}) for i in range(4, 9)]
    full = _sim_usage('SYN-X', state, full_questions)
    partial = _sim_usage('SYN-X', state, [q], drafts=drafts, verify=True)
    assert partial[1] < full[1] and partial[2] < full[2]
    assert partial[0] < full[0] and partial[3] < full[3]
    drex_small = simulate_call_meta('drex', 1, 'x', state=sliced, questions=[q])
    drex_big = simulate_call_meta('drex', 2, 'x', state=state, questions=[q, other])
    assert drex_small['cost_usd'] < drex_big['cost_usd']
    assert drex_small['latency_ms'] < drex_big['latency_ms']

    def handler(request):
        body = json.loads(request.content)
        payload = json.loads(body['messages'][1]['content'])
        assert 'UNRELATED' not in json.dumps(payload)
        assert 'OMIT-IDENTIFIER' not in json.dumps(payload)
        assert payload['drafts'][q.name] == {'choice': 'no'}
        return httpx.Response(200, json={'model': 'gpt-4.1',
            'usage': {'prompt_tokens': 1000, 'completion_tokens': 100},
            'choices': [{'message': {'content': json.dumps({'answers': {
                q.name: {'action': 'CONFIRM', 'choice': 'yes', 'confidence': .99, 'rationale': 'wrong action'}
            }})}}]})
    try:
        result = FrontierAdapter(transport=httpx.MockTransport(handler)).verify(state, [q], drafts, case_id='SYN-X')
        assert result.cost_usd == pytest.approx((1000 * 2 + 100 * 8) / 1_000_000)
        assert result.answers[q.name].raw['cost']['kind'] == 'estimated'
        assert result.answers[q.name].confidence == 0
        assert result.answers[q.name].raw['error'] == 'CONFIRM choice contradicts draft'
    finally:
        settings.cache_clear()


def test_hard_imr_is_near_chance_calibrated_and_verification_has_no_gold_advantage():
    from billproof.adapters.frontier import FrontierSimulator
    from billproof.adapters.simulate import simulate_answers
    from billproof.schema import Answer, GoldItem, Question

    q = Question(name='imr_overturn', family='imr_overturn', type='noul', instructions='Overturn?')
    gold = {q.name: GoldItem(answer='yes', difficulty=1.0)}
    for profile in ('drex', 'frontier'):
        hits = confidence = 0.0
        n = 2000
        for i in range(n):
            answer = simulate_answers([q], gold, profile=profile, seed=f'imr-calibration-{i}')[q.name]
            hits += answer.choice == 'yes'
            confidence += answer.confidence
        accuracy, mean_confidence = hits / n, confidence / n
        assert .47 < accuracy < .62
        if profile == 'drex':
            assert .5 <= mean_confidence < .58
            assert abs(accuracy - mean_confidence) < .04
        else:
            assert mean_confidence > accuracy + .06
    simulator = FrontierSimulator()
    for i in range(20):
        case_id = f'IMR-verification-{i}'
        standalone = simulator.decide({}, [q], case_id=case_id, gold=gold).answers[q.name]
        draft = Answer(name=q.name, choice='no', confidence=.55)
        verified = simulator.verify({}, [q], {q.name: draft}, case_id=case_id, gold=gold).answers[q.name]
        assert verified.choice == standalone.choice


def test_selective_report_excludes_humans_without_losing_legacy_scores():
    import pytest
    from billproof.bench.metrics import build_report, wilson_ci

    choices = {'kept': 'yes', 'verified': 'no', 'human': 'yes', 'capped': 'yes'}
    routes = {'kept': 'drex_kept', 'verified': 'frontier_verified', 'human': 'human', 'capped': 'hard_cap'}
    confidences = {'kept': .9, 'verified': .8, 'human': .55, 'capped': .55}
    hybrid = {
        'case_id': 'IMR-TEST', 'lane': 'hybrid',
        'gold': {k: 'yes' for k in choices},
        'correct': {k: k == 'kept' for k in choices},
        'answers': {k: {'choice': v, 'confidence': confidences[k], 'route': routes[k],
                       'confidence_kind': 'self_reported' if k == 'verified' else 'native'}
                    for k, v in choices.items()},
    }
    frontier = {**hybrid, 'lane': 'frontier', 'answers': {
        k: {**v, 'route': None, 'confidence_kind': 'self_reported'} for k, v in hybrid['answers'].items()
    }}
    report = build_report([hybrid, frontier], {'bench': 'imr', 'mode': 'SIMULATED'})
    lane = report['lanes']['hybrid']
    assert lane['accuracy'] == .25
    assert lane['coverage'] == .5 and lane['human_rate'] == .5 and lane['verify_rate'] == .25
    assert lane['accuracy_on_automated'] == .5
    assert lane['accuracy_on_automated_ci'] == list(wilson_ci(1, 2))
    assert lane['n_automated_scored'] == 2
    assert lane['ece'] == pytest.approx((.1 + .45 + .45) / 3)
    assert report['lanes']['frontier']['coverage'] == 1
    points = report['selective']['hybrid']
    assert points[0]['coverage'] == .5
    strict = next(p for p in points if p['threshold'] == .85)
    assert strict['coverage'] == .25 and strict['accuracy_on_automated'] == 1
    assert points[-1]['coverage'] == 0
    assert points[-1]['accuracy_on_automated'] is None
    assert points[-1]['accuracy_on_automated_ci'] == [None, None]
    assert '50.0% for human review' in report['headline']
    assert '25.0%' not in report['headline']
