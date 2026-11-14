"""Parking Watch: a minimal, deployable prototype.

Run with: uvicorn server:app --host 0.0.0.0 --port 8000

No camera frames or license plates are retained. Vision and SMS are opt-in
and are inactive unless their environment variables are configured.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv('DATABASE_PATH', str(BASE / 'parking_watch.sqlite3')))
WATCH_KEY = os.getenv('WATCH_KEY', '')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
NACE_API_KEY = os.getenv('NACE_API_KEY', '')
VISION_PROVIDER = os.getenv('VISION_PROVIDER', 'drex').strip().lower()
NACE_MODEL = os.getenv('NACE_MODEL', 'drex-max-v1.1')
NACE_WORKSPACE_ID = os.getenv('NACE_WORKSPACE_ID', '').strip()  # optional workspace with ingested parking files
NACE_BASE_URL = 'https://console.nace.ai'
SF_RULES_PDF = 'https://www.sfmta.com/media/1379/download?inline='
SFMTA_NDI_WORKSPACE_ID = os.getenv('SFMTA_NDI_WORKSPACE_ID', '').strip()
SFMTA_NDI_FILE_ID = os.getenv('SFMTA_NDI_FILE_ID', '').strip()
SIGHTING_HITS = max(1, int(os.getenv('SIGHTING_HITS', '2')))
DEPARTURE_MISSES = max(1, int(os.getenv('DEPARTURE_MISSES', '2')))
DEPARTURE_COOLDOWN_SECONDS = max(0, int(os.getenv('DEPARTURE_COOLDOWN_SECONDS', '30')))
SCAN_INTERVAL_MS = max(750, int(os.getenv('SCAN_INTERVAL_MS', '1800')))
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID', '').strip()
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN', '').strip()
TWILIO_FROM = os.getenv('TWILIO_FROM', '').strip()
ALERT_TO = os.getenv('ALERT_TO', '').strip()
TWILIO_MODE = os.getenv('TWILIO_MODE', 'standard').strip().lower()
TWILIO_TRIAL_TEMPLATE = os.getenv('TWILIO_TRIAL_TEMPLATE', 'sms_internal_alerts').strip()
PACIFIC = ZoneInfo('America/Los_Angeles')
OBSERVATION_MINUTES = 120
SMS_PENDING = ('accepted', 'queued', 'sending', 'sent', 'scheduled')
DEMO_MODE = os.getenv('DEMO_MODE', 'false').lower() == 'true'
THREE_WHEEL_DEMO_ALERTS = DEMO_MODE and os.getenv('THREE_WHEEL_DEMO_ALERTS', 'false').lower() == 'true'
THREE_WHEEL_DEMO_THRESHOLD = 0.35
CONFIDENCE_THRESHOLD = float(os.getenv('CONFIDENCE_THRESHOLD', '0.76'))
APP_URL = os.getenv('APP_URL', '')
REFERENCE_DIR = BASE / 'reference_images'
REFERENCE_MANIFEST = REFERENCE_DIR / 'manifest.json'

app = FastAPI(title='Parking Watch', version='0.3.0')
lock = asyncio.Lock()


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts REAL NOT NULL, type TEXT NOT NULL, detail TEXT NOT NULL, confidence REAL, source TEXT NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, data TEXT NOT NULL)')
    con.execute('''CREATE TABLE IF NOT EXISTS sms_delivery (
        event_id INTEGER PRIMARY KEY REFERENCES events(id),
        mode TEXT NOT NULL, template TEXT, status TEXT NOT NULL, sid TEXT, error TEXT,
        checked_at REAL NOT NULL
    )''')
    con.commit()
    return con


def get_state(con):
    row = con.execute("SELECT data FROM state WHERE key='main'").fetchone()
    default = {'present': False, 'misses': 0, 'hits': 0, 'timer': None, 'timer_warned': False,
               'last_scan': None, 'first_seen': None, 'candidate_first_seen': None, 'detection_source': None,
               'last_sighting': None, 'last_departure': None,
               'observation_timer': None, 'last_alert_at': None,
               'reference_evaluation': None, 'automatic_alerts_enabled': False}
    if row:
        default.update(json.loads(row['data']))
    return default


def save_state(con, state):
    con.execute("INSERT INTO state(key,data) VALUES('main',?) ON CONFLICT(key) DO UPDATE SET data=excluded.data", (json.dumps(state),))


def record(con, event_type, detail, confidence=None, source='live'):
    cur = con.execute('INSERT INTO events(ts,type,detail,confidence,source) VALUES (?,?,?,?,?)', (time.time(), event_type, detail, confidence, source))
    return cur.lastrowid


def auth(x_watch_key: str | None):
    if not WATCH_KEY:
        # Local testing remains usable with no key. NEVER expose an unprotected
        # deployment: set WATCH_KEY on any public host.
        if os.getenv('REQUIRE_AUTH', 'false').lower() == 'true':
            raise HTTPException(503, 'WATCH_KEY is required for production')
        return
    import secrets
    if not x_watch_key or not secrets.compare_digest(x_watch_key, WATCH_KEY):
        raise HTTPException(401, 'Invalid access key')


def sms_setup_error():
    if TWILIO_MODE not in ('standard', 'trial'):
        return 'Set TWILIO_MODE to standard or trial.'
    required = {'TWILIO_ACCOUNT_SID': TWILIO_ACCOUNT_SID, 'TWILIO_AUTH_TOKEN': TWILIO_AUTH_TOKEN,
                'ALERT_TO': ALERT_TO}
    if TWILIO_MODE == 'standard':
        required['TWILIO_FROM'] = TWILIO_FROM
    missing = [name for name, value in required.items() if not value]
    if missing:
        return 'Set ' + ', '.join(missing) + ' in .env and restart Parking Watch.'
    if not re.fullmatch(r'AC[0-9a-fA-F]{32}', TWILIO_ACCOUNT_SID):
        return 'Set TWILIO_ACCOUNT_SID to the Account SID from your Twilio Console.'
    if not re.fullmatch(r'\+[1-9][0-9]{1,14}', ALERT_TO):
        return 'Set ALERT_TO to a phone number in international E.164 format.'
    if TWILIO_MODE == 'trial' and TWILIO_TRIAL_TEMPLATE not in ('sms_internal_alerts', 'sms_account_alerts'):
        return 'Set TWILIO_TRIAL_TEMPLATE to sms_internal_alerts or sms_account_alerts.'
    return None


def sms_configured():
    return sms_setup_error() is None


def twilio_url(sid=None):
    path = 'Messages' if sid is None else f'Messages/{sid}'
    return f'https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/{path}.json'


def twilio_error(code):
    hints = {
        20003: 'Check the Account SID and Auth Token in Twilio Console.',
        21211: 'Check the recipient number and its international format.',
        21408: 'Check Messaging geographic permissions for the destination.',
        21606: 'Check that the configured sender can send SMS on this account.',
        21608: 'Verify the recipient in Twilio Console before sending from a trial account.',
        30007: 'The carrier filtered the message. Check the Twilio Messaging Logs.',
        30454: 'The trial SMS allowance has been exhausted.',
        572002: 'Use the trial sender shown for this recipient in Twilio Console.',
        572005: 'Complete phone verification for the recipient in Twilio Console.',
        572006: 'Use a predefined SMS template from the Twilio trial Console.',
    }
    if isinstance(code, int) and not isinstance(code, bool):
        return f'Twilio error {code}. ' + hints.get(code, 'See Twilio Messaging Logs for details.')
    return None


def twilio_result(payload):
    sid = payload.get('sid')
    status = payload.get('status')
    allowed = (*SMS_PENDING, 'delivered', 'undelivered', 'failed', 'canceled')
    if not isinstance(sid, str) or not re.fullmatch(r'SM[0-9a-fA-F]{32}', sid) or status not in allowed:
        raise ValueError('Unexpected Twilio response')
    return {'sid': sid, 'status': status, 'error': twilio_error(payload.get('error_code'))}


async def send_sms(message: str):
    error = sms_setup_error()
    if error:
        return {'status': 'not_configured', 'sid': None, 'error': error}
    data = {'To': ALERT_TO, 'Body': TWILIO_TRIAL_TEMPLATE if TWILIO_MODE == 'trial' else message}
    if TWILIO_FROM:
        data['From'] = TWILIO_FROM
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(twilio_url(), auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), data=data)
        if response.is_success:
            return twilio_result(response.json())
        error = f'Twilio returned HTTP {response.status_code}. Check Twilio Messaging Logs.'
        try:
            error = twilio_error(response.json().get('code')) or error
        except (ValueError, AttributeError):
            pass
        status = 'submission_unknown' if response.status_code >= 500 else 'rejected'
        return {'status': status, 'sid': None, 'error': error}
    except (httpx.HTTPError, ValueError, AttributeError):
        # A timeout or unreadable response can occur after Twilio accepted the POST.
        return {'status': 'submission_unknown', 'sid': None,
                'error': 'Twilio acceptance could not be verified. Check Messaging Logs before retrying.'}


def record_sms(con, detail, source):
    event_id = record(con, 'notification', detail, source=source)
    con.execute('''INSERT INTO sms_delivery (event_id, mode, template, status, checked_at)
                   VALUES (?, ?, ?, ?, ?)''',
                (event_id, TWILIO_MODE, TWILIO_TRIAL_TEMPLATE if TWILIO_MODE == 'trial' else None,
                 'submission_unknown', time.time()))
    return event_id


def save_sms_status(event_id, result):
    with db() as con:
        con.execute('UPDATE sms_delivery SET status=?, sid=?, error=?, checked_at=? WHERE event_id=?',
                    (result['status'], result['sid'], result['error'], time.time(), event_id))


async def submit_sms(event_id, message):
    result = await send_sms(message)
    save_sms_status(event_id, result)
    return {**result, 'event_id': event_id, 'mode': TWILIO_MODE,
            'template': TWILIO_TRIAL_TEMPLATE if TWILIO_MODE == 'trial' else None}


async def poll_sms_delivery():
    if not sms_configured():
        return
    with db() as con:
        pending = con.execute('''SELECT s.event_id, s.sid, s.status FROM sms_delivery s
            JOIN events e ON e.id=s.event_id
            WHERE s.sid IS NOT NULL AND s.status IN ('accepted','queued','sending','sent','scheduled')
              AND e.ts > ? ORDER BY s.checked_at LIMIT 10''', (time.time() - 86400,)).fetchall()
    if not pending:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        async def check(row):
            sid = row['sid']
            try:
                response = await client.get(twilio_url(sid), auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
                response.raise_for_status()
                result = twilio_result(response.json())
                if result['sid'] != sid:
                    raise ValueError('Unexpected Message SID')
            except (httpx.HTTPError, ValueError, AttributeError):
                result = {'status': row['status'], 'sid': sid,
                          'error': 'Could not refresh delivery status. Check Twilio Messaging Logs.'}
            save_sms_status(row['event_id'], result)

        await asyncio.gather(*(check(row) for row in pending))


def departure_message(last_seen):
    def local(ts):
        return datetime.fromtimestamp(ts, PACIFIC).strftime('%I:%M %p').lstrip('0')

    ends_at = last_seen + OBSERVATION_MINUTES * 60
    return (f'Hey Ben! 🚗 An SFMTA parking-enforcement vehicle was last seen at {local(last_seen)}.\n\n'
            f'Your 2-hour observation timer ends at {local(ends_at)}.')


def vision_configured():
    return bool(NACE_API_KEY) if VISION_PROVIDER == 'drex' else bool(OPENAI_API_KEY)


def reference_examples():
    try:
        examples = json.loads(REFERENCE_MANIFEST.read_text())
    except (OSError, ValueError):
        raise HTTPException(503, 'Reference-image manifest is unavailable.')
    if not isinstance(examples, list) or not examples:
        raise HTTPException(503, 'No reference images are configured.')
    for item in examples:
        if (not isinstance(item, dict) or item.get('label') not in ('positive', 'negative')
                or not re.fullmatch(r'[a-z0-9_-]+\.jpg', item.get('file', ''))):
            raise HTTPException(503, 'Reference-image manifest contains an invalid entry.')
        if not (REFERENCE_DIR / item['file']).is_file():
            raise HTTPException(503, 'A configured reference image is missing.')
    return examples


def reference_image_data(item: dict):
    try:
        return 'data:image/jpeg;base64,' + base64.b64encode((REFERENCE_DIR / item['file']).read_bytes()).decode('ascii')
    except OSError:
        raise HTTPException(503, 'A configured reference image is missing.')


def validate_image(image_data: str):
    if len(image_data) > 1_650_000 or not image_data.startswith('data:image/jpeg;base64,'):
        raise HTTPException(400, 'Expected a JPEG data URI under about 1.2 MB')


async def classify_drex(image_data: str, examples: list[dict], query_title='Live camera frame', *, three_wheel_demo=False):
    state = ('Image 1 is the query image. Images 2 onward are labeled reference examples in this order: '
             + '; '.join(f"Image {index + 2} is a {item['label']} {item['view'].lower()} example: {item['title']}"
                         for index, item in enumerate(examples))
             + '. Identify an SFMTA three-wheeled parking-enforcement vehicle in Image 1 by comparing its vehicle '
             'design, markings, and livery to the positive examples. Use the negative lookalikes to reject similar '
             'vehicles from other cities or agencies. Do not classify a vehicle as SFMTA solely because it is a '
             'three-wheeler or has an amber light. Do not identify people or read license plates.')
    request = {
        'model': NACE_MODEL,
        'state': state,
        'images': [image_data] + [reference_image_data(item) for item in examples],
        'questions': {
            'enforcement_vehicle': {
                'type': 'noul',
                'instructions': ('Does Image 1 show an SFMTA three-wheeled parking-enforcement vehicle consistent '
                                 'with the labeled positive reference images and distinguishable from the negative '
                                 'lookalikes? Assess Image 1 only.'),
                'criteria': {
                    'true': 'The query matches the SFMTA vehicle design and identifying livery in positive references.',
                    'false': 'The query is not an SFMTA enforcement vehicle, including a similar vehicle from another agency.'
                }
            }
        }
    }
    threshold = CONFIDENCE_THRESHOLD
    if three_wheel_demo:
        threshold = THREE_WHEEL_DEMO_THRESHOLD
        request['state'] = (
            'Image 1 is the only image to classify. All later images are examples of three-wheeled vehicles '
            'that qualify, including GO-4 Interceptor carts from different cities. A photo shown on a phone '
            'or monitor qualifies. A matching body shape can be enough when a wheel or logo is hidden. '
            'Ignore city, agency and livery. Do not identify people or read license plates.'
        )
        question = request['questions']['enforcement_vehicle']
        question['instructions'] = 'Does Image 1 show a plausible three-wheeled vehicle? Assess Image 1 only.'
        question['criteria'] = {
            'true': 'A possible three-wheeled vehicle, including a compact cart resembling the examples.',
            'false': 'No vehicle, or a vehicle that is clearly two-wheeled or four-wheeled.',
        }
    try:
        async with httpx.AsyncClient(timeout=70) as client:
            resp = await client.post(f'{NACE_BASE_URL}/v1/systemone',
                headers={'Authorization': f'Bearer {NACE_API_KEY}'}, json=request)
            resp.raise_for_status()
            payload = resp.json()
        prob = float(payload['answers']['enforcement_vehicle']['noul'])
        if not 0 <= prob <= 1:
            raise ValueError('Probability outside 0-1')
        return {'is_enforcement': prob >= threshold, 'confidence': round(prob, 4),
                'reason': f'Reference-assisted Drex probability {prob:.1%}', 'provider': 'drex',
                'evaluation_time_ms': payload.get('evaluation_time_ms')}
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        code = getattr(getattr(exc, 'response', None), 'status_code', None)
        raise HTTPException(502, f'Drex analysis failed{f" (HTTP {code})" if code else ""}. Check NACE_API_KEY and model availability.')


async def classify_frame(image_data: str):
    """Model decision only; event and SMS logic happens in update_detection.

    Show an actual vehicle photo to a laptop webcam for a real model test.
    No claim is made that a passing vehicle scanned a plate.
    """
    validate_image(image_data)
    if VISION_PROVIDER == 'drex':
        if not NACE_API_KEY:
            raise HTTPException(503, 'Set NACE_API_KEY to enable Drex vision (or choose VISION_PROVIDER=openai).')
        examples = reference_examples()
        positives = sum(item['label'] == 'positive' for item in examples)
        negatives = sum(item['label'] == 'negative' for item in examples)
        if positives < 3 or negatives < 3 or len(examples) > 7:
            raise HTTPException(503, 'Drex reference matching needs at least three positive and three negative examples, with no more than seven.')
        if THREE_WHEEL_DEMO_ALERTS:
            return await classify_drex(image_data, examples, three_wheel_demo=True)
        return await classify_drex(image_data, examples)

    if VISION_PROVIDER != 'openai':
        raise HTTPException(500, 'VISION_PROVIDER must be drex or openai')
    if not OPENAI_API_KEY:
        raise HTTPException(503, 'Set OPENAI_API_KEY to enable OpenAI vision')
    instructions = (
        'Inspect the image for a San Francisco Municipal Transportation Agency (SFMTA) '
        'parking enforcement cart or clearly marked parking control vehicle, not a Muni transit bus. '
        'Exclude ordinary cars, police vehicles, scooters, motorcycles and delivery vehicles. '
        'Do not identify people or license plates. On uncertainty return false. '
        'Return only JSON: {"is_enforcement":true or false,"confidence":0.0 to 1.0,"reason":"short reason"}.'
    )
    threshold = CONFIDENCE_THRESHOLD
    if THREE_WHEEL_DEMO_ALERTS:
        threshold = THREE_WHEEL_DEMO_THRESHOLD
        instructions = (
            'Look for any plausible three-wheeled vehicle, including photos on a phone. Ignore agency and '
            'livery. Do not identify people or read license plates. Return only JSON with confidence as '
            'the probability of a three-wheeled vehicle and reason as a short string.'
        )
    request = {
        'model': OPENAI_MODEL, 'max_tokens': 130, 'response_format': {'type': 'json_object'},
        'messages': [{'role': 'user', 'content': [
            {'type': 'text', 'text': instructions},
            {'type': 'image_url', 'image_url': {'url': image_data, 'detail': 'low'}}
        ]}]
    }
    try:
        async with httpx.AsyncClient(timeout=35) as client:
            resp = await client.post('https://api.openai.com/v1/chat/completions',
                headers={'Authorization': f'Bearer {OPENAI_API_KEY}'}, json=request)
            resp.raise_for_status()
            result = json.loads(resp.json()['choices'][0]['message']['content'])
        conf = min(max(float(result.get('confidence', 0)), 0), 1)
        detected = conf >= threshold and (THREE_WHEEL_DEMO_ALERTS or bool(result.get('is_enforcement')))
        return {'is_enforcement': detected,
                'confidence': round(conf, 4), 'reason': str(result.get('reason', ''))[:120],
                'provider': 'openai'}
    except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError):
        raise HTTPException(502, 'OpenAI image analysis failed. Check key and model access.')


class Frame(BaseModel):
    image: str


class TimerRequest(BaseModel):
    minutes: int = Field(ge=1, le=1440)
    warn_minutes: int = Field(default=10, ge=0, le=180)


class SimulateRequest(BaseModel):
    detected: bool


class AlertSettings(BaseModel):
    enabled: bool


@app.get('/')
def home():
    return FileResponse(BASE/'static'/'index.html')


@app.get('/health')
def health():
    return {'ok': True, 'app': 'Parking Watch'}


@app.get('/api/overview')
async def overview(x_watch_key: str | None = Header(default=None)):
    auth(x_watch_key)
    with db() as con:
        state = get_state(con)
        events = [dict(row) for row in con.execute('''SELECT e.*, s.status AS sms_status, s.sid AS sms_sid,
                    s.mode AS sms_mode, s.template AS sms_template, s.error AS sms_error
                    FROM events e LEFT JOIN sms_delivery s ON s.event_id=e.id
                    ORDER BY e.id DESC LIMIT 100''')]
    preview_seen_at = state['last_sighting']
    if THREE_WHEEL_DEMO_ALERTS and state['observation_timer']:
        preview_seen_at = state['observation_timer']['started_at']
    return {'state': state, 'events': events,
            'sms_preview': departure_message(preview_seen_at) if preview_seen_at is not None else None,
            'capabilities': {'vision': vision_configured(), 'vision_provider': VISION_PROVIDER,
                             'ndi': bool(NACE_API_KEY), 'nace_search': bool(NACE_API_KEY and NACE_WORKSPACE_ID), 'scan_interval_ms': SCAN_INTERVAL_MS,
                             'sms': sms_configured(), 'sms_mode': TWILIO_MODE, 'sms_setup_error': sms_setup_error(),
                             'demo': DEMO_MODE, 'three_wheel_demo_alerts': THREE_WHEEL_DEMO_ALERTS,
                             'secured': bool(WATCH_KEY),
                             'reference_count': len(reference_examples()) if VISION_PROVIDER == 'drex' else 0},
            'server_time': time.time()}


@app.get('/api/reference/examples')
async def reference_list(x_watch_key: str | None = Header(default=None)):
    auth(x_watch_key)
    examples = reference_examples()
    return {'examples': [{key: item[key] for key in ('id', 'label', 'view', 'title')}
                         | {'image_url': f"/api/reference/images/{item['file']}"}
                         for item in examples]}


@app.get('/api/reference/images/{filename}')
async def reference_image(filename: str):
    allowed = {item['file'] for item in reference_examples()}
    if filename not in allowed:
        raise HTTPException(404, 'Reference image not found.')
    return FileResponse(REFERENCE_DIR / filename, media_type='image/jpeg')


@app.post('/api/reference/evaluate')
async def evaluate_references(x_watch_key: str | None = Header(default=None)):
    auth(x_watch_key)
    if VISION_PROVIDER != 'drex' or not NACE_API_KEY:
        raise HTTPException(503, 'Reference evaluation requires the configured Drex API key.')
    examples = reference_examples()
    positives = sum(item['label'] == 'positive' for item in examples)
    negatives = sum(item['label'] == 'negative' for item in examples)
    if positives < 3 or negatives < 3 or len(examples) > 7:
        raise HTTPException(503, 'Evaluation needs at least three positive and three negative reference images, with no more than seven.')

    async with lock:
        with db() as con:
            state = get_state(con)
            state['reference_evaluation'] = None
            state['automatic_alerts_enabled'] = False
            record(con, 'reference_evaluation', 'Reference holdout evaluation started. Automatic alerts paused.',
                   source='system')
            save_state(con, state)
            con.commit()

    results = []
    try:
        for held_out in examples:
            comparison = [item for item in examples if item['id'] != held_out['id']]
            decision = await classify_drex(reference_image_data(held_out), comparison,
                                           query_title=f"Held-out {held_out['label']} test ({held_out['view']})")
            results.append({'id': held_out['id'], 'title': held_out['title'],
                            'expected': held_out['label'] == 'positive',
                            'detected': decision['is_enforcement'],
                            'confidence': decision['confidence'],
                            'correct': decision['is_enforcement'] == (held_out['label'] == 'positive')})
    except HTTPException as exc:
        evaluation = {'passed': False, 'evaluated_at': time.time(),
                      'threshold': CONFIDENCE_THRESHOLD, 'tested': len(results),
                      'false_positives': [], 'false_negatives': [], 'results': results,
                      'error': exc.detail}
        async with lock:
            with db() as con:
                state = get_state(con)
                state['reference_evaluation'] = evaluation
                state['automatic_alerts_enabled'] = False
                record(con, 'reference_evaluation',
                       'Reference holdout evaluation failed before completion. Automatic alerts remain disabled.',
                       source='system')
                save_state(con, state)
                con.commit()
        raise exc

    false_positives = [item['id'] for item in results if item['expected'] is False and item['detected']]
    false_negatives = [item['id'] for item in results if item['expected'] is True and item['detected'] is False]
    passed = not false_positives and not false_negatives
    evaluation = {'passed': passed, 'evaluated_at': time.time(),
                  'threshold': CONFIDENCE_THRESHOLD, 'tested': len(results),
                  'false_positives': false_positives, 'false_negatives': false_negatives,
                  'results': results}
    async with lock:
        with db() as con:
            state = get_state(con)
            state['reference_evaluation'] = evaluation
            state['automatic_alerts_enabled'] = passed
            record(con, 'reference_evaluation',
                   f"Reference holdout evaluation {'passed' if passed else 'failed'}: "
                   f"{len(false_positives)} false positives, {len(false_negatives)} false negatives.",
                   source='system')
            save_state(con, state)
            con.commit()
    return {**evaluation, 'automatic_alerts_enabled': passed}


@app.post('/api/reference/alerts')
async def configure_reference_alerts(payload: AlertSettings,
                                     x_watch_key: str | None = Header(default=None)):
    auth(x_watch_key)
    async with lock:
        with db() as con:
            state = get_state(con)
            evaluation = state.get('reference_evaluation') or {}
            if payload.enabled and not evaluation.get('passed'):
                raise HTTPException(409, 'Run a passing reference-image evaluation before enabling automatic alerts.')
            state['automatic_alerts_enabled'] = payload.enabled
            record(con, 'reference_alerts',
                   'Automatic alerts enabled after reference evaluation.' if payload.enabled
                   else 'Automatic alerts disabled by dashboard setting.', source='system')
            save_state(con, state)
            con.commit()
    return {'automatic_alerts_enabled': payload.enabled}


async def update_detection(detected: bool, confidence: float, reason: str, source: str, *, observed_at=None):
    """Two positive frames then two negatives confirm a sighting and departure.

    Observation timer after departure is a product demo clock, NOT a legal parking reset.
    """
    sms_message = None
    sms_event = None
    sms_status = None
    sighting_alert = False
    demo_sighting = THREE_WHEEL_DEMO_ALERTS and source == 'vision'
    detection_source = 'three_wheel_demo' if demo_sighting else source
    required_hits = 1 if demo_sighting else SIGHTING_HITS
    async with lock:
        with db() as con:
            con.execute('BEGIN IMMEDIATE')
            state = get_state(con)
            now = time.time()
            seen_at = now if observed_at is None else observed_at
            state['last_scan'] = now
            if state['detection_source'] not in (None, detection_source):
                state.update(present=False, hits=0, misses=0, candidate_first_seen=None)
            state['detection_source'] = detection_source
            cooldown_elapsed = now - (state['last_alert_at'] or 0) >= DEPARTURE_COOLDOWN_SECONDS
            if detected:
                if not state['hits']:
                    state['candidate_first_seen'] = seen_at
                state['hits'] = state.get('hits', 0) + 1
                state['misses'] = 0
                if not state['present'] and state['hits'] >= required_hits:
                    state['present'] = True
                    state['first_seen'] = state['candidate_first_seen']
                    description = 'Possible three-wheeled vehicle spotted.' if demo_sighting else 'SFMTA-like vehicle confirmed in view.'
                    record(con, 'sighting', f'{description} {reason}', confidence, source)
                    if demo_sighting and cooldown_elapsed:
                        sighting_alert = True
                        state['last_alert_at'] = now
                        state['observation_timer'] = {'started_at': seen_at, 'minutes': OBSERVATION_MINUTES}
                        sms_message = departure_message(seen_at)
                        sms_event = record_sms(con, sms_message, source)
                if state['present']:
                    state['last_sighting'] = seen_at
            else:
                state['hits'] = 0
                state['candidate_first_seen'] = None
                if state['present']:
                    state['misses'] += 1
                    if state['misses'] >= DEPARTURE_MISSES:
                        state['present'] = False
                        state['misses'] = 0
                        state['last_departure'] = now
                        record(con, 'departure', 'Confirmed vehicle is no longer visible. This does not imply a plate scan.', None, source)
                        alert_authorized = source == 'simulation' or state.get('automatic_alerts_enabled', False)
                        if not demo_sighting:
                            if alert_authorized and cooldown_elapsed:
                                last_seen = state['last_sighting'] or now
                                state['observation_timer'] = {'started_at': last_seen, 'minutes': OBSERVATION_MINUTES}
                                sms_message = departure_message(last_seen)
                                if source == 'simulation':
                                    sms_status = 'simulation'
                                    record(con, 'notification', 'Simulation preview • ' + sms_message, source=source)
                                else:
                                    state['last_alert_at'] = now
                                    sms_event = record_sms(con, sms_message, source)
                            elif not alert_authorized:
                                record(con, 'notification', 'Automatic departure alert held until reference-image evaluation passes.',
                                       source=source)
                            else:
                                record(con, 'notification', 'Departure recorded. SMS skipped during the notification cooldown.',
                                       source=source)
            save_state(con, state)
            con.commit()
    if sms_event is not None:
        sms_status = (await submit_sms(sms_event, sms_message))['status']
    return {'detected': detected, 'confirmed_present': state['present'],
            'confirmation_hits': state['hits'], 'confidence': confidence, 'reason': reason,
            'sighting_alert': sighting_alert, 'departure_alert': bool(sms_message) and not sighting_alert,
            'sms_status': sms_status,
            'alert_preview': sms_message}


@app.post('/api/frame')
async def frame(payload: Frame, x_watch_key: str | None = Header(default=None)):
    auth(x_watch_key)
    observed_at = time.time()
    result = await classify_frame(payload.image)
    return {**result, **(await update_detection(result['is_enforcement'], result['confidence'], result['reason'],
                                                 'vision', observed_at=observed_at))}


@app.post('/api/simulate')
async def simulate(payload: SimulateRequest, x_watch_key: str | None = Header(default=None)):
    auth(x_watch_key)
    if not DEMO_MODE:
        raise HTTPException(403, 'Demo mode is disabled')
    return await update_detection(payload.detected, 0.99 if payload.detected else 0.0, 'Simulated test event', 'simulation')


async def nace_call(method: str, path: str, *, payload: dict | None = None):
    if not NACE_API_KEY:
        raise HTTPException(503, 'Set NACE_API_KEY to enable NDI document extraction')
    try:
        async with httpx.AsyncClient(timeout=75) as client:
            response = await client.request(method, NACE_BASE_URL + path,
                headers={'Authorization': f'Bearer {NACE_API_KEY}'}, json=payload)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        raise HTTPException(502, f'Nace API request failed{f" (HTTP {code})" if code else ""}.')


@app.get('/api/nace/check')
async def nace_check(x_watch_key: str | None = Header(default=None)):
    """Non-billable model catalog check, without sending an image or key to the browser."""
    auth(x_watch_key)
    if not NACE_API_KEY:
        raise HTTPException(503, 'Set NACE_API_KEY in .env, then restart the server.')
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f'{NACE_BASE_URL}/v1/models',
                headers={'Authorization': f'Bearer {NACE_API_KEY}'})
            r.raise_for_status()
            payload = r.json()
        items = payload if isinstance(payload, list) else payload.get('data', payload.get('models', []))
        names = set()
        for item in items:
            if isinstance(item, dict):
                name = item.get('name') or item.get('id') or item.get('model')
                if name:
                    names.add(str(name))
        return {'authenticated': True, 'drex_max_v1_1_listed': NACE_MODEL in names,
                'models': sorted(names)[:20], 'document_processing_endpoint': '/v1/documents/parse',
                'workspace_search_configured': bool(NACE_WORKSPACE_ID),
                'note': 'This checks model catalog access; it does not run a live image or document extraction.'}
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, f'Nace authentication/catalog check failed (HTTP {exc.response.status_code}).')
    except (httpx.HTTPError, ValueError, TypeError):
        raise HTTPException(502, 'Nace model catalog check failed. Check internet access and retry.')


def sfmta_document_source():
    if SFMTA_NDI_WORKSPACE_ID and SFMTA_NDI_FILE_ID:
        return {'type': 'workspace_file', 'workspace_id': SFMTA_NDI_WORKSPACE_ID,
                'file_id': SFMTA_NDI_FILE_ID}
    return {'type': 'url', 'url': SF_RULES_PDF, 'file_name': 'sfmta_parking_guide.pdf'}


def parse_result_view(job: dict):
    result = job.get('result') or {}
    document = result.get('document') or {}
    preview = document.get('markdown') or document.get('text') or ''
    return {'job_id': job.get('job_id'), 'status': job.get('status'),
            'markdown_preview': str(preview)[:6500], 'file_name': result.get('file_name'),
            'ocr_applied': result.get('ocr_applied'),
            'error': (job.get('error') or {}).get('message') if isinstance(job.get('error'), dict) else None,
            'source_url': SF_RULES_PDF, 'source_note': 'Historical SFMTA brochure, not a substitute for current signs.'}


@app.post('/api/ndi/parse')
async def ndi_parse(x_watch_key: str | None = Header(default=None)):
    """Runs the very same NDI Parse route shown in the Nace API console screenshots."""
    auth(x_watch_key)
    payload = {'source': sfmta_document_source(),
               'page_ranges': [{'start': 1, 'end': 3}],
               'output': {'formats': ['markdown']}}
    return parse_result_view(await nace_call('POST', '/v1/documents/parse?wait_seconds=25', payload=payload))


@app.get('/api/ndi/parse/{job_id}')
async def ndi_parse_status(job_id: str, x_watch_key: str | None = Header(default=None)):
    auth(x_watch_key)
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', job_id):
        raise HTTPException(400, 'Invalid job ID')
    return parse_result_view(await nace_call('GET', '/v1/documents/jobs/' + job_id))


class ParkingSearchRequest(BaseModel):
    query: str = Field(min_length=5, max_length=350)


def parking_search_result(job: dict):
    result = job.get('result') or {}
    output = result.get('output') or {}
    content = output.get('answer')
    if isinstance(content, dict):
        confidence = content.get('confidence')
        evidences = content.get('evidences') or []
        answer = content.get('answer')
    else:
        confidence = output.get('confidence')
        evidences = output.get('evidences') or []
        answer = content
    return {'job_id': job.get('job_id'), 'status': job.get('status'),
            'answer': str(answer)[:4000] if answer is not None else None,
            'confidence': confidence,
            'evidences': [{'page': x.get('page'), 'source_path': x.get('source_path'),
                           'quote': str(x.get('quote', ''))[:450]} for x in evidences[:5] if isinstance(x, dict)],
            'error': (job.get('error') or {}).get('message') if isinstance(job.get('error'), dict) else None}


@app.post('/api/nace/search')
async def nace_search(request: ParkingSearchRequest, x_watch_key: str | None = Header(default=None)):
    auth(x_watch_key)
    if not NACE_WORKSPACE_ID:
        raise HTTPException(503, 'Set NACE_WORKSPACE_ID for a Nace workspace containing ingested, ready parking-rule documents.')
    if not re.fullmatch(r'[0-9a-fA-F-]{36}', NACE_WORKSPACE_ID):
        raise HTTPException(400, 'Invalid workspace ID; use the UUID in your Nace workspace URL.')
    job = await nace_call('POST', f'/v1/workspaces/{NACE_WORKSPACE_ID}/automatic-search?wait_seconds=25',
                          payload={'query': request.query, 'mode': 'fact'})
    return parking_search_result(job)


@app.get('/api/nace/search/{job_id}')
async def nace_search_status(job_id: str, x_watch_key: str | None = Header(default=None)):
    auth(x_watch_key)
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', job_id):
        raise HTTPException(400, 'Invalid job ID')
    return parking_search_result(await nace_call('GET', '/v1/documents/jobs/' + job_id))


def ndi_result_view(job: dict):
    result = job.get('result') or {}
    return {'status': job.get('status'), 'job_id': job.get('job_id'),
            'data': result.get('data'), 'fields': result.get('fields', []),
            'error': (job.get('error') or {}).get('message') if isinstance(job.get('error'), dict) else None,
            'source_url': SF_RULES_PDF, 'source_note': 'SFMTA published brochure; historical reference, verify current signs/rules.'}


@app.post('/api/ndi/rules')
async def ndi_rules(x_watch_key: str | None = Header(default=None)):
    """Actual NDI Extract request with cited SFMTA source; may return pending job."""
    auth(x_watch_key)
    payload = {
        'source': sfmta_document_source(),
        'schema': {'type': 'object', 'properties': {
            'parking_time_limit': {'type': 'string', 'description': 'Describe parking time limits mentioned in the brochure'},
            'permit_area_movement': {'type': 'string', 'description': 'Find any requirement to move the vehicle after posted parking limit'},
            'street_sweeping': {'type': 'string', 'description': 'Find restrictions relating to street sweeping'},
        }},
        'instructions': 'Extract only what this published SFMTA guide explicitly says. Do not invent rules. Return cited evidence.'
    }
    job = await nace_call('POST', '/v1/documents/extract?wait_seconds=25', payload=payload)
    return ndi_result_view(job)


@app.get('/api/ndi/rules/{job_id}')
async def ndi_rules_status(job_id: str, x_watch_key: str | None = Header(default=None)):
    auth(x_watch_key)
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', job_id):
        # Nace job ids may contain dashes and are safe as a single path segment.
        raise HTTPException(400, 'Invalid job ID')
    job = await nace_call('GET', '/v1/documents/jobs/' + job_id)
    return ndi_result_view(job)


@app.post('/api/timer/start')
async def timer_start(payload: TimerRequest, x_watch_key: str | None = Header(default=None)):
    auth(x_watch_key)
    if payload.warn_minutes >= payload.minutes:
        raise HTTPException(400, 'Warning must be earlier than the time limit')
    async with lock:
        with db() as con:
            state = get_state(con)
            state['timer'] = {'started_at': time.time(), 'minutes': payload.minutes, 'warn_minutes': payload.warn_minutes}
            state['timer_warned'] = False
            record(con, 'timer_start', f"Manual {payload.minutes}-minute parking timer started; warns with {payload.warn_minutes} minutes left", None, 'manual')
            save_state(con, state)
            con.commit()
    return {'ok': True}


@app.post('/api/timer/stop')
async def timer_stop(x_watch_key: str | None = Header(default=None)):
    auth(x_watch_key)
    async with lock:
        with db() as con:
            state = get_state(con)
            state['timer'] = None
            record(con, 'timer_stop', 'Parking timer stopped manually', None, 'manual')
            save_state(con, state)
            con.commit()
    return {'ok': True}


@app.post('/api/sms/test')
async def sms_test(x_watch_key: str | None = Header(default=None)):
    auth(x_watch_key)
    message = 'Parking Watch test: SMS alerts are connected. Detection does not alter parking rules.'
    with db() as con:
        event_id = record_sms(con, 'SMS test', 'manual')
    return await submit_sms(event_id, message)


async def timer_monitor():
    """Background timer warning works only while this server stays awake."""
    while True:
        try:
            await asyncio.sleep(20)
            event_id = None
            async with lock:
                with db() as con:
                    state = get_state(con)
                    timer = state.get('timer')
                    if timer and not state.get('timer_warned'):
                        remaining = timer['started_at'] + timer['minutes']*60 - time.time()
                        if remaining <= timer['warn_minutes']*60:
                            state['timer_warned'] = True
                            save_state(con, state)
                            record(con, 'timer_warning', 'Parking time approaching or past its configured limit', None, 'system')
                            event_id = record_sms(con, 'Manual parking timer warning', 'system')
                            con.commit()
            if event_id is not None:
                await submit_sms(event_id, 'Parking Watch: your manually set parking timer is near or past its limit. Verify the sign and move your car if necessary.')
            await poll_sms_delivery()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Catch unexpected errors to keep subsequent reminders alive.
            print('Timer or SMS status update failed.', flush=True)


@app.on_event('startup')
async def on_startup():
    with db():
        pass
    app.state.timer_task = asyncio.create_task(timer_monitor())


@app.on_event('shutdown')
async def on_shutdown():
    task = getattr(app.state, 'timer_task', None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
