"""8-bit News: Keenable finds this week's news on a topic, and the story becomes a Drex Life world.

  1. Keenable search (last N days) returns ranked articles; Keenable fetch reads the top ones as markdown.
  2. The Life director turns the reporting into states and rules; every rule ends with its source numbers, e.g. [1, 3].
  3. Drex decides every cell, every step.
Keenable's keyless /public endpoints are used unless KEENABLE_API_KEY is set.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone

import httpx

from pixeldrex.llm import DeepInfra
from pixeldrex.life import DIRECTOR, World

API = "https://api.keenable.ai"
APP = "PixelDrex"


def _paths() -> tuple[dict, str, str]:
    key = os.getenv("KEENABLE_API_KEY")
    if key:
        return {"X-API-Key": key}, "/v1/search", "/v1/fetch"
    return {"X-Keenable-Title": APP}, "/v1/search/public", "/v1/fetch/public"


async def search_news(topic: str, *, days: int = 7, k: int = 5) -> list[dict]:
    headers, search_path, _ = _paths()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    async with httpx.AsyncClient(base_url=API, timeout=30, headers=headers) as c:
        r = await c.post(
            search_path,
            json={"query": f"{topic} news", "published_after": since, "max_results": k, "snippet_max_length": 400},
        )
        r.raise_for_status()
    return r.json().get("results", [])


async def fetch_article(url: str, max_chars: int = 3500) -> str | None:
    headers, _, fetch_path = _paths()
    try:
        async with httpx.AsyncClient(base_url=API, timeout=40, headers=headers) as c:
            r = await c.get(fetch_path, params={"url": url})
        if r.status_code != 200:
            return None
        return (r.json().get("content") or "")[:max_chars] or None
    except httpx.HTTPError:
        return None


async def world_from_news(topic: str, *, days: int = 7, k: int = 5, read: int = 2, on_line=None) -> tuple[World, dict]:
    say = on_line or (lambda s: None)
    results = await search_news(topic, days=days, k=k)
    if not results:
        raise RuntimeError(f"Keenable found no news about {topic!r} in the last {days} days")
    say(f"Keenable found {len(results)} articles from the last {days} days; reading the top {min(read, len(results))}")
    bodies = await asyncio.gather(*(fetch_article(r["url"]) for r in results[:read]))
    sources = []
    for i, r in enumerate(results, 1):
        sources.append(
            {
                "n": i,
                "title": r.get("title"),
                "url": r.get("url"),
                "published_at": r.get("published_at"),
                "snippet": r.get("snippet"),
                "text": bodies[i - 1] if i <= len(bodies) else None,
            }
        )
    for s in sources:
        say(f"[{s['n']}] {s['title']} ({(s['published_at'] or '')[:10]})" + (" — read in full" if s["text"] else ""))
    prompt = (
        f"Design a world that dramatises this week's news about: {topic}. Use these news reports (numbered). Pick "
        "4-5 cell states from the things the reporting describes spreading, growing, being contained or recovering "
        "(a condition such as wind or rain must be its own state if a rule depends on it). Rules may ONLY mention "
        "the states you defined, are phrased from the point of view of the cell that changes, and must END with the "
        "numbers of the reports they are based on, in the form [1] or [2, 4].\n\nReports:\n"
        + json.dumps([{k: v for k, v in s.items() if k != "url"} for s in sources], indent=1)
    )
    async with DeepInfra() as di:
        text = await di.chat(prompt, system=DIRECTOR, max_tokens=900, temperature=0.4, json_mode=True)
    m = re.search(r"\{.*\}", text, re.S)
    world = World.from_dict(json.loads(m.group(0) if m else text))
    meta = {
        "topic": topic,
        "days": days,
        "sources": [
            {k: s[k] for k in ("n", "title", "url", "published_at")} | {"read_in_full": bool(s["text"])}
            for s in sources
        ],
    }
    return world, meta
