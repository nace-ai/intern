# Cosmic Customs — five slides

## Slide 1. Cosmic Customs

- An orbital customs desk for fictional cargo, built for the Nace Drexathon.
- Inspectors read a manifest, a containment certificate, and a watch note.
- The desk compares Nace Drex with a frontier model on the same evidence.
- A deterministic rule oracle labels every case. The models do not grade themselves.
- The question is whether a fast decision model can apply a written rulebook
- without clearing a shipment that should be held or reviewed.

## Slide 2. Rulebook and generated evidence

- Damaged seals and pressure below 90 kPa are HOLD, even when other facts conflict.
- Missing required facts and contradictions are REVIEW. A weight gap above 3% is REVIEW.
- Singing cargo needs a valid certificate, or a valid waiver that is not class-4 acoustic.
- Exactly 90 kPa and exactly 3% pass. A waiver cannot override a physical hazard.
- 20 development cases expose every primitive rule. 100 held-out cases are 33 CLEAR,
- 33 HOLD, and 34 REVIEW. Selected interactions and wording families stay out of development.
- Documents are newly generated synthetic text. This is not a claim of zero contamination.
- Model input is the rulebook and the evidence only. Expected labels stay off the page.

## Slide 3. Architecture and Nace integrations

- FastAPI serves a static terminal. Python 3.12 holds the oracle, clients, and storage.
- Drex uses POST /v1/systemone with one CLEAR/HOLD/REVIEW choice question.
- OpenRouter uses chat completions and a one-field JSON verdict schema.
- Both providers see the same shared text. Settings freeze after the development pilot.
- NDI flow: upload grant, parse, extract with schemas/ndi_fields.json, ground quotes,
- then Drex. Extraction failure does not get replaced with generator facts.
- Intern 0.30.1 wrote tests/test_intern_integrity.py with Drex as System 1.
- A privileged fact baseline and a naive regex baseline sit beside the models.

## Slide 4. Measured results

- Development pilot 2026-11-14T201845+0000-70aac090, n=5 development cases:
- Drex 5/5 correct, false clearance 0/3, failures 0 of 5. P50/P95 336 ms / 491 ms, known cost $0.00012312.
- OpenRouter 4/5 correct, false clearance 1/3, failures 0 of 5. P50/P95 1107 ms / 1437 ms, known cost $0.0096500.
- One OpenRouter miss in the pilot: DEV-12 gold REVIEW, predicted CLEAR.
- Held-out test 2026-11-14T215926+0000-36032342, n=100 held-out cases, identical shared evidence and rules:
- Drex 89/100 correct, false clearance 8/67, failures 0 of 100. P50/P95 285 ms / 447 ms, known cost $0.00249756.
- OpenRouter 95/100 correct, false clearance 3/67, failures 0 of 100. P50/P95 1083 ms / 1361 ms, known cost $0.1967275.
- Median OpenRouter/Drex latency 3.78 on 100 matched pairs. Above 1 means Drex returned first.
- One NDI packet TST-056 in 2026-11-14T201933+0000-3b155911. Document cost $0.1200. OCR True.
- Fields 7/8. Grounding coverage 1.0.
- Drex on that extraction matched the oracle.
- Mock runs are simulated and stay separate. No settings changed after the development freeze.

## Slide 5. Demo and next steps

- The local dashboard is http://127.0.0.1:8000/. It replays saved results and spends nothing on load.
- The Inspect button is labeled MOCK or LIVE from server health. Default is mock.
- With APP_MODE=live and server-side keys, Inspect calls both providers once per click.
- Text view replays the held-out test. Document view shows the one live NDI packet.
- Artwork on the packets is geometric fallback art, not an Adobe Firefly render.
- GitHub: github.com/bashaboinamanojkumar/cosmic-customs. Dashboard: cosmic-customs.vercel.app, mock mode.
