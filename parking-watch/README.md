# Parking Watch SF | Drexathon desktop MVP (v0.3)

This is a **modification of the original Parking Watch SF v0.1 code**, not a new unrelated project. It retains the existing FastAPI server, dashboard, SQLite history, manual parking timer, and Twilio integration, and adds Nace AI Drex Max computer vision and NDI document extraction.

## Hackathon review

- [Drex decision model](server.py#L290-L339) sends a camera image and labeled references to Nace's `POST /v1/systemone`. The default model is `drex-max-v1.1`, and the app compares its returned probability with a detection threshold.
- [NDI document parsing](server.py#L722-L728) and [NDI extraction](server.py#L792-L805) process SFMTA's parking guide through Nace's document APIs.
- [Integration tests](tests/test_parking_watch.py) verify the request and response handling with mocked API calls. From a fresh checkout, run `python -m pip install -r requirements.txt pytest`, then `python -m pytest -q`. Live Drex and NDI calls require a Nace API key.
- [Intern fork](https://github.com/benfeleke/intern) is the reviewer tool fork from [Nace's upstream repository](https://github.com/nace-ai/intern). Parking Watch's application code is in this repository.

### Verify the Nace integration without credentials

Run these commands from the repository root in a Python virtual environment.

```bash
python -m pip install -r requirements.txt pytest
python -m pytest -q tests/test_parking_watch.py -k 'drex_image_decision or ndi_extract_request or ndi_parse_uses_console_route'
```

The three selected tests check the Drex image decision request and response, the NDI extraction request and citations, and the NDI parsing route and job status. The tests use mocked responses and make no external API calls.

## Fastest setup on a Mac

1. Clone or download this repository. In Terminal, run `cd parking-watch && ./START_HERE.command`. You may also right-click the `.command` file in Finder and choose Open.
2. It installs the Python dependencies and launches `http://localhost:8000` in your browser. If it does not open automatically, paste that URL into Safari or Chrome.
3. The **computer webcam preview works without any cloud keys**. Click **Start camera + analysis** and grant camera access.
4. Add your `NACE_API_KEY` to the local `.env` created by the launcher, keep `VISION_PROVIDER=drex`, and restart the app.
5. Click **Check Nace connection** to confirm the demo API key can access Drex Max. Then hold a photo of an SFMTA 3-wheeled parking-enforcement vehicle on your phone in front of the webcam for ~4–6 seconds; when it confirms presence, remove the phone and wait for two subsequent clear frames.
6. For **real SMS**, set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `ALERT_TO` (e.g. `+14155550123`) in `.env`. For a free trial, add `TWILIO_MODE=trial`, verify the recipient in Twilio Console, and use `TWILIO_TRIAL_TEMPLATE=sms_internal_alerts` (default) or `sms_account_alerts`. For custom SMS, use `TWILIO_MODE=standard` (default) and set `TWILIO_FROM` to your SMS sender. Restart and click **Test SMS**. Check notification history for the Message SID and delivery receipt.
7. For **NDI**, click **Parse SFMTA PDF** (the exact document processing API shown in your screenshot), then **Run NDI extraction**. The app sends a published SFMTA parking-guide PDF to Nace's document Extract endpoint and shows the returned fields, citations if provided, and pending job status.

The camera preview and simulation work without cloud credentials. Live recognition requires Nace credentials, and SMS delivery requires Twilio credentials. Use `.env.example` as the configuration template.

### Manual alternative

```bash
cd parking-watch
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Add NACE_API_KEY for live vision; optionally add Twilio credentials for real SMS.
uvicorn server:app --host 127.0.0.1 --port 8000
```

## What the demo proves

- **Webcam**: Standard browser `getUserMedia` on `http://localhost:8000`; choose a connected camera in the dropdown.
- **Drex Max v1.1**: A live JPEG frame is sent to Nace's `POST /v1/systemone` with the image at top-level `images`, a `noul` question and configurable `CONFIDENCE_THRESHOLD`. The `answers.enforcement_vehicle.noul` probability is the model's output, **not a guarantee of accuracy**. Two positive frames are required to confirm presence.
- **Departure**: Two consecutive negative frames after a confirmed sighting record a departure. The event is only disappearance from the camera view, **not** proof of plate scanning or the vehicle's actual departure from the area.
- **Text**: After reference evaluation passes and automatic alerts are enabled, a confirmed departure can send one SMS, subject to `DEPARTURE_COOLDOWN_SECONDS`. With `DEMO_MODE=true` and `THREE_WHEEL_DEMO_ALERTS=true`, the first plausible three-wheeler sighting can send an SMS. Standard mode sends the personalized Pacific-time message, and trial mode sends the selected Twilio template. The observation countdown and manual parking timer display separate deadlines. Simulated departures produce a dashboard preview.
- **NDI (Nace Perception)**: `POST /v1/documents/extract?wait_seconds=25` with a fixed official SFMTA PDF URL and a JSON schema. Completed `result.data` and `result.fields` (including source citations) appear, with polling if the job is still running. This historic brochure is educational, not a live parking-rule database.
- **Optional comparison mode**: `VISION_PROVIDER=openai` with `OPENAI_API_KEY` switches model back to the original OpenAI vision route for benchmarking.

The project does not retain frames, video, faces or license plates; it records only event types, timestamps, confidence and brief source text. Use a source you are permitted to record. For a real product, use on-device object detection, model evaluation against labeled clips, camera obstruction checks, adaptive sampling and reliable persistent delivery status.

## Manual checks

1. Start server. Open `/health` and see `{"ok":true,"app":"Parking Watch"}`.
2. Click **Start camera + analysis**; verify live webcam video and camera selection.
3. If no Nace key, use **Demo: vehicle seen**, then **Demo: vehicle gone**. The second button sends two negative simulated frames and records a departure.
4. With Drex key: hold photo visible until dashboard says **Vehicle confirmed**; remove it and check **Activity log** for **Departure**. With Twilio configured, verify SMS on the receiving phone.
5. Press **Run NDI extraction** and, if necessary, **Refresh NDI job**. Confirm source-linked fields are displayed.

For automated backend checks, run `python -m pytest tests -q` (requires `pip install pytest`). The tests mock external AI/SMS calls. Browser preview is tested in Chromium with a fake webcam; real laptop hardware, live Nace access and actual Twilio delivery require local verification.

## API

- `GET /health` server liveness.
- `GET /api/overview` state, recent events, provider/config capabilities.
- `POST /api/frame` with `{"image":"data:image/jpeg;base64,..."}` classifies live frame via Drex or OpenAI.
- `POST /api/simulate` with `{"detected":true/false}` available only with `DEMO_MODE=true`.
- `POST /api/sms/test` sends a test message if Twilio is configured.
- `POST /api/ndi/rules` invokes NDI extraction on the SFMTA PDF.
- `GET /api/ndi/rules/{job_id}` polls NDI job status and results.
- `POST /api/timer/start` and `/api/timer/stop` manage the legal-parking-time **manual** countdown.

`/api/*` endpoints require `X-Watch-Key` when `WATCH_KEY` is set. Set a long random `WATCH_KEY` and `REQUIRE_AUTH=true`, disable `DEMO_MODE`, and serve over HTTPS before any public hosting. Bind only to 127.0.0.1 for this computer demo; never expose the default no-auth demo server to the internet. Keep API tokens in `.env`, never in frontend code or GitHub. API usage and SMS can cost money. The UI does not automatically start a legal parking clock upon SFMTA sightings because SFMTA's rules do not work that way.

## References

- Drex Max images: https://console.nace.ai/docs/drex/guides/drex-max
- Drex noul response: https://console.nace.ai/docs/drex/guides/reading-answers
- Nace NDI (Perception) Extract: https://console.nace.ai/docs/perception/extract
- NDI jobs: https://console.nace.ai/docs/perception/jobs
- SFMTA parking guidance: https://www.sfmta.com/getting-around/drive-park/how-avoid-parking-tickets
- Historical SFMTA brochure used for NDI: https://www.sfmta.com/media/1379/download?inline=

## Screenshot API examples and credential security

The Nace API key authenticates requests to the following services.

- **Decision model**: `POST https://console.nace.ai/v1/systemone` (Drex Max image recognition).
- **Document processing**: `POST https://console.nace.ai/v1/documents/parse` (NDI Parse). **Extract** at `/v1/documents/extract` is also supported and returns structured parking-rule fields.
- **Search**: `POST https://console.nace.ai/v1/workspaces/{workspace_id}/automatic-search` (optional; searches documents already ingested and Ready in that workspace). Set `NACE_WORKSPACE_ID` locally to enable its dashboard button. Search does **not** crawl the public web.

Store your Nace API key in the local `.env` under `NACE_API_KEY` and restart `START_HERE.command`. Git ignores the local `.env`. The check endpoint queries the model catalog without returning the key.

`GET /api/nace/check` is an authenticated API-key/catalog check; it is not a live paid inference. The Drex camera flow and NDI document flow still require real testing with your fresh credential, an available Nace account balance, and a network connection.
