"""Step 2: ask Drex one calibrated yes/no (noul) question per failure and log the raw probability.

Request (verified against console.nace.ai/docs/drex/api-reference/systemone and
/docs/drex/guides/reading-answers):

    POST https://console.nace.ai/v1/systemone
    Authorization: Bearer $NACE_API_KEY
    X-Nace-Client: drex-flaky/0.1
    Content-Type: application/json
    {"model": "drex-max-v1.1",   # pinned; verified via GET /v1/models
     "state": {...structured NDI fields..., "log_excerpt": "..."},
     "questions": {"is_flaky": {"type": "noul", "instructions": QUESTION}}}

Response: answers.is_flaky.noul = P(yes) in [0,1]; plus usage.input_tokens (billed),
evaluation_time_ms, request_id. We threshold `noul`, never `confidence`.

    python drex_decide.py          # real if NACE_API_KEY set, else MOCK
    python drex_decide.py --mock   # deterministic MOCK probabilities (clearly labelled)
"""
from __future__ import annotations

import argparse
import hashlib
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from drexflaky.common import LOGS, QUESTION, load_env, load_labels, resolve_mode, result_path, read_json, write_json

BASE = "https://console.nace.ai/v1"
PREFERRED_MODEL = os.environ.get("DREX_MODEL", "drex-max-v1.1")
# $ per million INPUT tokens (output is not billed). drex-v2.0: nace.ai/drex.md launch price;
# Drex Max: console docs pricing page ($0.12/M Drex Max input tokens). Override with DREX_PRICE_PER_M_INPUT.
MODEL_PRICES = {"drex-v2.0": 0.03, "drex-max-v1.1": 0.12, "drex-max-finance": 0.12}
DREX_PRICE_PER_M_INPUT = float(os.environ.get("DREX_PRICE_PER_M_INPUT", MODEL_PRICES.get(PREFERRED_MODEL, 0.12)))
CLIENT_ID = "drex-flaky/0.1"
QUESTION_DETAIL = (QUESTION + " Answer yes if the evidence points to the test or CI environment (timeouts on shared infra, "
                   "races in test code, network blips, test-order coupling, resource exhaustion) and no if the evidence "
                   "points to a change in this PR breaking behaviour.")


def build_state(fields: dict, log_text: str) -> dict:
    return {
        "context": "A CI test failed on a pull request. Decide whether it is flaky.",
        "structured_fields_from_ndi": fields,
        "log_excerpt": log_text[-3500:],
    }


def build_request(model: str, state: dict) -> dict:
    return {"model": model, "state": state,
            "questions": {"is_flaky": {"type": "noul", "instructions": QUESTION_DETAIL}}}


def pick_model(session) -> tuple[str, list]:
    r = session.get(f"{BASE}/models", timeout=20)
    r.raise_for_status()
    body = r.json()
    items = body.get("data", body.get("models", body)) if isinstance(body, dict) else body
    names = [m.get("name") or m.get("id") for m in items if isinstance(m, dict)]
    if PREFERRED_MODEL in names:
        return PREFERRED_MODEL, names
    raise SystemExit(f"pinned model {PREFERRED_MODEL!r} not available to this key; available: {names}. Set DREX_MODEL.")


def mock_probability(case_id: str, f: dict, log_text: str) -> float:
    """Deterministic, feature-based stand-in. NOT Drex output."""
    z = -0.4
    z += 2.2 if f.get("retry_passed") else -1.6
    z += 9.0 * (f.get("recent_fail_rate") or 0)
    et = (f.get("error_type") or "")
    if any(k in et for k in ("Timeout", "ConnectionError", "gaierror", "MaxRetry", "OSError", "TestingLibrary")):
        z += 1.0
    if any(k in et for k in ("ImportError", "TypeError", "Snapshot")):
        z -= 2.0
    test_mod = (f.get("test_name") or "").split("::")[0].split("/")[-1].replace("test_", "").split(".")[0].split("_")[0]
    if test_mod and any(test_mod.lower() in c.lower() for c in f.get("changed_files") or []):
        z -= 0.7
    h = int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    z += (h - 0.5) * 1.6
    return round(1 / (1 + math.exp(-z)), 4)


