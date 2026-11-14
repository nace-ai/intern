"""Seeded decision simulators shared by the drex and frontier mock adapters.

Profiles:
- "drex":     native probabilities, well calibrated (says 0.9 -> right ~90%);
              synthetic accuracy falls with difficulty (~97% easy -> ~70% hard).
              IMR uses a separate near-chance scenario, not measured model accuracy.
- "frontier": slightly more accurate on hard items, but confidence is
              SELF-REPORTED and over-confident (no probabilities returned).

Everything derives from sha256(profile | seed | question.name): fully
deterministic per (seed, question name), independent of call order.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from typing import Literal

from billproof.schema import Answer, GoldItem, Question

Profile = Literal["drex", "frontier"]

# accuracy(difficulty) interpolates easy -> hard over difficulty 0..1
_ACC: dict[str, tuple[float, float]] = {
    "drex": (0.97, 0.70),
    "frontier": (0.97, 0.78),
}
# IMR pre-decision categories contain much less information than billing-rule facts.
# These are conservative scenario assumptions, NOT fitted or measured IMR accuracy.
# data/real/SOURCES.md: balanced held-out sample; population/type rates near 50%.
IMR_ACCURACY_ASSUMPTIONS = {
    "version": "imr-near-chance-v1",
    "drex": {"easy": 0.70, "hard": 0.52, "confidence_sd": 0.025},
    "frontier": {"easy": 0.72, "hard": 0.55},
    "verification": "same_frontier_sampler_no_gold_repair",
}

# Explicit hypothetical service assumptions, NOT measured performance or vendor quotes.
# Configured prices override these illustration defaults. No verify-only discount.
SIMULATION_ASSUMPTIONS = {
    "version": "tokens-v1",
    "chars_per_token": 4,
    "drex": {"input_usd_per_mtok": 0.03, "output_usd_per_mtok": 0.0,
             "request_ms": 20.0, "prefill_tokens_s": 50000, "decode_tokens_s": 3000},
    "frontier": {"input_usd_per_mtok": 2.0, "output_usd_per_mtok": 8.0,
                 "request_ms": 150.0, "prefill_tokens_s": 15000, "decode_tokens_s": 120},
}


def _rng(*parts: str) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:16], 16))


def _accuracy(profile: str, difficulty: float, *, family: str | None = None) -> float:
    if family == "imr_overturn":
        assumption = IMR_ACCURACY_ASSUMPTIONS[profile]
        easy, hard = assumption["easy"], assumption["hard"]
    else:
        easy, hard = _ACC[profile]
    return easy + (hard - easy) * min(max(difficulty, 0.0), 1.0)


def _pick(choice: str, options: list[str], conf: float, rng: random.Random) -> dict[str, float]:
    """Build a probability distribution with `conf` mass on `choice`."""
    rest = [o for o in options if o != choice]
    weights = [rng.random() ** 2 for _ in rest]
    total = sum(weights) or 1.0
    probs = {choice: round(conf, 4)}
    probs.update({o: round((1 - conf) * w / total, 4) for o, w in zip(rest, weights)})
    return probs


def _simulate_one(profile: str, q: Question, gold: GoldItem | None, rng: random.Random) -> Answer:
    options = q.options()
    target = gold.answer if gold and gold.answer in options else None
    is_imr = q.family == "imr_overturn"
    difficulty = gold.difficulty if gold else (1.0 if is_imr else 0.3)
    mu = _accuracy(profile, difficulty, family=q.family)

    if profile == "drex":
        # Calibrated by construction: stated conf c, then correct ~ Bernoulli(c),
        # so P(correct | confidence=c) = c.
        floor = 1.0 / len(options) + (0.0 if is_imr else 0.02)
        sd = IMR_ACCURACY_ASSUMPTIONS["drex"]["confidence_sd"] if is_imr else 0.07
        conf = min(0.999, max(floor, rng.gauss(mu, sd)))
        correct = rng.random() < conf
        choice = target if (correct and target) else rng.choice([o for o in options if o != target] or options)
        probs = _pick(choice, options, conf, rng)
        return Answer(name=q.name, choice=choice, probabilities=probs,
                      confidence=probs[choice], confidence_kind="native",
                      raw={"sim": "drex"})

    correct = rng.random() < mu
    choice = target if (correct and target) else rng.choice([o for o in options if o != target] or options)
    # Over-confident self-report: stated conf sits well above true accuracy.
    conf = min(0.995, mu + rng.uniform(0.06, 0.22))
    return Answer(name=q.name, choice=choice, probabilities=None,
                  confidence=round(conf, 4), confidence_kind="self_reported",
                  raw={"sim": "frontier"})


def simulate_answers(
    questions: list[Question],
    gold: dict[str, GoldItem] | None,
    *,
    profile: Profile,
    seed: str,
) -> dict[str, Answer]:
    """Deterministic answers for a whole-case call. gold may be None (picks freely)."""
    gold = gold or {}
    return {
        q.name: _simulate_one(profile, q, gold.get(q.name), _rng("ans", profile, seed, q.name))
        for q in questions
    }


def estimate_tokens(value) -> int:
    """Transparent chars/4 proxy; live adapters continue using provider usage."""
    blob = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))
    return math.ceil(len(blob) / SIMULATION_ASSUMPTIONS["chars_per_token"])


def simulate_call_meta(
    profile: Profile, n_questions: int, seed: str, *, state=None,
    questions: list[Question] | None = None, request=None, verify: bool = False,
) -> dict:
    """Token-proportional service model shared by full and verification calls.

    Time = request overhead + input/prefill rate + output/decode rate.
    Cost = (input * input rate + output * output rate) / 1M, as in live clients.
    Defaults are hypothetical assumptions; they never alter answer calibration.
    `seed` stays for call-site compatibility, not artificial timing randomness.
    """
    from billproof.config import settings

    cfg = settings()
    assumptions = SIMULATION_ASSUMPTIONS[profile]
    if not n_questions:
        return dict(latency_ms=0.0, cost_usd=0.0, input_tokens=0, output_tokens=0)
    if request is None:
        specs = {q.name: {"type": q.type, "instructions": q.instructions,
                          **({"criteria": q.criteria} if q.criteria is not None else {})}
                 for q in questions or []}
        request = {"state": state, "questions": specs or {"count": n_questions}}
    input_tokens = estimate_tokens(request)
    if questions is None:
        output_tokens = n_questions * (32 if profile == "drex" else 60)
    else:
        examples = {}
        for q in questions:
            choice = max(q.options(), key=len)
            if profile == "drex":
                examples[q.name] = {"choice": choice,
                                    "probabilities": {o: 0.5 for o in q.options()}}
            else:
                examples[q.name] = {"choice": choice, "confidence": 0.95,
                                    "rationale": " ".join(["evidence"] * 20)}
                if verify:
                    examples[q.name]["action"] = "OVERRIDE"
        output_tokens = estimate_tokens({"answers": examples})
    if profile == "drex":
        input_price = cfg.drex_price_per_mtok if cfg.drex_price_per_mtok is not None else assumptions["input_usd_per_mtok"]
        output_price = 0.0  # Same input-only billing formula as DrexAdapter.
    else:
        rates = frontier_pricing()
        input_price = rates["input_usd_per_mtok"]
        output_price = rates["output_usd_per_mtok"]
    latency_ms = assumptions["request_ms"] + 1000 * (
        input_tokens / assumptions["prefill_tokens_s"] + output_tokens / assumptions["decode_tokens_s"])
    return {"latency_ms": round(latency_ms, 3),
            "cost_usd": (input_tokens * input_price + output_tokens * output_price) / 1_000_000,
            "input_tokens": input_tokens, "output_tokens": output_tokens}


def frontier_pricing(model: str | None = None) -> dict:
    """Configured router rates, otherwise labelled list-price estimates (USD/MTok).

    Snapshot checked 2026-11-13. Router markups, caching and service tiers vary.
    Unknown models get the explicit illustrative budget, never a zero-dollar rate.
    Sources: https://developers.openai.com/api/docs/pricing
    https://developers.openai.com/api/docs/models/gpt-4.1
    https://platform.claude.com/docs/en/about-claude/pricing
    """
    from billproof.config import settings

    cfg = settings()
    name = (model or cfg.frontier_model or "unknown").lower().rsplit("/", 1)[-1]
    table = [
        ("gpt-6.1-sol", 2.0, 10.0), ("gpt-6.1-terra", 1.0, 5.0),
        ("gpt-6-luna", 0.1, 0.5), ("gpt-6-astra", 10.0, 50.0),
        ("claude-opus-5-5", 4.0, 20.0), ("claude-opus-5.5", 4.0, 20.0),
        ("claude-sonnet-5", 2.0, 10.0), ("claude-sonnet-4", 3.0, 15.0),
        ("claude-opus-4-5", 5.0, 25.0), ("claude-opus-4-6", 5.0, 25.0),
        ("claude-opus-4-7", 5.0, 25.0), ("claude-opus-4-8", 5.0, 25.0),
        ("claude-haiku-4-5", 1.0, 5.0),
    ]
    family, price_in, price_out = "unknown_model_budget", 2.0, 8.0
    if name == "gpt-4.1" or (name.startswith("gpt-4.1-") and name[8:9].isdigit()):
        family, price_in, price_out = "gpt-4.1", 2.0, 8.0
    else:
        for prefix, pi, po in table:
            if name == prefix or name.startswith(prefix + "-"):
                family, price_in, price_out = prefix, pi, po
                break
    return {
        "kind": "estimated" if cfg.frontier_price_in is None or cfg.frontier_price_out is None else "configured",
        "family": family,
        "input_usd_per_mtok": cfg.frontier_price_in if cfg.frontier_price_in is not None else price_in,
        "output_usd_per_mtok": cfg.frontier_price_out if cfg.frontier_price_out is not None else price_out,
        "basis": "provider_usage_times_rates", "default_rate_date": "2026-11-13",
    }
