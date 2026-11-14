"""Drex decision engine — powered by the real Nace Drex API.

With NACE_API_KEY set, ResolveAI asks the Nace Drex API (model
drex-max-v1.1) four noul questions per case over HTTP:

    POST https://console.nace.ai/v1/systemone
    Authorization: Bearer $NACE_API_KEY
    Content-Type: application/json
    X-Nace-Client: resolveai-drexathon/1

    {
      "model": "drex-max-v1.1",
      "state": "<case description>",
      "questions": {
        "<name>": {"type": "noul", "instructions": "..."}
      }
    }

``questions`` is a *dictionary* keyed by question name (never a list), and
every question is a noul question. A single structured decision is derived
from the returned noul probabilities in ``response.json()["answers"]``.

Without an API key — or if the request fails — a deterministic
rule-based fallback applies the matched policy rule directly, so the
demo always works offline. The fallback reason reports the actual
HTTP status and response body when the API returns an error.
"""
from __future__ import annotations

import json
import os

import httpx

ALLOWED_ACTIONS = {"refund", "replacement", "store_credit", "human_review"}

DEFAULT_NACE_ENDPOINT = "https://console.nace.ai/v1/systemone"
DEFAULT_DREX_MODEL = "drex-max-v1.1"
NACE_CLIENT_ID = "resolveai-drexathon/1"

# Model name for the UI (resolved from the environment at import time;
# the live call re-reads NACE_MODEL at request time).
DREX_MODEL = os.environ.get("NACE_MODEL") or DEFAULT_DREX_MODEL


class DrexAPIError(RuntimeError):
    """The Nace Drex API returned an error response or invalid body."""


# ---------------------------------------------------------------- questions
# The four noul questions sent to the Nace Drex API for every case, as a
# dictionary of question name -> instructions. The payload builder turns
# this into {"<name>": {"type": "noul", "instructions": ...}}.
NOUL_QUESTIONS: dict[str, str] = {
    "can_refund": (
        "Can this customer case receive a refund under the supplied "
        "customer record, order record, and policy?"
    ),
    "can_replace": (
        "Can this customer case receive a replacement under the supplied "
        "customer record, order record, and policy?"
    ),
    "can_credit": (
        "Can this customer case receive store credit under the supplied "
        "customer record, order record, and policy?"
    ),
    "needs_human": (
        "Does this case require human review because of risk, missing "
        "evidence, or ambiguity?"
    ),
}

# Map question name -> the action it represents.
QUESTION_TO_ACTION: dict[str, str] = {
    "can_refund": "refund",
    "can_replace": "replacement",
    "can_credit": "store_credit",
    "needs_human": "human_review",
}

# ----------------------------------------------------------------- fallback
def _fallback_decision(case: dict) -> dict:
    """Deterministic decision from the already-matched policy rule."""
    rule = (case.get("context") or {}).get("matched_rule") or (
        (case.get("policy") or {}).get("rules") or [{}]
    )[-1]
    conditions = rule.get("conditions", {})
    confidence = 0.55 if not conditions else 0.7 + 0.05 * len(conditions)
    return {
        "action": rule.get("action", "human_review"),
        "confidence": round(min(confidence, 0.95), 2),
        "reasons": [
            f"Policy rule {rule.get('id', '?')} ({rule.get('name', '?')}): "
            f"{rule.get('note', '')}",
            "Decided by the offline rule-based fallback.",
        ],
        "evidence": case.get("evidence", []),
        "probabilities": {},
        "model": "fallback-rules",
        "answer": None,
        "usage": None,
        "request_id": None,
        "api_error": None,
    }

