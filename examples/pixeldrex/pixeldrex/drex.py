"""Async client for Nace's Drex decision API (POST /v1/systemone).

Drex answers typed questions about a `state` with calibrated probabilities:
  - noul:   yes/no      -> answers[q]["noul"]   = P(yes)
  - choice: pick a label -> answers[q]["choice"], ["probabilities"]
  - score:  ordered scale -> answers[q]["score"] = expected level index

Up to 512 questions and 8 images (Drex Max models only) per request.
Free accounts may have 8 requests in flight, so calls go through a semaphore.
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BASE_URL = os.getenv("NACE_BASE_URL", "https://console.nace.ai").rstrip("/")
DEFAULT_MODEL = os.getenv("DREX_MODEL", "drex-max-v1.1")
FINANCE_MODEL = "drex-max-finance"
IMAGE_MODELS = {"drex-max-v1.1", "drex-max-finance", "drex-latest"}
MAX_IN_FLIGHT = int(os.getenv("DREX_MAX_IN_FLIGHT", "8"))
MAX_QUESTIONS = 512
RETRYABLE = {429, 500, 502, 503, 529}


# ---------------------------------------------------------------- questions
def noul(instructions: str) -> dict:
    """Yes/no question. Answer: probability of yes."""
    return {"type": "noul", "instructions": instructions}


def choice(instructions: str, criteria: dict[str, str | None]) -> dict:
    """Pick one label. criteria = {label: description or None}."""
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: str, levels: list[str]) -> dict:
    """Ordered scale, lowest first. Answer: expected level index (float)."""
    return {"type": "score", "instructions": instructions, "criteria": levels}


# ---------------------------------------------------------------- images
def image_to_b64(path_or_bytes: str | Path | bytes, max_side: int = 1024, quality: int = 85) -> str:
    """Downscale and JPEG-encode an image as a data URL (keeps requests under 4 MiB)."""
    from PIL import Image

    raw = path_or_bytes if isinstance(path_or_bytes, bytes) else Path(path_or_bytes).read_bytes()
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------- results
@dataclass
class DrexResult:
    answers: dict[str, dict[str, Any]]
    model: str
    input_tokens: int
    eval_ms: float | None
    wall_ms: float
    request_id: str | None

    def p(self, qid: str) -> float:
        """P(yes) for a noul question."""
        return self.answers[qid]["noul"]

    def pick(self, qid: str) -> str:
        return self.answers[qid]["choice"]

    def probs(self, qid: str) -> dict[str, float]:
        return self.answers[qid]["probabilities"]

    def score(self, qid: str) -> float:
        return self.answers[qid]["score"]

    def confidence(self, qid: str) -> float:
        return self.answers[qid]["confidence"]


@dataclass
class Usage:
    """Running totals, for the Drex-vs-LLM comparison panel."""

    calls: int = 0
    questions: int = 0
    input_tokens: int = 0
    wall_ms: list[float] = field(default_factory=list)
    errors: int = 0

    # Drex Max list price is $0.06 / 1M input tokens (output not billed).
    # Override with DREX_PRICE_PER_M if the console shows a different rate.
    price_per_m: float = float(os.getenv("DREX_PRICE_PER_M", "0.06"))

    @property
    def cost_usd(self) -> float:
        return self.input_tokens / 1e6 * self.price_per_m

    def summary(self) -> dict:
        lat = sorted(self.wall_ms)
        return {
            "calls": self.calls,
            "questions": self.questions,
            "input_tokens": self.input_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "p50_ms": round(lat[len(lat) // 2], 1) if lat else None,
            "errors": self.errors,
        }


class DrexError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Drex HTTP {status}: {body[:500]}")
        self.status = status


# ---------------------------------------------------------------- client
class Drex:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL, timeout: float = 60.0):
        key = api_key or os.getenv("NACE_API_KEY")
        if not key:
            raise RuntimeError("NACE_API_KEY is not set (put it in .env)")
        self.model = model
        self.usage = Usage()
        self._sem = asyncio.Semaphore(MAX_IN_FLIGHT)
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "X-Nace-Client": "pixeldrex/0.1",
            },
        )

    async def __aenter__(self) -> "Drex":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    async def ask(
        self,
        state: Any,
        questions: dict[str, dict],
        *,
        images: list[str] | None = None,
        model: str | None = None,
        retries: int = 4,
    ) -> DrexResult:
        """One request: many questions about one state (and optional images)."""
        if not questions:
            raise ValueError("at least one question is required")
        if len(questions) > MAX_QUESTIONS:
            raise ValueError(f"{len(questions)} questions; split into chunks of {MAX_QUESTIONS} (see ask_many)")
        model = model or self.model
        body: dict[str, Any] = {"model": model, "state": state, "questions": questions}
        if images:
            if model not in IMAGE_MODELS:
                raise ValueError(f"{model} reads text only; use drex-max-v1.1 or drex-max-finance for images")
            if len(images) > 8:
                raise ValueError("at most 8 images per request")
            body["images"] = images

        delay = 1.0
        for attempt in range(retries + 1):
            async with self._sem:
                t0 = time.perf_counter()
                resp = await self._http.post("/v1/systemone", json=body)
                wall = (time.perf_counter() - t0) * 1000
            if resp.status_code == 200:
                data = resp.json()
                usage = data.get("usage") or {}
                self.usage.calls += 1
                self.usage.questions += len(questions)
                self.usage.input_tokens += usage.get("input_tokens", 0)
                self.usage.wall_ms.append(wall)
                return DrexResult(
                    answers=data["answers"],
                    model=data.get("model", model),
                    input_tokens=usage.get("input_tokens", 0),
                    eval_ms=data.get("evaluation_time_ms"),
                    wall_ms=wall,
                    request_id=data.get("request_id"),
                )
            if resp.status_code in RETRYABLE and attempt < retries:
                ra = resp.headers.get("retry-after-ms")
                wait = float(ra) / 1000 if ra else float(resp.headers.get("retry-after", delay))
                await asyncio.sleep(wait)
                delay = min(delay * 2, 8)
                continue
            self.usage.errors += 1
            raise DrexError(resp.status_code, resp.text)
        raise AssertionError("unreachable")

    async def ask_many(
        self, state: Any, questions: dict[str, dict], *, images: list[str] | None = None, model: str | None = None
    ) -> dict[str, dict]:
        """Like ask(), but splits >512 questions into parallel chunks and merges the answers."""
        items = list(questions.items())
        chunks = [dict(items[i : i + MAX_QUESTIONS]) for i in range(0, len(items), MAX_QUESTIONS)]
        results = await asyncio.gather(*(self.ask(state, c, images=images, model=model) for c in chunks))
        merged: dict[str, dict] = {}
        for r in results:
            merged.update(r.answers)
        return merged
