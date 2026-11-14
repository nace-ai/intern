# ResolveAI — Drexathon

Autonomous customer-resolution demo. Specialized logic triages a customer
request, looks up **simulated** customer/order records, applies a
customer-service policy, and then **Drex makes exactly one structured
decision**: `refund`, `replacement`, `store_credit`, or `human_review`.

All side effects (payments, shipping, tickets) are **simulated and printed
only** — no real integrations.

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI and pipeline: triage → records → policy → Drex → display |
| `drex.py` | Drex decision engine: OpenAI-compatible API call + deterministic offline fallback |
| `data.py` | Simulated customers, orders, the service policy, and 4 benchmark cases |
| `requirements.txt` | `streamlit`, `python-dotenv`, `pandas` |
| `.env.example` | Template for API keys (copy to `.env`) |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add DREX_API_KEY to use the Drex API
streamlit run app.py
```

No API key? The app still works: Drex falls back to evaluating the policy
deterministically, and the UI says so.

## Configuration (environment variables)

| Variable | Meaning |
|---|---|
| `DREX_API_KEY` | API key for the Drex endpoint. Empty → offline fallback. |
| `DREX_BASE_URL` | OpenAI-compatible base URL (default `https://api.openai.com/v1`) |
| `DREX_MODEL` | Model name (default `gpt-4o-mini`) |
| `DREX_TEMPERATURE` | Sampling temperature (default `0` for stable decisions) |

## How a decision is made

1. **Triage** — keyword categories: `damaged`, `delayed`, `refund`, `general`.
2. **Records** — simulated customer and order lookups (tier, LTV, disputes,
   total, delivery status, days since delivery).
3. **Policy** — first matching rule wins (`data.POLICY`). High-value orders
   and repeat complainants go to `human_review`; damaged-in-window gets a
   replacement; paid in-window gets a refund; in-transit delays get store
   credit; out-of-window gets 50% store credit; otherwise `human_review`.
4. **Drex** — receives request + records + policy + evidence and must return
   one JSON object: `action`, `confidence`, `reasons`, `evidence`. Invalid
   actions are forced to `human_review`.
5. **Display** — decision, confidence, reasons, evidence, and the simulated
   action taken.

## Benchmark mode

Sidebar → **Benchmark** → *Run all benchmark cases*. Four predefined cases
run through the full pipeline and Drex's action is compared with the expected
policy outcome, shown in a pass/fail table.
