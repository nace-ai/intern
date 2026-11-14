"""DeepInfra client (OpenAI-compatible): System 2 writing, the LLM baseline, and FLUX images."""

from __future__ import annotations

import asyncio
import base64
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DI_BASE = "https://api.deepinfra.com/v1/openai"
TEXT_MODEL = os.getenv("DI_TEXT_MODEL", "deepseek-ai/DeepSeek-V4-Flash")
VISION_MODEL = os.getenv("DI_VISION_MODEL", "Qwen/Qwen3-VL-235B-A22B-Instruct")
IMAGE_MODEL = os.getenv("DI_IMAGE_MODEL", "black-forest-labs/FLUX-1-schnell")


@dataclass
class LLMUsage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0  # DeepInfra reports usage.estimated_cost per call
    wall_ms: list[float] = field(default_factory=list)

    def summary(self) -> dict:
        lat = sorted(self.wall_ms)
        return {
            "calls": self.calls,
            "tokens": self.prompt_tokens + self.completion_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "p50_ms": round(lat[len(lat) // 2], 1) if lat else None,
        }


class DeepInfra:
    def __init__(self, api_key: str | None = None, max_in_flight: int = 16, timeout: float = 120.0):
        key = api_key or os.getenv("DEEPINFRA_API_KEY")
        if not key:
            raise RuntimeError("DEEPINFRA_API_KEY is not set (put it in .env)")
        self.usage = LLMUsage()
        self._sem = asyncio.Semaphore(max_in_flight)
        self._http = httpx.AsyncClient(timeout=timeout, headers={"Authorization": f"Bearer {key}"})

    async def __aenter__(self) -> "DeepInfra":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    async def chat(
        self,
        prompt: str,
        *,
        system: str | None = None,
        images: list[str] | None = None,  # data URLs
        model: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
        json_mode: bool = False,
        raw: bool = False,
    ) -> "str | tuple[str, dict]":
        model = model or (VISION_MODEL if images else TEXT_MODEL)
        content: list[dict] | str = prompt
        if images:
            content = [{"type": "text", "text": prompt}] + [
                {"type": "image_url", "image_url": {"url": u}} for u in images
            ]
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
        body: dict = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        for attempt in range(4):
            async with self._sem:
                t0 = time.perf_counter()
                resp = await self._http.post(f"{DI_BASE}/chat/completions", json=body)
                wall = (time.perf_counter() - t0) * 1000
            if resp.status_code in (429, 500, 502, 503) and attempt < 3:
                await asyncio.sleep(2**attempt)
                continue
            resp.raise_for_status()
            data = resp.json()
            u = data.get("usage") or {}
            self.usage.calls += 1
            self.usage.prompt_tokens += u.get("prompt_tokens", 0)
            self.usage.completion_tokens += u.get("completion_tokens", 0)
            self.usage.cost_usd += u.get("estimated_cost") or 0.0
            self.usage.wall_ms.append(wall)
            content = data["choices"][0]["message"]["content"] or ""
            return (content, data) if raw else content
        raise AssertionError("unreachable")

    async def image(self, prompt: str, *, model: str | None = None, size: str = "1024x1024") -> bytes:
        """Generate one image with FLUX on DeepInfra. Returns PNG/JPEG bytes."""
        body = {"model": model or IMAGE_MODEL, "prompt": prompt, "size": size, "n": 1, "response_format": "b64_json"}
        async with self._sem:
            resp = await self._http.post(f"{DI_BASE}/images/generations", json=body)
        resp.raise_for_status()
        return base64.b64decode(resp.json()["data"][0]["b64_json"])
