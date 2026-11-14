import asyncio
import importlib
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs
import pytest
import httpx
from fastapi.testclient import TestClient


@pytest.fixture
def kit(tmp_path, monkeypatch):
    import server
    monkeypatch.setattr(server, 'DB_PATH', tmp_path / 'test.sqlite3')
    monkeypatch.setattr(server, 'WATCH_KEY', '')
    monkeypatch.setattr(server, 'DEMO_MODE', True)
    monkeypatch.setattr(server, 'THREE_WHEEL_DEMO_ALERTS', False)
    monkeypatch.setattr(server, 'VISION_PROVIDER', 'drex')
    monkeypatch.setattr(server, 'NACE_API_KEY', '')
    monkeypatch.setattr(server, 'SFMTA_NDI_WORKSPACE_ID', '')
    monkeypatch.setattr(server, 'SFMTA_NDI_FILE_ID', '')
    monkeypatch.setattr(server, 'OPENAI_API_KEY', '')
    monkeypatch.setattr(server, 'TWILIO_ACCOUNT_SID', '')
    monkeypatch.setattr(server, 'TWILIO_AUTH_TOKEN', '')
    monkeypatch.setattr(server, 'TWILIO_FROM', '')
    monkeypatch.setattr(server, 'ALERT_TO', '')
    monkeypatch.setattr(server, 'TWILIO_MODE', 'standard')
    monkeypatch.setattr(server, 'TWILIO_TRIAL_TEMPLATE', 'sms_internal_alerts')
    monkeypatch.setattr(server, 'DEPARTURE_COOLDOWN_SECONDS', 0)
    return server


def test_health_and_ui(kit):
    with TestClient(kit.app) as c:
        assert c.get('/health').json()['ok']
        page = c.get('/')
        assert page.status_code == 200
        assert 'getUserMedia' in page.text and 'Run NDI extraction' in page.text
        j = c.get('/api/overview').json()
        assert j['capabilities']['vision_provider'] == 'drex'
        assert not j['capabilities']['vision']


def test_simulated_departure_and_safe_sms_preview(kit, monkeypatch):
    async def fake_send(msg):
        pytest.fail('Simulated departure must not send a real SMS')
    monkeypatch.setattr(kit, 'send_sms', fake_send)
    with TestClient(kit.app) as c:
        assert c.post('/api/simulate', json={'detected': True}).json()['confirmed_present'] is False
        assert c.post('/api/simulate', json={'detected': True}).json()['confirmed_present'] is True
        assert c.post('/api/simulate', json={'detected': False}).json()['departure_alert'] is False
        last = c.post('/api/simulate', json={'detected': False}).json()
        assert last['departure_alert'] is True
        assert last['sms_status'] == 'simulation'
        assert last['alert_preview'].startswith('Hey Ben! 🚗 An SFMTA parking-enforcement vehicle')
        overview = c.get('/api/overview').json()
        assert overview['state']['observation_timer']['minutes'] == 120
        assert overview['state']['timer'] is None
        assert any(e['type'] == 'departure' for e in overview['events'])
        assert any(e['type'] == 'notification' and last['alert_preview'] in e['detail'] for e in overview['events'])


def mock_nace(kit, monkeypatch, responder):
    orig = httpx.AsyncClient
    transport = httpx.MockTransport(responder)
    monkeypatch.setattr(kit.httpx, 'AsyncClient', lambda *a, **kw: orig(transport=transport, **kw))


@pytest.fixture
def twilio(kit, monkeypatch):
    monkeypatch.setattr(kit, 'TWILIO_ACCOUNT_SID', 'AC' + 'a'*32)
    monkeypatch.setattr(kit, 'TWILIO_AUTH_TOKEN', 'private-test-token')
    monkeypatch.setattr(kit, 'ALERT_TO', '+14155550123')
    return kit