# ------------------------------------------------------------------ payload
def _build_state(case: dict) -> str:
    """Build the ``state`` string: the full case context as text.

    Defensive: uses .get with defaults so a missing section (e.g.
    "records") can never raise a KeyError.
    """
    records = case.get("records") or {}
    customer = records.get("customer") if isinstance(records, dict) else None
    order = records.get("order") if isinstance(records, dict) else None
    matched_rule = (case.get("context") or {}).get("matched_rule")

    lines = [
        f"Customer request: {case.get('request', '')}",
        f"Triage category: {case.get('triage', '')}",
        f"Customer record: {json.dumps(customer, default=str)}",
        f"Order record: {json.dumps(order, default=str)}",
        f"Policy: {json.dumps(case.get('policy') or {}, default=str)}",
    ]
    if matched_rule:
        lines.append(f"Matched policy rule: {json.dumps(matched_rule, default=str)}")
    evidence = case.get("evidence") or []
    if evidence:
        lines.append("Evidence: " + " | ".join(str(e) for e in evidence))
    return "\n".join(lines)


def _build_payload(case: dict) -> dict:
    """Build the Nace Drex API payload in the verified format:
    model + state (string) + questions (dictionary of noul questions).
    No SDK structured-output arguments (no choice/score/predicate/cast_to).
    """
    model = os.environ.get("NACE_MODEL") or DREX_MODEL
    return {
        "model": model,
        "state": _build_state(case),
        "questions": {
            name: {"type": "noul", "instructions": instructions}
            for name, instructions in NOUL_QUESTIONS.items()
        },
    }

