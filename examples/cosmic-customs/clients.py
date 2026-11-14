"""Provider adapters. Every external call in the project goes through this module."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from config import (
    DREX_INPUT_USD_PER_TOKEN,
    DREX_PRICING_PROVENANCE,
    NACE_BASE_URL,
    NACE_DREX_MODEL,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
    nace_api_key,
    openrouter_api_key,
)
from schemas import DecisionInput, DecisionResult, Verdict

VERDICTS = ("CLEAR", "HOLD", "REVIEW")
DREX_TIMEOUT_S = 60.0
OPENROUTER_TIMEOUT_S = 90.0
DOCUMENT_WAIT_S = 45
DOCUMENT_POLL_DEADLINE_S = 180.0


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _result(request: DecisionInput, provider: str, mode: str, **kwargs: Any) -> DecisionResult:
    payload = {
        "case_id": request.case_id,
        "provider": provider,
        "requested_model": None,
        "actual_model": None,
        "verdict": None,
        "probabilities": None,
        "status": "error",
        "latency_ms": None,
        "server_latency_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
        "cost_source": None,
        "request_id": None,
        "input_hash": request.input_hash,
        "timestamp": utc_now(),
        "mode": mode,
        "cached": None,
        "error": None,
    }
    payload.update(kwargs)
    return DecisionResult.model_validate(payload)


class MockDecisionClient:
    """Deterministic local stand-in. It never opens a socket."""

    def __init__(self, provider: str) -> None:
        if provider not in ("drex", "openrouter"):
            raise ValueError(provider)
        self.provider = provider

    async def decide(self, request: DecisionInput) -> DecisionResult:
        digest = hashlib.sha256(f"{self.provider}:{request.case_id}:{request.input_hash}".encode("utf-8")).hexdigest()
        verdict: Verdict = VERDICTS[int(digest[:2], 16) % 3]
        probabilities = {
            label: (0.7 if label == verdict else 0.15) for label in VERDICTS
        }
        return _result(
            request,
            self.provider,
            "mock",
            requested_model=f"mock-{self.provider}",
            actual_model=f"mock-{self.provider}",
            verdict=verdict,
            probabilities=probabilities,
            status="ok",
            latency_ms=None,
            server_latency_ms=None,
            cost_usd=None,
            cost_source="not_measured",
            error=None,
        )


class DrexClient:
    def __init__(self, http: httpx.AsyncClient, api_key: str | None = None, model: str = NACE_DREX_MODEL) -> None:
        self.http = http
        self.api_key = api_key if api_key is not None else nace_api_key()
        self.model = model
        self.pricing_provenance = DREX_PRICING_PROVENANCE
        self.usd_per_input_token = DREX_INPUT_USD_PER_TOKEN

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RuntimeError("NACE_API_KEY is not configured")
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def decide(self, request: DecisionInput) -> DecisionResult:
        body = {
            "model": self.model,
            "state": request.shared_input,
            "questions": {
                "verdict": {
                    "type": "choice",
                    "instructions": "Choose the single customs verdict required by the rulebook in the state.",
                    "criteria": {
                        "CLEAR": "Release the shipment under rule 5.",
                        "HOLD": "Detain the shipment under rule 1.",
                        "REVIEW": "Send the shipment to a human reviewer under rules 2, 3, or 4.",
                    },
                }
            },
        }
        started = time.perf_counter()
        try:
            response = await self.http.post(
                f"{NACE_BASE_URL}/systemone",
                headers=self._headers(),
                json=body,
                timeout=DREX_TIMEOUT_S,
            )
        except httpx.TimeoutException as exc:
            return _result(
                request,
                "drex",
                "live",
                requested_model=self.model,
                status="timeout",
                latency_ms=(time.perf_counter() - started) * 1000,
                error=type(exc).__name__,
            )
        except httpx.HTTPError as exc:
            return _result(
                request,
                "drex",
                "live",
                requested_model=self.model,
                status="error",
                latency_ms=(time.perf_counter() - started) * 1000,
                error=type(exc).__name__,
            )
        latency_ms = (time.perf_counter() - started) * 1000
        request_id = response.headers.get("x-request-id")
        try:
            payload = response.json()
        except ValueError:
            return _result(
                request,
                "drex",
                "live",
                requested_model=self.model,
                status="invalid",
                latency_ms=latency_ms,
                request_id=request_id,
                error=f"non-json HTTP {response.status_code}",
            )
        if isinstance(payload, dict) and payload.get("request_id"):
            request_id = str(payload["request_id"])
        if response.status_code != 200:
            message = ""
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict):
                    message = str(error.get("message") or error.get("type") or "")
            return _result(
                request,
                "drex",
                "live",
                requested_model=self.model,
                status="error",
                latency_ms=latency_ms,
                request_id=request_id,
                error=message or f"HTTP {response.status_code}",
            )
        return self._parse_success(request, payload, latency_ms, request_id)

    def _parse_success(
        self,
        request: DecisionInput,
        payload: dict[str, Any],
        latency_ms: float,
        request_id: str | None,
    ) -> DecisionResult:
        answers = payload.get("answers")
        usage = payload.get("usage")
        answer = answers.get("verdict") if isinstance(answers, dict) else None
        problems: list[str] = []
        verdict = None
        probabilities = None
        if not isinstance(answer, dict) or answer.get("type") != "choice":
            problems.append("choice answer missing")
        else:
            choice = answer.get("choice")
            raw_probabilities = answer.get("probabilities")
            if choice not in VERDICTS:
                problems.append("choice is not a customs verdict")
            elif not isinstance(raw_probabilities, dict):
                problems.append("probabilities missing")
            else:
                parsed: dict[str, float] = {}
                for label in VERDICTS:
                    value = raw_probabilities.get(label)
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        problems.append(f"probability for {label} missing")
                        break
                    parsed[label] = float(value)
                else:
                    total = sum(parsed.values())
                    if abs(total - 1.0) > 0.02:
                        problems.append("probabilities do not sum to 1")
                    else:
                        verdict = choice
                        probabilities = parsed
        input_tokens = None
        output_tokens = None
        if not isinstance(usage, dict) or not isinstance(usage.get("input_tokens"), int):
            problems.append("usage.input_tokens missing")
        else:
            input_tokens = usage["input_tokens"]
            output_value = usage.get("output_tokens")
            output_tokens = output_value if isinstance(output_value, int) else None
        actual_model = payload.get("model") if isinstance(payload.get("model"), str) else None
        if actual_model != self.model:
            problems.append(f"actual model {actual_model} does not match requested {self.model}")
        server_latency = payload.get("evaluation_time_ms")
        server_latency_ms = float(server_latency) if isinstance(server_latency, (int, float)) and not isinstance(server_latency, bool) else None
        cost = None
        cost_source = None
        if input_tokens is not None and actual_model == self.model and self.usd_per_input_token is not None:
            cost = Decimal(input_tokens) * self.usd_per_input_token
            cost_source = "drex_input_token_rate:" + self.pricing_provenance
        status = "ok" if verdict and not problems else "invalid"
        return _result(
            request,
            "drex",
            "live",
            requested_model=self.model,
            actual_model=actual_model,
            verdict=verdict if status == "ok" else None,
            probabilities=probabilities if status == "ok" else probabilities,
            status=status,
            latency_ms=latency_ms,
            server_latency_ms=server_latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost if status == "ok" else None,
            cost_source=cost_source if status == "ok" else None,
            request_id=request_id,
            error="; ".join(problems) or None,
        )


class OpenRouterClient:
    def __init__(self, http: httpx.AsyncClient, api_key: str | None = None, model: str = OPENROUTER_MODEL) -> None:
        self.http = http
        self.api_key = api_key if api_key is not None else openrouter_api_key()
        self.model = model
        self.output_field = "max_tokens"
        self.max_tokens = 2048

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def decide(self, request: DecisionInput) -> DecisionResult:
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Reply with the customs verdict required by the user message. Do not add an explanation.",
                },
                {"role": "user", "content": request.shared_input},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "customs_verdict",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "verdict": {"type": "string", "enum": ["CLEAR", "HOLD", "REVIEW"]},
                        },
                        "required": ["verdict"],
                        "additionalProperties": False,
                    },
                },
            },
            "provider": {"require_parameters": True},
            self.output_field: self.max_tokens,
        }
        started = time.perf_counter()
        try:
            response = await self.http.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers=self._headers(),
                json=body,
                timeout=OPENROUTER_TIMEOUT_S,
            )
        except httpx.TimeoutException as exc:
            return _result(
                request,
                "openrouter",
                "live",
                requested_model=self.model,
                status="timeout",
                latency_ms=(time.perf_counter() - started) * 1000,
                error=type(exc).__name__,
            )
        except httpx.HTTPError as exc:
            return _result(
                request,
                "openrouter",
                "live",
                requested_model=self.model,
                status="error",
                latency_ms=(time.perf_counter() - started) * 1000,
                error=type(exc).__name__,
            )
        latency_ms = (time.perf_counter() - started) * 1000
        request_id = response.headers.get("x-request-id")
        try:
            payload = response.json()
        except ValueError:
            return _result(
                request,
                "openrouter",
                "live",
                requested_model=self.model,
                status="invalid",
                latency_ms=latency_ms,
                request_id=request_id,
                error=f"non-json HTTP {response.status_code}",
            )
        if isinstance(payload, dict) and isinstance(payload.get("id"), str):
            request_id = payload["id"]
        if response.status_code != 200:
            message = ""
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict):
                    message = str(error.get("message") or "")
            return _result(
                request,
                "openrouter",
                "live",
                requested_model=self.model,
                status="error",
                latency_ms=latency_ms,
                request_id=request_id,
                error=message or f"HTTP {response.status_code}",
            )
        return self._parse_success(request, payload, latency_ms, request_id)

    def _parse_success(
        self,
        request: DecisionInput,
        payload: dict[str, Any],
        latency_ms: float,
        request_id: str | None,
    ) -> DecisionResult:
        problems: list[str] = []
        choices = payload.get("choices")
        content = None
        finish = None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            problems.append("choices missing")
        else:
            finish = choices[0].get("finish_reason")
            message = choices[0].get("message")
            if isinstance(message, dict):
                content = message.get("content")
            if finish == "length":
                problems.append("output truncated")
        verdict = None
        if isinstance(content, str):
            import json

            try:
                parsed = json.loads(content)
            except ValueError:
                problems.append("verdict json unreadable")
            else:
                if isinstance(parsed, dict) and parsed.get("verdict") in VERDICTS and set(parsed) == {"verdict"}:
                    verdict = parsed["verdict"]
                else:
                    problems.append("verdict json does not match the schema")
        else:
            problems.append("message content missing")
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        input_tokens = usage.get("prompt_tokens") if isinstance(usage.get("prompt_tokens"), int) else None
        output_tokens = usage.get("completion_tokens") if isinstance(usage.get("completion_tokens"), int) else None
        cost = None
        cost_source = None
        cost_value = usage.get("cost")
        if isinstance(cost_value, bool) or not isinstance(cost_value, (int, float)):
            cost = None
        else:
            cost = Decimal(str(cost_value))
            cost_source = "openrouter_usage.cost"
        prompt_details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
        cached_tokens = prompt_details.get("cached_tokens")
        cached = None
        if isinstance(cached_tokens, int):
            cached = cached_tokens > 0
        actual_model = payload.get("model") if isinstance(payload.get("model"), str) else None
        status = "ok" if verdict and not problems else "invalid"
        return _result(
            request,
            "openrouter",
            "live",
            requested_model=self.model,
            actual_model=actual_model,
            verdict=verdict if status == "ok" else None,
            probabilities=None,
            status=status,
            latency_ms=latency_ms,
            server_latency_ms=None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost if status == "ok" else None,
            cost_source=cost_source if status == "ok" else None,
            request_id=request_id,
            cached=cached,
            error="; ".join(problems) or None,
        )


class DocumentClient:
    """Console perception workflow: upload grant, parse, extract, ground, poll."""

    def __init__(self, http: httpx.AsyncClient, api_key: str | None = None) -> None:
        self.http = http
        self.api_key = api_key if api_key is not None else nace_api_key()

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RuntimeError("NACE_API_KEY is not configured")
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def create_upload_grant(self, path: str, total_size_bytes: int) -> dict[str, Any]:
        response = await self.http.post(
            f"{NACE_BASE_URL}/documents/upload-grants",
            headers=self._headers(),
            json={"path": path, "max_bytes": total_size_bytes + 1024, "total_size_bytes": total_size_bytes, "ttl_seconds": 600},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        for key in ("workspace_id", "upload_url", "token", "expires_at"):
            if key not in payload:
                raise RuntimeError(f"upload grant missing {key}")
        return payload

    async def redeem_upload(self, grant: dict[str, Any], filename: str, data: bytes, path: str) -> dict[str, Any]:
        import json

        response = await self.http.post(
            grant["upload_url"],
            headers={"X-Upload-Token": grant["token"]},
            data={"metadata": json.dumps({"path": path})},
            files={"file": (filename, data, "application/pdf")},
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        file_info = (payload.get("result") or {}).get("file") or {}
        if "file_id" not in file_info:
            raise RuntimeError("upload response missing result.file.file_id")
        return payload

    async def parse(self, workspace_id: str, file_id: str, idempotency_key: str) -> dict[str, Any]:
        return await self._submit(
            "parse",
            {
                "source": {"type": "workspace_file", "workspace_id": workspace_id, "file_id": file_id},
                "output": {"formats": ["markdown", "text"]},
            },
            idempotency_key,
        )

    async def extract(self, parse_job_id: str, schema: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        return await self._submit(
            "extract",
            {
                "source": {"type": "parse_result", "job_id": parse_job_id},
                "schema": schema,
                "citations": {"enabled": True, "include_source_text": True},
            },
            idempotency_key,
        )

    async def ground(self, parse_job_id: str, targets: list[dict[str, str]], idempotency_key: str) -> dict[str, Any]:
        return await self._submit(
            "ground",
            {
                "source": {"type": "parse_result", "job_id": parse_job_id},
                "targets": targets,
            },
            idempotency_key,
        )

    async def _submit(self, tool: str, body: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        response = await self.http.post(
            f"{NACE_BASE_URL}/documents/{tool}?wait_seconds={DOCUMENT_WAIT_S}",
            headers={**self._headers(), "Idempotency-Key": idempotency_key},
            json=body,
            timeout=DOCUMENT_WAIT_S + 20,
        )
        response.raise_for_status()
        return response.json()

    async def get_job(self, job_id: str) -> dict[str, Any]:
        response = await self.http.get(
            f"{NACE_BASE_URL}/documents/jobs/{job_id}",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    async def wait_job(self, job: dict[str, Any], deadline_s: float = DOCUMENT_POLL_DEADLINE_S) -> dict[str, Any]:
        """Poll the existing job. A timeout does not create another paid job."""
        current = job
        started = time.perf_counter()
        while current.get("status") not in ("succeeded", "failed", "cancelled"):
            if time.perf_counter() - started > deadline_s:
                current = dict(current)
                current["poll_timeout"] = True
                return current
            await _async_sleep(2)
            current = await self.get_job(str(current["job_id"]))
        return current


async def _async_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


def settled_document_cost(job: dict[str, Any]) -> Decimal | None:
    cost = job.get("cost")
    if not isinstance(cost, dict):
        return None
    total = cost.get("total_usd")
    if total is None:
        return None
    return Decimal(str(total))


async def list_nace_models(http: httpx.AsyncClient, api_key: str) -> list[dict[str, Any]]:
    response = await http.get(
        f"{NACE_BASE_URL}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    models = payload.get("models")
    if not isinstance(models, list):
        raise RuntimeError("GET /v1/models did not return a models list")
    return models


async def confirm_published_drex_rate(http: httpx.AsyncClient) -> bool:
    """Re-read the public pricing page. The models list does not include a price."""
    response = await http.get("https://console.nace.ai/docs/drex/reference/pricing", timeout=30)
    response.raise_for_status()
    text = response.text
    return "drex-max-v1.1" in text and "0.00000003" in text and "0.03" in text


async def find_openrouter_model(http: httpx.AsyncClient, api_key: str, model_id: str) -> dict[str, Any] | None:
    response = await http.get(
        f"{OPENROUTER_BASE_URL}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise RuntimeError("OpenRouter /models did not return data")
    for row in rows:
        if isinstance(row, dict) and row.get("id") == model_id:
            return row
    return None