@pytest.mark.parametrize('sender', ['', '+17375550111'])
def test_trial_sms_template_and_delivery_receipt(twilio, monkeypatch, sender):
    monkeypatch.setattr(twilio, 'TWILIO_MODE', 'trial')
    monkeypatch.setattr(twilio, 'TWILIO_FROM', sender)
    sid = 'SM' + 'b'*32

    def respond(req):
        assert req.url.host == 'api.twilio.com'
        if req.method == 'POST':
            assert req.url.path.endswith('/Messages.json')
            expected = {
                'To': ['+14155550123'], 'Body': ['sms_internal_alerts']}
            if sender:
                expected['From'] = [sender]
            assert parse_qs(req.content.decode()) == expected
            return httpx.Response(201, json={'sid': sid, 'status': 'queued'})
        assert req.url.path.endswith(f'/Messages/{sid}.json')
        return httpx.Response(200, json={'sid': sid, 'status': 'delivered'})

    mock_nace(twilio, monkeypatch, respond)
    with TestClient(twilio.app) as c:
        result = c.post('/api/sms/test').json()
        assert result['sid'] == sid and result['status'] == 'queued'
        assert result['template'] == 'sms_internal_alerts'
        asyncio.run(twilio.poll_sms_delivery())
        event = c.get('/api/overview').json()['events'][0]
        assert event['id'] == result['event_id']
        assert event['sms_status'] == 'delivered'
        assert event['sms_template'] == 'sms_internal_alerts'


@pytest.mark.parametrize('offset, start, end', [
    ('2026-11-14T21:30:00+00:00', '1:30 PM', '3:30 PM'),
    ('2026-03-08T01:30:00-08:00', '1:30 AM', '4:30 AM'),
    ('2026-11-01T01:30:00-07:00', '1:30 AM', '2:30 AM'),
])
def test_personalized_message_uses_elapsed_time_across_dst(kit, offset, start, end):
    assert kit.departure_message(datetime.fromisoformat(offset).timestamp()) == (
        f'Hey Ben! 🚗 An SFMTA parking-enforcement vehicle was last seen at {start}.\n\n'
        f'Your 2-hour observation timer ends at {end}.')


def test_twilio_rejection_and_ambiguous_submission(twilio, monkeypatch):
    monkeypatch.setattr(twilio, 'TWILIO_MODE', 'trial')
    responses = iter(('rejected', 'timeout'))

    def respond(req):
        if next(responses) == 'timeout':
            raise httpx.ReadTimeout('private-test-token', request=req)
        return httpx.Response(400, json={'code': 21608, 'message': 'private-test-token'})

    mock_nace(twilio, monkeypatch, respond)
    with TestClient(twilio.app) as c:
        rejected = c.post('/api/sms/test')
        assert rejected.json()['status'] == 'rejected'
        assert 'Verify the recipient' in rejected.json()['error']
        unknown = c.post('/api/sms/test')
        assert unknown.json()['status'] == 'submission_unknown'
        assert 'private-test-token' not in rejected.text + unknown.text


@pytest.mark.parametrize('mode', ['trial', 'standard'])
def test_three_wheel_demo_sends_at_first_plausible_sighting(twilio, monkeypatch, mode):
    monkeypatch.setattr(twilio, 'THREE_WHEEL_DEMO_ALERTS', True)
    monkeypatch.setattr(twilio, 'NACE_API_KEY', 'demo-nace-key')
    monkeypatch.setattr(twilio, 'TWILIO_MODE', mode)
    monkeypatch.setattr(twilio, 'TWILIO_FROM', '+14155550124')
    monkeypatch.setattr(twilio, 'DEPARTURE_COOLDOWN_SECONDS', 60)
    monkeypatch.setattr(twilio, 'DEPARTURE_MISSES', 2)
    scores = iter((.1, .4, .9, .01, .01, .8))
    messages = []

    def respond(req):
        if req.url.host == 'api.twilio.com':
            messages.append(parse_qs(req.content.decode())['Body'][0])
            return httpx.Response(201, json={'sid': 'SM' + 'd'*32, 'status': 'queued'})
        prompt = json.loads(req.content)['state']
        assert 'All later images are examples' in prompt and 'Ignore city, agency and livery' in prompt
        return httpx.Response(200, json={'answers': {'enforcement_vehicle': {'noul': next(scores)}}})

    mock_nace(twilio, monkeypatch, respond)
    with TestClient(twilio.app) as c:
        assert not c.get('/api/overview').json()['state']['automatic_alerts_enabled']
        for index in range(6):
            result = c.post('/api/frame', json={'image': 'data:image/jpeg;base64,AAAA'}).json()
            assert result['sighting_alert'] is (index == 1)
            assert not result['departure_alert']
            if index == 1:
                alert = result['alert_preview']
                overview = c.get('/api/overview').json()
                started_at = overview['state']['observation_timer']['started_at']
                assert started_at == overview['state']['last_sighting']
        final = c.get('/api/overview').json()
        assert final['state']['observation_timer']['started_at'] == started_at
        assert final['sms_preview'] == alert
        assert messages == ['sms_internal_alerts' if mode == 'trial' else alert]


