"""Drex and OpenAI behind one interface: decide(task, narrative) -> Decision. Never raises.

Latency is wall-clock time of the decision call as seen by the client, including each SDK's own
default retries, so both providers are measured the same way. Prices are not hardcoded anywhere.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from evaluation.benchmark.tasks import Task


@dataclass
class Decision:
    option: str | None
    latency_seconds: float
    probabilities: dict[str, float] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    request_id: str | None = None
    model: str | None = None
    server_time_ms: float | None = None
    error: dict[str, str] | None = None  # {"code", "message"}; option is None whenever this is set
    raw: Any = None  # provider response summary kept verbatim for the audit trail


class Provider(Protocol):
    name: str

    def decide(self, task: Task, narrative: str) -> Decision: ...


# ------------------------------------------------------------------------------- Drex


class DrexProvider:
    name = "drex"

    def __init__(self, model: str = "drex-latest", timeout_seconds: float = 60.0, transport: Any = None):
        from agents.drex_client import NaceDrexDecider

        self.requested_model = model
        self._decider = NaceDrexDecider(model, timeout_seconds, transport)

    def close(self) -> None:
        self._decider.close()

    def decide(self, task: Task, narrative: str) -> Decision:
        from agents.drex_client import DrexError, QuestionSpec

        spec = QuestionSpec("choice", task.instructions, dict(task.options))
        started = time.perf_counter()
        try:
            outcome = self._decider.decide(narrative, {"decision": spec})
        except DrexError as error:
            return Decision(None, time.perf_counter() - started, error={"code": error.code, "message": error.message})
        answer = outcome.choices["decision"]
        return Decision(
            option=answer.selected,
            latency_seconds=outcome.duration_seconds,
            probabilities=answer.probabilities,
            input_tokens=outcome.input_tokens,
            request_id=outcome.request_id,
            model=outcome.model,
            server_time_ms=outcome.server_time_ms,
            raw={"choice": answer.selected, "probabilities": answer.probabilities, "request_id": outcome.request_id,
                 "model": outcome.model, "evaluation_time_ms": outcome.server_time_ms, "input_tokens": outcome.input_tokens},
        )


# ----------------------------------------------------------------------------- OpenAI


def _system_prompt(task: Task, structured: bool) -> str:
    options = "\n".join(f"- {label}: {description}" for label, description in task.options.items())
    ending = "Choose exactly one option." if structured else "Reply with only the option label, nothing else."
    return f"{task.instructions}\n\nOptions:\n{options}\n\n{ending}"


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9_ ]", "", text.strip().lower())


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str, temperature: float | None = None, structured: bool = True,
                 timeout_seconds: float = 60.0, api_key: str | None = None, http_client: Any = None):
        import openai

        self._openai = openai
        self.requested_model = model
        self._temperature = temperature
        self._structured = structured
        options: dict[str, Any] = {"timeout": timeout_seconds}
        if http_client is not None:
            options["http_client"] = http_client
        try:
            # api_key=None makes the SDK read OPENAI_API_KEY from the environment; it is never logged.
            self._client = openai.OpenAI(api_key=api_key, **options)
        except openai.OpenAIError as error:
            raise RuntimeError("OPENAI_API_KEY is missing; add it to the local .env or environment.") from error

    def close(self) -> None:
        self._client.close()

    def _request(self, task: Task, narrative: str) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.requested_model,
            "messages": [
                {"role": "system", "content": _system_prompt(task, self._structured)},
                {"role": "user", "content": narrative},
            ],
        }
        if self._structured:
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "decision",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"decision": {"type": "string", "enum": list(task.options)}},
                        "required": ["decision"],
                        "additionalProperties": False,
                    },
                },
            }
        if self._temperature is not None:
            request["temperature"] = self._temperature
        return request

    def _error_code(self, error: Exception) -> str:
        o = self._openai
        for cls, code in (
            (o.APITimeoutError, "provider_timeout"),
            (o.APIConnectionError, "provider_unavailable"),
            (o.AuthenticationError, "provider_auth_failed"),
            (o.RateLimitError, "provider_rate_limited"),
            (o.BadRequestError, "provider_rejected_request"),
            (o.APIStatusError, "provider_error"),
        ):
            if isinstance(error, cls):
                return code
        return "provider_error"

    def decide(self, task: Task, narrative: str) -> Decision:
        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(**self._request(task, narrative))
        except self._openai.OpenAIError as error:
            message = getattr(error, "message", None) or type(error).__name__
            return Decision(None, time.perf_counter() - started, error={"code": self._error_code(error), "message": str(message)[:300]})
        latency = time.perf_counter() - started

        choice = response.choices[0]
        content = choice.message.content
        refusal = getattr(choice.message, "refusal", None)
        usage = response.usage
        decision = Decision(
            option=None,
            latency_seconds=latency,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            request_id=response.id,
            model=response.model,
            raw={"content": content, "refusal": refusal, "finish_reason": choice.finish_reason, "id": response.id,
                 "model": response.model,
                 "usage": {"prompt_tokens": getattr(usage, "prompt_tokens", None), "completion_tokens": getattr(usage, "completion_tokens", None)}},
        )
        if refusal:
            decision.error = {"code": "refusal", "message": "The model refused to answer."}
            return decision
        parsed = self._parse(task, content)
        if parsed is None:
            decision.error = {"code": "invalid_output", "message": "The response was not exactly one of the allowed options."}
        else:
            decision.option = parsed
        return decision

    def _parse(self, task: Task, content: str | None) -> str | None:
        if not content:
            return None
        candidate = content
        if self._structured:
            try:
                value = json.loads(content)
            except ValueError:
                return None
            candidate = value.get("decision") if isinstance(value, dict) else None
            if not isinstance(candidate, str):
                return None
        wanted = _normalize(candidate)
        for label in task.options:
            if _normalize(label) == wanted:
                return label
        return None
