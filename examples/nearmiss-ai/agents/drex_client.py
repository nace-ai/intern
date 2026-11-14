"""Thin adapter around Nace Drex `system_one` (nace-sdk).

Documented API used (https://console.nace.ai/docs/developer-tools/python/questions):
  client.system_one(state=..., questions={id: Choice(...) | Noul(...)}, model=...)
  -> SystemOneResponse with .choices[id].choice / .probabilities,
     .nouls[id].noul (probability of yes), .model, .request_id

Agents depend only on the `Decider` protocol so tests can inject a fake. The
fake lives in tests/ and is never used at runtime.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class QuestionSpec:
    """One bounded question. `criteria` maps each label to its description (choice only)."""

    kind: Literal["choice", "noul"]
    instructions: str
    criteria: dict[str, str] | None = None

    @property
    def options(self) -> list[str]:
        if self.kind == "choice":
            return list(self.criteria or {})
        return ["yes", "no"]


@dataclass(frozen=True)
class ChoiceResult:
    selected: str
    probabilities: dict[str, float]


@dataclass
class DrexOutcome:
    choices: dict[str, ChoiceResult] = field(default_factory=dict)
    nouls: dict[str, float] = field(default_factory=dict)  # probability of "yes"
    model: str | None = None
    request_id: str | None = None
    duration_seconds: float = 0.0
    input_tokens: int | None = None  # billed input tokens (usage.input_tokens)
    server_time_ms: float | None = None  # Drex-reported model time (evaluation_time_ms)


class DrexError(Exception):
    """A Drex call that returned no usable decision. Message is safe to show in the UI."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Decider(Protocol):
    def decide(self, state: Any, questions: dict[str, QuestionSpec]) -> DrexOutcome: ...


def _check_probability(value: Any, where: str) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise DrexError("invalid_provider_output", f"Drex returned an out-of-range score at {where}.")
    return float(value)


def validate_outcome(outcome: DrexOutcome, questions: dict[str, QuestionSpec]) -> DrexOutcome:
    """Reject inconsistent provider output instead of patching it (plan: handle inconsistent output types)."""
    for qid, spec in questions.items():
        if spec.kind == "choice":
            answer = outcome.choices.get(qid)
            if answer is None:
                raise DrexError("invalid_provider_output", f"Drex returned no answer for question '{qid}'.")
            labels = set(spec.options)
            if answer.selected not in labels:
                raise DrexError("invalid_provider_output", f"Drex chose an unknown label for '{qid}'.")
            if set(answer.probabilities) != labels:
                raise DrexError(
                    "invalid_provider_output", f"Drex probabilities for '{qid}' do not cover every option."
                )
            for label, value in answer.probabilities.items():
                _check_probability(value, f"{qid}.{label}")
        else:
            if qid not in outcome.nouls:
                raise DrexError("invalid_provider_output", f"Drex returned no answer for question '{qid}'.")
            _check_probability(outcome.nouls[qid], qid)
    return outcome


class NaceDrexDecider:
    """Live decider. One instance per analysis run; call close() when finished."""

    def __init__(self, model: str, timeout_seconds: float = 60.0, transport: Any = None):
        import nace_sdk  # imported lazily so the service can start without the SDK configured

        self._sdk = nace_sdk
        self._model = model
        try:
            # Reads NACE_API_KEY from the environment; never passed through logs.
            # `transport` is only for offline tests (httpx.MockTransport).
            self._client = nace_sdk.NaceClient(
                model=model, timeout=timeout_seconds, client_info="nearmiss-ai/0.1", transport=transport
            )
        except nace_sdk.NaceError as error:
            raise DrexError(
                "provider_not_configured",
                "Nace API key is missing or malformed; set NACE_API_KEY in the local environment.",
            ) from error

    def close(self) -> None:
        self._client.close()

    def _to_sdk_question(self, spec: QuestionSpec):
        if spec.kind == "choice":
            return self._sdk.Choice(instructions=spec.instructions, criteria=dict(spec.criteria or {}))
        return self._sdk.Noul(instructions=spec.instructions)

    def decide(self, state: Any, questions: dict[str, QuestionSpec]) -> DrexOutcome:
        sdk = self._sdk
        payload = {qid: self._to_sdk_question(spec) for qid, spec in questions.items()}
        started = time.perf_counter()
        try:
            response = self._client.system_one(state=state, questions=payload, model=self._model)
        except Exception as error:  # mapped to safe, typed codes below
            raise _map_error(sdk, error) from error
        elapsed = time.perf_counter() - started

        outcome = DrexOutcome(
            model=response.model,
            request_id=response.request_id,
            duration_seconds=elapsed,
            input_tokens=getattr(response.usage, "input_tokens", None),
            server_time_ms=response.evaluation_time_ms,
        )
        for qid, answer in response.choices.items():
            outcome.choices[qid] = ChoiceResult(selected=answer.choice, probabilities=dict(answer.probabilities))
        for qid, answer in response.nouls.items():
            outcome.nouls[qid] = answer.noul
        return validate_outcome(outcome, questions)


def _map_error(sdk: Any, error: Exception) -> DrexError:
    """Convert SDK exceptions into safe error codes. Never include credentials."""
    request_id = getattr(error, "request_id", None)
    suffix = f" (request id {request_id})" if request_id else ""
    table = [
        ("NaceAuthenticationError", "provider_auth_failed", "Drex rejected the API key"),
        ("NaceInsufficientCreditError", "provider_insufficient_credit", "Nace account balance is insufficient"),
        ("NaceRateLimitError", "provider_rate_limited", "Drex rate limit reached"),
        ("NaceAPITimeoutError", "provider_timeout", "Drex call timed out"),
        ("NaceAPIConnectionError", "provider_unavailable", "Could not reach Drex"),
        ("NaceUnprocessableEntityError", "provider_rejected_request", "Drex rejected the request shape"),
        ("NaceInternalServerError", "provider_unavailable", "Drex service error"),
        ("NaceAPIResponseValidationError", "invalid_provider_output", "Drex returned an unexpected response body"),
    ]
    for class_name, code, text in table:
        cls = getattr(sdk, class_name, None)
        if cls is not None and isinstance(error, cls):
            server_message = getattr(error, "message", None)
            detail = f": {server_message}" if server_message and code == "provider_rejected_request" else ""
            return DrexError(code, f"{text}{detail}{suffix}.")
    if isinstance(error, getattr(sdk, "NaceError", ())):
        return DrexError("provider_error", f"Drex call failed{suffix}.")
    return DrexError("provider_error", f"Unexpected error while calling Drex ({type(error).__name__}).")