def call_drex(session, model, state):
    body = build_request(model, state)
    t0 = time.perf_counter()
    last = None
    for attempt in range(3):
        r = session.post(f"{BASE}/systemone", json=body, timeout=70)
        if r.status_code in (429, 500, 502, 503, 529) and attempt < 2:
            last = r; time.sleep(2 * (attempt + 1)); continue
        break
    rtt = (time.perf_counter() - t0) * 1000
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    j = r.json()
    ans = j["answers"]["is_flaky"]
    return {"p_flaky": ans["noul"], "confidence": ans.get("confidence"), "model": j.get("model"),
            "usage": j.get("usage"), "evaluation_time_ms": j.get("evaluation_time_ms"),
            "request_id": j.get("request_id"), "latency_ms": round(rtt, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    load_env()
    mode = resolve_mode(a.mock, "NACE_API_KEY")
    ndi_path = result_path("ndi", mode)
    if not ndi_path.exists():
        raise SystemExit(f"run ndi_extract.py first ({ndi_path} missing)")
    ndi = {c["case_id"]: c for c in read_json(ndi_path)["cases"]}
    cases = load_labels()[: a.limit]
    meta = {"mode": mode, "question": QUESTION_DETAIL}

    if mode == "real":
        s = requests.Session()
        s.headers.update({"Authorization": f"Bearer {os.environ['NACE_API_KEY'].strip()}", "Content-Type": "application/json", "X-Nace-Client": CLIENT_ID})
        model, available = pick_model(s)
        meta.update(model_requested=model, models_available=available)
        print(f"[drex] GET /v1/models -> {available}; pinned {model}")

        def run(c):
            f = ndi[c["case_id"]]["fields"]
            text = (LOGS / f"{c['case_id']}.log").read_text()
            try:
                out = call_drex(s, model, build_state(f, text))
            except Exception as e:  # noqa: BLE001
                out = {"error": f"{type(e).__name__}: {str(e)[:300]}", "p_flaky": None}
            return {"case_id": c["case_id"], **out}
        with ThreadPoolExecutor(a.workers) as ex:
            recs = list(ex.map(run, cases))
        tok = sum((r.get("usage") or {}).get("input_tokens", 0) for r in recs)
        ok = [r for r in recs if r.get("p_flaky") is not None]
        meta.update(total_input_tokens=tok, price_per_m_input=DREX_PRICE_PER_M_INPUT,
                    usd_total=tok / 1e6 * DREX_PRICE_PER_M_INPUT,
                    usd_per_1k=(tok / max(len(ok), 1)) / 1e6 * DREX_PRICE_PER_M_INPUT * 1000,
                    errors=len(recs) - len(ok))
    else:
        model = PREFERRED_MODEL + " (MOCK)"
        meta.update(model_requested=model, note="MOCK probabilities from a local heuristic; not Drex output")
        recs = []
        for c in cases:
            f = ndi[c["case_id"]]["fields"]
            text = (LOGS / f"{c['case_id']}.log").read_text()
            req_chars = len(str(build_request(PREFERRED_MODEL, build_state(f, text))))
            h = int(hashlib.sha256(c["case_id"].encode()).hexdigest()[8:12], 16) / 0xFFFF
            recs.append({"case_id": c["case_id"], "p_flaky": mock_probability(c["case_id"], f, text),
                         "model": model, "usage": {"input_tokens": req_chars // 4},
                         "latency_ms": round(180 + 120 * h, 1), "mock": True})
        tok = sum(r["usage"]["input_tokens"] for r in recs)
        meta.update(total_input_tokens=tok, price_per_m_input=DREX_PRICE_PER_M_INPUT,
                    usd_per_1k=(tok / len(recs)) / 1e6 * DREX_PRICE_PER_M_INPUT * 1000, errors=0)
    write_json(result_path("drex", mode), {**meta, "cases": recs})
    print(f"[drex] mode={mode} model={meta['model_requested']} n={len(recs)} errors={meta['errors']} -> {result_path('drex', mode)}")
    for r in recs:
        if r.get("error"):
            print(f"  error {r['case_id']}: {r['error']}")


if __name__ == "__main__":
    main()
