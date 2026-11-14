"""Drex adapter: real hosted client (POST /v1/systemone) + seeded simulator.

Wire format (research/raw/R1 + D1): one POST sends {model, state, questions}
with all of a case's typed questions in a single call; the response carries a
probability distribution per question (noul -> P(yes); choice -> per label;
score -> per level index + legend), usage tokens, and the served model id.
Hosted Drex silently substitutes unpinned/unknown models -> we assert
response.model == settings().drex_model. 429/529 are retried honoring the
retry-after-ms header. Only HTTP 200 is billed: input_tokens x $/Mtok.
"""

from __future__ import annotations

import hashlib
import random
import time
from typing import Any

import httpx

from billproof.adapters.base import DecisionAdapter
from billproof.adapters.simulate import simulate_answers, simulate_call_meta
from billproof.config import settings
from billproof.schema import Answer, DecisionResult, GoldItem, Question

SYSTEMONE_PATH = "/v1/systemone"
DEFAULT_PRICE_PER_MTOK = 0.03  # console hosted: $0.03/1M input tokens, output free
RETRYABLE_STATUS = {429, 529}
CLIENT_HEADERS = {"X-Nace-Client": "billproof/0.1"}


class ModelMismatch(RuntimeError):
    """The service silently served a different model than the pinned id."""


def _question_spec(q: Question) -> dict[str, Any]:
    spec: dict[str, Any] = {"type": q.type, "instructions": q.instructions}
    if q.criteria is not None:
        spec["criteria"] = q.criteria
    return spec


def _to_answer(q: Question, raw: dict[str, Any], option_order: list[str] | None = None) -> Answer:
    """Map one wire answer to schema.Answer with native probabilities."""
    options = option_order or q.options()
    if q.type == "noul":
        p = min(max(float(raw.get("noul", 0.5)), 0.0), 1.0)
        probs = {"yes": p, "no": round(1.0 - p, 6)}
        choice = "yes" if p >= 0.5 else "no"
        conf = raw.get("confidence")
        return Answer(name=q.name, choice=choice, probabilities=probs,
                      confidence=float(conf) if conf is not None else max(p, 1.0 - p),
                      confidence_kind="native", raw=dict(raw))
    src = raw.get("probabilities") or {}
    if q.type == "score":
        probs = {label: float(src.get(str(i), src.get(label, 0.0)) or 0.0)
                 for i, label in enumerate(options)}
    else:
        probs = {label: float(src.get(label, 0.0) or 0.0) for label in options}
    if sum(probs.values()) <= 0:
        probs = {label: 1.0 / len(options) for label in options}
    choice = raw.get("choice")
    if choice not in options:
        choice = max(probs, key=probs.get)
    conf = raw.get("confidence")
    return Answer(name=q.name, choice=str(choice), probabilities=probs,
                  confidence=float(conf) if conf is not None else probs[choice],
                  confidence_kind="native", raw=dict(raw))


def _shuffled_spec(q: Question, rng: random.Random) -> tuple[dict[str, Any], list[str]]:
    """Wire question with options reordered (shuffle check, PLAN 5b). Returns (spec, order sent)."""
    options = q.options()
    if q.type == "noul" or len(options) < 2:
        return _question_spec(q), options
    perm = options[:]
    rng.shuffle(perm)
    if perm == options:
        perm = options[::-1]
    spec: dict[str, Any] = {"type": q.type, "instructions": q.instructions}
    if q.type == "choice":
        crit = q.criteria or {}
        spec["criteria"] = {k: crit.get(k) for k in perm}
    else:  # score: criteria is the ordered level list; legend comes back as sent
        spec["criteria"] = perm
    return spec, perm


