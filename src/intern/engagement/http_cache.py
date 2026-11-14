"""Cached HTTP for exploit research: vulnerability feeds change daily at most, and NVD rate-limits
unauthenticated clients (5 requests / 30 s), so responses are cached on disk with a TTL and NVD
calls are paced. The cache holds only public data; a 404 is cached too, so absences aren't refetched.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

#: One day, the default TTL: feeds (NVD, KEV, EPSS, Exploit-DB's index) refresh daily at most.
DAY = 24 * 60 * 60
#: Sentinel body marking a cached 404 (negative cache, same TTL as a real response).
NOT_FOUND = "\x00404"


class HttpError(RuntimeError):
    """A fetch that failed; the message names the host, the status says how."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def cache_file(cache_dir: Path, url: str, body: str | None = None) -> Path:
    """The cache entry for a request: its URL (and POST body) hashed to one file name."""
    key = hashlib.sha1((url + (body or "")).encode("utf-8")).hexdigest()
    return cache_dir / key


def _read_fresh(path: Path, ttl_s: float) -> str | None:
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return None
    if age >= ttl_s:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _write(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        pass  # the cache is best-effort


async def cached_text(
    url: str,
    *,
    ttl_s: float = DAY,
    client: httpx.AsyncClient,
    cache_dir: Path,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
) -> str | None:
    """The text body of a URL, from cache when fresh; None for a 404 (cached too)."""
    file = cache_file(cache_dir, url, body)
    cached = _read_fresh(file, ttl_s)
    if cached is not None:
        return None if cached == NOT_FOUND else cached
    response = await client.request(method, url, headers=headers, content=body)
    host = urlparse(url).hostname or url
    if response.status_code == 404:
        _write(file, NOT_FOUND)
        return None
    if response.status_code in (403, 429):
        hint = " — set NVD_API_KEY for 10x the limit" if "nvd" in host else ""
        raise HttpError(f"rate limited by {host} (HTTP {response.status_code}){hint}", status=response.status_code)
    if response.status_code >= 400:
        raise HttpError(f"{host}: HTTP {response.status_code}", status=response.status_code)
    text = response.text
    _write(file, text)
    return text


async def cached_json(
    url: str,
    *,
    ttl_s: float = DAY,
    client: httpx.AsyncClient,
    cache_dir: Path,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
) -> object | None:
    """A URL's JSON body from cache when fresh; None for a 404."""
    text = await cached_text(
        url, ttl_s=ttl_s, client=client, cache_dir=cache_dir, method=method, headers=headers, body=body
    )
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError as e:
        host = urlparse(url).hostname or url
        raise HttpError(f"{host}: response is not valid JSON ({e})") from e


#: When a host was last called, for spacing requests to rate-limited services.
_LAST: dict[str, float] = {}


async def pace(host: str, min_gap_s: float) -> None:
    """Space calls to one host (NVD: 6.5 s apart without a key, 0.7 s with one)."""
    wait = _LAST.get(host, 0.0) + min_gap_s - time.monotonic()
    if wait > 0:
        await asyncio.sleep(wait)
    _LAST[host] = time.monotonic()


def reset_pacing() -> None:
    _LAST.clear()