def test_confirmed_live_departure_sends_once_during_cooldown(twilio, monkeypatch):
    monkeypatch.setattr(twilio, 'TWILIO_FROM', '+14155550124')
    monkeypatch.setattr(twilio, 'DEPARTURE_COOLDOWN_SECONDS', 60)
    monkeypatch.setattr(twilio, 'SIGHTING_HITS', 2)
    monkeypatch.setattr(twilio, 'DEPARTURE_MISSES', 2)
    frames = iter((True, True, False, False, False, True, True, False, False))
    sent = []

    async def classify(image):
        return {'is_enforcement': next(frames), 'confidence': .95, 'reason': 'test frame'}

    def respond(req):
        body = parse_qs(req.content.decode())
        assert body['From'] == ['+14155550124']
        sent.append(body['Body'][0])
        return httpx.Response(201, json={'sid': 'SM' + 'c'*32, 'status': 'queued'})

    monkeypatch.setattr(twilio, 'classify_frame', classify)
    mock_nace(twilio, monkeypatch, respond)
    with TestClient(twilio.app) as c:
        with twilio.db() as con:
            state = twilio.get_state(con)
            state['automatic_alerts_enabled'] = True
            twilio.save_state(con, state)
        for i in range(9):
            reply = c.post('/api/frame', json={'image': 'data:image/jpeg;base64,AAAA'}).json()
            assert reply['departure_alert'] is (i == 3)
            if i == 3:
                state = c.get('/api/overview').json()['state']
                assert state['first_seen'] <= state['last_sighting'] < state['last_departure']
                assert state['observation_timer']['started_at'] == state['last_sighting']
                assert sent == [twilio.departure_message(state['last_sighting'])]
        events = c.get('/api/overview').json()['events']
        assert len(sent) == 1
        assert [e['sms_status'] for e in events if e['sms_sid']] == ['queued']
        assert sum(e['type'] == 'notification' for e in events) == 2


def test_drex_image_decision_real_payload_mocked(kit, monkeypatch):
    monkeypatch.setattr(kit, 'NACE_API_KEY', 'dummy_local_only')
    def responder(request):
        assert request.url.path == '/v1/systemone'
        assert request.headers['authorization'] == 'Bearer dummy_local_only'
        j = json.loads(request.content)
        assert j['model'] == 'drex-max-v1.1'
        assert j['images'][0].startswith('data:image/jpeg;base64,')
        assert len(j['images']) == 7
        assert 'positive examples' in j['state'] and 'negative lookalikes' in j['state']
        assert j['questions']['enforcement_vehicle']['type'] == 'noul'
        return httpx.Response(200, json={'answers':{'enforcement_vehicle':{'type':'noul','noul':0.973}},'evaluation_time_ms':108})
    mock_nace(kit, monkeypatch, responder)
    with TestClient(kit.app) as c:
        for i in range(2):
            r = c.post('/api/frame', json={'image':'data:image/jpeg;base64,AAAA'})
            assert r.status_code == 200, r.text
            assert r.json()['confidence'] == .973
            assert r.json()['provider'] == 'drex'
        assert r.json()['confirmed_present'] is True