# ------------------------------------------------------------------- parse
def _coerce_probability(value) -> float | None:
    """Extract a 0..1 probability from a raw answer value.

    Accepts a bare number or a dict with a probability-like field
    (e.g. {"probability": 0.85}, {"noul": 0.85}).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    if isinstance(value, dict):
        for key in ("probability", "yes_probability", "noul", "score", "confidence"):
            if key in value:
                inner = _coerce_probability(value[key])
                if inner is not None:
                    return inner
    return None


def _extract_noul_probabilities(body: dict) -> dict[str, float]:
    """Extract per-question noul probabilities from ``body["answers"]``.

    Handles the response shapes:
      - {"answers": {"can_refund": 0.85, ...}}
      - {"answers": {"can_refund": {"probability": 0.85}, ...}}
      - {"answers": [{"id": "can_refund", "probability": 0.85}, ...]}
    with flat top-level question names as a last resort.
    """
    probs: dict[str, float] = {}
    answers = body.get("answers")

    if isinstance(answers, dict):
        for qid, val in answers.items():
            prob = _coerce_probability(val)
            if prob is not None:
                probs[str(qid)] = prob
        return probs

    if isinstance(answers, list):
        for item in answers:
            if not isinstance(item, dict):
                continue
            qid = (
                item.get("id")
                or item.get("question_id")
                or item.get("question")
            )
            prob = _coerce_probability(item)
            if qid is not None and prob is not None:
                probs[str(qid)] = prob
        return probs

    for qid in NOUL_QUESTIONS:
        prob = _coerce_probability(body.get(qid))
        if prob is not None:
            probs[qid] = prob

    return probs


def _derive_decision(probs: dict[str, float], case: dict) -> dict:
    """Derive exactly one final decision from the noul probabilities.

    Rule: if needs_human is the highest probability -> human_review.
    Otherwise, the highest of can_refund / can_replace / can_credit wins.
    """
    if not probs:
        return {
            "action": "human_review",
            "confidence": 0.3,
            "reasons": [
                "No noul probabilities were returned by the API; "
                "escalating to human review.",
            ],
            "evidence": case.get("evidence", []),
            "probabilities": {},
            "model": DREX_MODEL,
            "answer": None,
            "usage": None,
            "request_id": None,
            "api_error": None,
        }

    human_prob = probs.get("needs_human", 0.0)
    options: dict[str, float] = {
        "can_refund": probs.get("can_refund", 0.0),
        "can_replace": probs.get("can_replace", 0.0),
        "can_credit": probs.get("can_credit", 0.0),
    }
    best_qid = max(options, key=options.get)
    best_prob = options[best_qid]

    ranked = sorted(probs.items(), key=lambda x: x[1], reverse=True)
    ranked_str = ", ".join(f"{k}={v:.2f}" for k, v in ranked)

    if human_prob > best_prob:
        action = "human_review"
        confidence = human_prob
        reasons = [
            f"needs_human ({human_prob:.2f}) is the highest noul probability; "
            "case escalated to human review.",
            f"All noul probabilities: {ranked_str}",
        ]
    else:
        action = QUESTION_TO_ACTION[best_qid]
        confidence = best_prob
        reasons = [
            f"Drex selected {action} ({best_prob:.2f}) — highest of "
            f"refund/replacement/store_credit; needs_human was {human_prob:.2f}.",
            f"All noul probabilities: {ranked_str}",
        ]

    # Convert question names to action names for display.
    display_probs = {
        QUESTION_TO_ACTION[k]: v for k, v in probs.items() if k in QUESTION_TO_ACTION
    }

    return {
        "action": action,
        "confidence": round(confidence, 2),
        "reasons": reasons,
        "evidence": case.get("evidence", []),
        "probabilities": display_probs,
        "model": DREX_MODEL,
        "answer": None,
        "usage": None,
        "request_id": None,
        "api_error": None,
    }


def _parse_nace_response(body: dict, case: dict) -> dict:
    """Parse a Nace Drex API response into the structured decision shape."""
    if not isinstance(body, dict):
        body = {}

    decision = _derive_decision(_extract_noul_probabilities(body), case)

    # Attach API metadata when present.
    usage = body.get("usage")
    if isinstance(usage, dict):
        decision["usage"] = usage

    request_id = body.get("request_id") or body.get("id")
    if request_id is not None:
        decision["request_id"] = str(request_id)

    answer = body.get("answer")
    if isinstance(answer, str):
        decision["answer"] = answer

    return decision

# --------------------------------------------------------------------- call
def _call_nace_api(case: dict) -> dict:
    """POST the four noul questions to the Nace Drex API via httpx.post
    and return a decision. Raises DrexAPIError with the actual HTTP
    status and response body on any error response."""
    api_key = os.environ.get("NACE_API_KEY", "")
    if not api_key:
        raise DrexAPIError("NACE_API_KEY is not set in the environment")

    endpoint = os.environ.get("NACE_ENDPOINT") or DEFAULT_NACE_ENDPOINT
    payload = _build_payload(case)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Nace-Client": NACE_CLIENT_ID,
    }

    resp = httpx.post(endpoint, headers=headers, json=payload, timeout=30)

    if resp.status_code >= 400:
        raise DrexAPIError(
            f"Nace Drex API returned HTTP {resp.status_code}: {resp.text}"
        )
    try:
        body = resp.json()
    except ValueError:
        raise DrexAPIError(
            f"Nace Drex API returned HTTP {resp.status_code} with a "
            f"non-JSON response body: {resp.text}"
        )
    return _parse_nace_response(body, case)

# ------------------------------------------------------------------- entry
def ask_drex(case: dict) -> dict:
    """Return a structured decision: action, confidence, reasons, evidence.

    - If NACE_API_KEY is absent, the offline rule fallback is used and the
      reason is stated explicitly.
    - If the API request itself fails (HTTP error, network error, …), the
      actual HTTP status and response body are shown in ``reasons`` and in
      the ``api_error`` field, and the rule fallback is used. The key is
      never reported as missing when it exists.
    """
    api_key = os.environ.get("NACE_API_KEY")

    if not api_key:
        fallback = _fallback_decision(case)
        fallback["reasons"].insert(
            0,
            "NACE_API_KEY is not set — using the offline rule-based fallback.",
        )
        fallback["api_error"] = None
        return fallback

    try:
        return _call_nace_api(case)
    except Exception as exc:  # noqa: BLE001
        # Show the *actual* error (HTTP status + body for API errors);
        # do NOT re-label it as a missing key.
        fallback = _fallback_decision(case)
        error_msg = f"{type(exc).__name__}: {exc}"
        fallback["reasons"].insert(
            0,
            f"Nace Drex API request failed — {error_msg}. "
            "Offline rule-based fallback used.",
        )
        fallback["api_error"] = error_msg
        return fallback
