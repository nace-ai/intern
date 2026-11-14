"""Keenable web search and page fetch (https://docs.keenable.ai, read 2026-11-14).

    POST https://api.keenable.ai/v1/search[/public]   JSON body {"query", "max_results"} -> {"results": [{"title", "url", "snippet"}]}
    GET  https://api.keenable.ai/v1/fetch[/public]    ?url=...&max_chars=...             -> {"title", "content", "truncated"}

With KEENABLE_API_KEY the keyed endpoints are used (Authorization: Bearer). Without a key the public endpoints
are used: they need only an app title header and allow 1,000 requests an hour per address. A web page is
untrusted input: the trial lets Drex check it before any of its text reaches the jurors (engine/trial.py).
Every failure returns nothing, so a search outage never stops a trial.
"""
from __future__ import annotations

import urllib.parse

from . import config, net

BASE = "https://api.keenable.ai/v1"


def _auth() -> tuple[dict, str]:
    key = config.env_key("KEENABLE_API_KEY")
    return ({"Authorization": "Bearer " + key}, "") if key else ({"X-Keenable-Title": config.KEENABLE_TITLE}, "/public")


def official_host(url: str) -> bool:
    """True for an https address on a court or government site (.gov, or a state's .us domain). Decided in code."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host.endswith(".gov") or host.endswith(".us"))


def search(query: str, max_results: int = 3) -> tuple[list[dict], int]:
    """([{title, url, snippet}], milliseconds). [] on any failure."""
    headers, suffix = _auth()
    try:
        _, data, ms = net.json_call("POST", f"{BASE}/search{suffix}", {"query": query, "max_results": max_results}, headers, tries=1)
    except net.NetError:
        return [], 0
    hits = data.get("results") if isinstance(data, dict) else None
    return [{"title": str(h.get("title") or ""), "url": str(h.get("url") or ""), "snippet": str(h.get("snippet") or "")}
            for h in hits or [] if isinstance(h, dict) and str(h.get("url") or "").startswith("https://")][:max_results], ms


def fetch(url: str, max_chars: int = 3000) -> tuple[dict | None, int]:
    """({title, content, url}, milliseconds) for an https page, or (None, 0)."""
    if urllib.parse.urlsplit(url).scheme != "https":
        return None, 0
    headers, suffix = _auth()
    query = urllib.parse.urlencode({"url": url, "max_chars": str(max_chars)})
    try:
        _, data, ms = net.json_call("GET", f"{BASE}/fetch{suffix}?{query}", None, headers, tries=1)
    except net.NetError:
        return None, 0
    if not isinstance(data, dict) or not data.get("content"):
        return None, 0
    return {"title": str(data.get("title") or ""), "content": str(data["content"])[:max_chars], "url": url}, ms