def test_reference_evaluation_passes_holdouts_and_enables_alerts(kit, monkeypatch):
    monkeypatch.setattr(kit, 'NACE_API_KEY', 'dummy_local_only')

    async def classify(image, examples, query_title='Live camera frame'):
        is_target = 'Held-out positive test' in query_title
        return {'is_enforcement': is_target, 'confidence': .98 if is_target else .02}

    monkeypatch.setattr(kit, 'classify_drex', classify)
    with TestClient(kit.app) as c:
        examples = c.get('/api/reference/examples').json()['examples']
        assert len(examples) == 6
        assert {item['view'] for item in examples if item['label'] == 'positive'} == {'Front', 'Side', 'Rear'}
        response = c.post('/api/reference/evaluate')
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['passed'] is True
        assert result['false_positives'] == []
        assert result['false_negatives'] == []
        assert result['automatic_alerts_enabled'] is True


def test_reference_false_positive_keeps_alerts_disabled(kit, monkeypatch):
    monkeypatch.setattr(kit, 'NACE_API_KEY', 'dummy_local_only')

    async def classify(image, examples, query_title='Live camera frame'):
        return {'is_enforcement': True, 'confidence': .98}

    monkeypatch.setattr(kit, 'classify_drex', classify)
    with TestClient(kit.app) as c:
        response = c.post('/api/reference/evaluate')
        assert response.status_code == 200
        result = response.json()
        assert result['passed'] is False
        assert len(result['false_positives']) == 3
        assert result['automatic_alerts_enabled'] is False
        assert c.post('/api/reference/alerts', json={'enabled': True}).status_code == 409


def test_live_departure_alert_is_suppressed_until_reference_pass(kit, monkeypatch):
    detections = iter((True, True, False, False))

    async def fake_classify(image):
        detected = next(detections)
        return {'is_enforcement': detected, 'confidence': .9 if detected else .1,
                'reason': 'reference test', 'provider': 'drex'}

    monkeypatch.setattr(kit, 'classify_frame', fake_classify)
    with TestClient(kit.app) as c:
        responses = [c.post('/api/frame', json={'image': 'data:image/jpeg;base64,AAAA'}).json()
                     for _ in range(4)]
        assert responses[1]['confirmed_present'] is True
        assert responses[3]['confirmed_present'] is False
        assert responses[3]['departure_alert'] is False
        assert responses[3]['alert_preview'] is None
        overview = c.get('/api/overview').json()
        assert overview['state']['observation_timer'] is None
        assert overview['state']['automatic_alerts_enabled'] is False
        assert any('held until reference-image evaluation passes' in item['detail']
                   for item in overview['events'])


def test_ndi_extract_request_mocked(kit, monkeypatch):
    monkeypatch.setattr(kit, 'NACE_API_KEY', 'dummy_local_only')
    def responder(req):
        if req.method == 'POST':
            assert req.url.path == '/v1/documents/extract'
            assert req.url.params['wait_seconds'] == '25'
            j = json.loads(req.content)
            assert j['source']['url'].startswith('https://www.sfmta.com/')
            assert j['schema']['type'] == 'object'
            return httpx.Response(200,json={'job_id':'testjob123','status':'succeeded','result':{
                'result_type':'extract','data':{'street_sweeping':'Follow posted notices'},
                'fields':[{'path':'/street_sweeping','value':'Follow posted notices','status':'found','citations':[{'source_text':'Street cleaning sign','location':{'page':2}}]}]
            }})
        assert req.url.path == '/v1/documents/jobs/testjob123'
        return httpx.Response(200,json={'job_id':'testjob123','status':'succeeded','result':{'data':{'street_sweeping':'Follow posted notices'},'fields':[]}})
    mock_nace(kit, monkeypatch, responder)
    with TestClient(kit.app) as c:
        r = c.post('/api/ndi/rules')
        assert r.status_code == 200, r.text
        assert r.json()['status'] == 'succeeded'
        assert r.json()['fields'][0]['citations'][0]['source_text'] == 'Street cleaning sign'
        assert c.get('/api/ndi/rules/testjob123').status_code == 200
        assert c.get('/api/ndi/rules/garbage!').status_code in (400,404,422)


def test_auth_and_invalid_image(kit, monkeypatch):
    monkeypatch.setattr(kit,'WATCH_KEY','secure_test_key')
    monkeypatch.setattr(kit,'NACE_API_KEY','dummy')
    with TestClient(kit.app) as c:
        assert c.get('/api/overview').status_code == 401
        headers={'X-Watch-Key':'secure_test_key'}
        assert c.get('/api/overview',headers=headers).status_code == 200
        r=c.post('/api/frame', headers=headers,json={'image':'not-a-jpeg'})
        assert r.status_code == 400