class DrexAdapter:
    """Hosted Drex. One /v1/systemone call decides all questions for a case."""

    lane = "drex"
    simulated = False

    def __init__(self, api_key: str, base_url: str, model: str,
                 price_per_mtok: float = DEFAULT_PRICE_PER_MTOK, *,
                 client: httpx.Client | None = None, max_retries: int = 4,
                 timeout: float = 65.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.price_per_mtok = price_per_mtok
        self.max_retries = max_retries
        self.client = client or httpx.Client(timeout=timeout)

    @staticmethod
    def list_models(base_url: str, api_key: str, *, client: httpx.Client | None = None) -> Any:
        c = client or httpx.Client(timeout=20)
        return c.get(base_url.rstrip("/") + "/v1/models",
                     headers={"Authorization": f"Bearer {api_key}"}).raise_for_status().json()

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json", **CLIENT_HEADERS}
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.client.post(self.base_url + SYSTEMONE_PATH, json=body, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == self.max_retries:
                    raise
                time.sleep(min(0.5 * 2**attempt, 5.0))
                continue
            if resp.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                delay = resp.headers.get("retry-after-ms")
                time.sleep(float(delay) / 1000.0 if delay else min(0.5 * 2**attempt, 5.0))
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError("unreachable")  # pragma: no cover

    def _call(self, state: dict | str, specs: dict[str, dict]) -> dict[str, Any]:
        data = self._post({"model": self.model, "state": state, "questions": specs})
        reported = data.get("model")
        if reported != self.model:
            raise ModelMismatch(
                f"pinned model {self.model!r} but service reported {reported!r}; "
                "check DREX_MODEL against GET /v1/models")
        return data

    def decide(self, state: dict | str, questions: list[Question], *, case_id: str,
               gold: dict[str, GoldItem] | None = None, shuffle_check: bool = False) -> DecisionResult:
        t0 = time.monotonic()
        calls = 0
        input_tokens = output_tokens = 0

        specs = {q.name: _question_spec(q) for q in questions}
        data = self._call(state, specs)
        calls += 1
        usage = data.get("usage") or {}
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
        wire = data.get("answers") or {}
        answers = {q.name: _to_answer(q, wire.get(q.name) or {}) for q in questions}

        if shuffle_check:
            rng = random.Random(int(hashlib.sha256(f"shuffle|{case_id}".encode()).hexdigest()[:16], 16))
            order: dict[str, list[str]] = {}
            shuffled: dict[str, dict] = {}
            for q in questions:
                spec, sent = _shuffled_spec(q, rng)
                shuffled[q.name], order[q.name] = spec, sent
            data2 = self._call(state, shuffled)
            calls += 1
            usage = data2.get("usage") or {}
            input_tokens += int(usage.get("input_tokens") or 0)
            output_tokens += int(usage.get("output_tokens") or 0)
            wire2 = data2.get("answers") or {}
            for q in questions:
                reasked = _to_answer(q, wire2.get(q.name) or {}, order[q.name])
                if reasked.choice != answers[q.name].choice:
                    answers[q.name].raw["flip"] = True  # unstable under reorder -> escalate

        return DecisionResult(
            lane=self.lane, model=self.model, answers=answers,
            latency_ms=(time.monotonic() - t0) * 1000,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=input_tokens * self.price_per_mtok / 1e6,
            request_id=data.get("request_id"), calls=calls)


class SimulatedDrexAdapter:
    """Seeded mock used whenever MOCK_MODE or a missing key selects simulation."""

    lane = "drex"
    simulated = True

    def __init__(self, model: str | None = None, *, seed: str = "sim", realtime: bool = False):
        self.model = model or settings().drex_model
        self.seed = seed
        self.realtime = realtime

    def decide(self, state: dict | str, questions: list[Question], *, case_id: str,
               gold: dict[str, GoldItem] | None = None, shuffle_check: bool = False) -> DecisionResult:
        seed = f"{self.seed}|{case_id}"
        answers = simulate_answers(questions, gold, profile="drex", seed=seed)
        calls = 1
        if shuffle_check:
            # re-ask with a different seed; a flip means the argmax moved
            reasked = simulate_answers(questions, gold, profile="drex", seed=seed + "|shuffled")
            calls = 2
            for q in questions:
                if reasked[q.name].choice != answers[q.name].choice:
                    answers[q.name].raw["flip"] = True
        meta = simulate_call_meta("drex", len(questions), seed, state=state, questions=questions)
        if self.realtime:
            time.sleep(meta["latency_ms"] / 1000)
        return DecisionResult(lane=self.lane, model=self.model, answers=answers,
                              latency_ms=meta["latency_ms"], input_tokens=meta["input_tokens"],
                              output_tokens=meta["output_tokens"], cost_usd=meta["cost_usd"],
                              simulated=True, calls=calls)


def get_drex() -> DecisionAdapter:
    """Real adapter when credentials are configured, else the seeded simulator."""
    s = settings()
    if s.use_real("drex"):
        return DrexAdapter(s.nace_api_key, s.nace_base_url, s.drex_model,
                           s.drex_price_per_mtok or DEFAULT_PRICE_PER_MTOK)
    return SimulatedDrexAdapter(s.drex_model)
