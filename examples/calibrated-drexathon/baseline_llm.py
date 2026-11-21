"""Step 5: the frontier-LLM baseline on DeepInfra (OpenAI-compatible API).

Same logs, same cost table, the LLM is asked directly for a label + action.

    python baseline_llm.py          # real if DEEPINFRA_API_KEY set, else MOCK
    python baseline_llm.py --mock
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from drexflaky.common import LOGS, load_env, load_labels, resolve_mode, result_path, write_json

BASE_URL = "https://api.deepinfra.com/v1/openai"
MODEL = os.environ.get("LLM_MODEL", "meta-llama/Llama-3.3-70B-Instruct-Turbo")
# $/M tokens from GET https://api.deepinfra.com/v1/openai/models metadata.pricing (checked 2026-11-14)
PRICES = {"meta-llama/Llama-3.3-70B-Instruct-Turbo": (0.10, 0.32), "deepseek-ai/DeepSeek-V3.2": (0.26, 0.38)}  # else read live from /models

SYSTEM = """You triage failing CI tests. Decide if a failure is FLAKY (caused by the test or CI environment:
timeouts on shared infra, races in test code, network blips, test-order coupling, resource exhaustion) or REAL
(a code change in this PR broke behaviour). Then choose an action:
QUARANTINE (skip the test and file a ticket), RETRY (rerun once), BLOCK (block the merge and alert a human).
Costs in engineer-minutes: quarantining a real bug=120, retrying a real bug=6, blocking a real bug=0,
quarantining a flaky test=2, retrying a flaky test=4, blocking a flaky test=25.
Reply with JSON only: {"label": "flaky"|"real", "action": "QUARANTINE"|"RETRY"|"BLOCK", "reason": "<one sentence>"}"""


def mock_answer(case_id, text):
    """Deterministic stand-in. NOT LLM output."""
    h = int(hashlib.sha256(("llm" + case_id).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    retry_pass = "PASSED" in text.split("retry history:")[-1]
    looks_infra = bool(re.search(r"Timeout|Connection|gaierror|Address already|No space|act\(", text))
    flaky = (retry_pass or looks_infra) if h > 0.12 else not (retry_pass or looks_infra)
    action = "QUARANTINE" if flaky and h > 0.3 else ("RETRY" if flaky else "BLOCK")
    return {"label": "flaky" if flaky else "real", "action": action, "reason": "MOCK"}


def parse(content: str) -> dict:
    m = re.search(r"\{.*\}", content, re.S)
    d = json.loads(m.group(0)) if m else {}
    lab = str(d.get("label", "")).lower()
    act = str(d.get("action", "")).upper()
    return {"label": lab if lab in ("flaky", "real") else None,
            "action": act if act in ("QUARANTINE", "RETRY", "BLOCK") else "RETRY", "reason": d.get("reason")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--model", default=None, help="DeepInfra model id (default: $LLM_MODEL or Llama-3.3-70B-Instruct-Turbo)")
    ap.add_argument("--tag", default="llm", help="results file stem, e.g. llm2 for a second baseline")
    a = ap.parse_args()
    global MODEL
    MODEL = a.model or MODEL
    load_env()
    mode = resolve_mode(a.mock, "DEEPINFRA_API_KEY")
    cases = load_labels()[: a.limit]
    pin, pout = PRICES.get(MODEL, (None, None))
    if pin is None:
        import requests
        meta_ = {m["id"]: (m.get("metadata") or {}).get("pricing") or {} for m in requests.get(BASE_URL + "/models", timeout=20).json()["data"]}
        pr = meta_.get(MODEL, {})
        pin, pout = pr.get("input_tokens", 0.0), pr.get("output_tokens", 0.0)

    if mode == "real":
        from openai import OpenAI
        client = OpenAI(api_key=os.environ["DEEPINFRA_API_KEY"].strip(), base_url=BASE_URL)

        def run(c):
            text = (LOGS / f"{c['case_id']}.log").read_text()
            t0 = time.perf_counter()
            try:
                r = client.chat.completions.create(model=MODEL, temperature=0, max_tokens=400,
                                                   messages=[{"role": "system", "content": SYSTEM},
                                                             {"role": "user", "content": text[-3500:]}])
                ms = (time.perf_counter() - t0) * 1000
                u = r.usage
                return {"case_id": c["case_id"], **parse(r.choices[0].message.content or ""), "latency_ms": round(ms, 1),
                        "usage": {"input_tokens": u.prompt_tokens, "output_tokens": u.completion_tokens}}
            except Exception as e:  # noqa: BLE001
                return {"case_id": c["case_id"], "label": None, "action": "RETRY", "error": f"{type(e).__name__}: {str(e)[:200]}"}
        with ThreadPoolExecutor(a.workers) as ex:
            recs = list(ex.map(run, cases))
    else:
        recs = []
        for c in cases:
            text = (LOGS / f"{c['case_id']}.log").read_text()
            h = int(hashlib.sha256(("lat" + c["case_id"]).encode()).hexdigest()[:4], 16) / 0xFFFF
            recs.append({"case_id": c["case_id"], **mock_answer(c["case_id"], text), "mock": True,
                         "latency_ms": round(900 + 1400 * h, 1),
                         "usage": {"input_tokens": (len(SYSTEM) + len(text[-3500:])) // 4, "output_tokens": 45}})
    ok = [r for r in recs if "usage" in r]
    tin = sum(r["usage"]["input_tokens"] for r in ok); tout = sum(r["usage"]["output_tokens"] for r in ok)
    usd = tin / 1e6 * pin + tout / 1e6 * pout
    meta = {"mode": mode, "model": MODEL + (" (MOCK)" if mode == "mock" else ""), "price_per_m": {"input": pin, "output": pout},
            "usd_total": usd, "usd_per_1k": usd / max(len(ok), 1) * 1000, "errors": len(recs) - len(ok)}
    write_json(result_path(a.tag, mode), {**meta, "cases": recs})
    print(f"[llm] mode={mode} model={meta['model']} n={len(recs)} errors={meta['errors']} -> {result_path(a.tag, mode)}")


if __name__ == "__main__":
    main()