def test_nace_key_catalog_check_mocked(kit, monkeypatch):
    monkeypatch.setattr(kit, 'NACE_API_KEY', 'fresh_test_key')
    def responder(req):
        assert req.method == 'GET'
        assert req.url.path == '/v1/models'
        assert req.headers['Authorization'] == 'Bearer fresh_test_key'
        return httpx.Response(200,json={'models':[{'name':'drex-max-v1.1'},{'name':'drex-v2.0'}]})
    mock_nace(kit, monkeypatch, responder)
    with TestClient(kit.app) as c:
        result = c.get('/api/nace/check')
        assert result.status_code == 200, result.text
        assert result.json()['authenticated'] is True
        assert result.json()['drex_max_v1_1_listed'] is True
        assert 'fresh_test_key' not in result.text


def test_ndi_parse_uses_console_route_mocked(kit, monkeypatch):
    monkeypatch.setattr(kit,'NACE_API_KEY','fake_local_test_key')
    monkeypatch.setattr(kit, 'SFMTA_NDI_WORKSPACE_ID', 'workspace123')
    monkeypatch.setattr(kit, 'SFMTA_NDI_FILE_ID', 'file123')
    def responder(req):
        if req.method == 'POST':
            assert req.url.path == '/v1/documents/parse'
            data = json.loads(req.content)
            assert data['source'] == {'type': 'workspace_file', 'workspace_id': 'workspace123', 'file_id': 'file123'}
            assert data['output']['formats'] == ['markdown']
            return httpx.Response(200,json={'job_id':'parsejob123','status':'succeeded','result':{
                'result_type':'parse','file_name':'sfmta_parking_guide.pdf',
                'document':{'markdown':'# Parking Guide\nCheck posted signs.'},'ocr_applied':False}})
        assert req.url.path == '/v1/documents/jobs/parsejob123'
        return httpx.Response(200,json={'job_id':'parsejob123','status':'succeeded','result':{'document':{'markdown':'hello'}}})
    mock_nace(kit, monkeypatch, responder)
    with TestClient(kit.app) as c:
        result=c.post('/api/ndi/parse')
        assert result.status_code == 200, result.text
        assert 'Check posted signs' in result.json()['markdown_preview']
        assert c.get('/api/ndi/parse/parsejob123').json()['markdown_preview']=='hello'


def test_optional_workspace_search_mocked(kit, monkeypatch):
    monkeypatch.setattr(kit,'NACE_API_KEY','fake_local_test_key')
    monkeypatch.setattr(kit,'NACE_WORKSPACE_ID','11111111-2222-4333-8444-555555555555')
    def responder(req):
        if req.method=='POST':
            assert req.url.path.endswith('/automatic-search')
            assert json.loads(req.content)['mode']=='fact'
            return httpx.Response(200,json={'job_id':'searchjob123','status':'succeeded',
                'result':{'output':{'answer':'Park according to signs', 'confidence':'high',
                                   'evidences':[{'page':2,'source_path':'rules.pdf','quote':'2 hours'}]}}})
        return httpx.Response(200,json={'job_id':'searchjob123','status':'succeeded','result':{'output':{'answer':'Ready'}}})
    mock_nace(kit, monkeypatch, responder)
    with TestClient(kit.app) as c:
        result=c.post('/api/nace/search',json={'query':'What is the limit?'})
        assert result.status_code == 200
        assert result.json()['answer']=='Park according to signs'
        assert result.json()['evidences'][0]['page']==2
        assert c.get('/api/nace/search/searchjob123').json()['answer']=='Ready'


def test_no_search_without_configured_workspace(kit, monkeypatch):
    monkeypatch.setattr(kit,'NACE_API_KEY','fake_local_test_key')
    monkeypatch.setattr(kit,'NACE_WORKSPACE_ID','')
    with TestClient(kit.app) as c:
        assert c.post('/api/nace/search',json={'query':'What is the posted time limit?'}).status_code==503
