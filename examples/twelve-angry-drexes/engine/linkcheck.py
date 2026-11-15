"""Check that a hyperlink leads to a live page before it is posted. Standard library only.

A link is in one of three states:
  ok         the page answered 2xx (redirects followed)
  dead       404, 410, a server error or no answer: never post it
  unchecked  the site refuses automated clients (401, 403, 429, usually a bot check). A person's browser may
             still open it, but code cannot confirm that

A page drawn by a script answers 200 for any address (the Texas statutes site does): `tools/link_audit.py --browser`
opens those in a real browser and looks for the section heading.
"""
from __future__ import annotations

import concurrent.futures as cf
import re
import time
import urllib.error
import urllib.request

from . import config, net

TIMEOUT = 12       # seconds for one page
READ = 65536       # bytes read to find the title
TTL = 600          # seconds a result is reused inside one process
REFUSED = {401, 403, 429}
_TITLE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)
_cache: dict[str, tuple[float, dict]] = {}


def _row(url: str, state: str, status: int | None, why: str = "", final: str = "", title: str = "") -> dict:
    return {"url": url, "state": state, "status": status, "final": final or url, "title": title, "why": why}


def _once(url: str) -> dict:
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": config.USER_AGENT, "Accept": "text/html,*/*;q=0.8"})
    try:
        with net._open(req, TIMEOUT) as resp:
            found = _TITLE.search(resp.read(READ))
            title = re.sub(r"\s+", " ", found.group(1).decode("utf-8", "replace")).strip()[:160] if found else ""
            return _row(url, "ok", resp.status, final=resp.geturl(), title=title)
    except urllib.error.HTTPError as e:
        if e.code in REFUSED:
            wall = e.headers.get("cf-mitigated", "") if e.headers else ""
            return _row(url, "unchecked", e.code, "the site refuses automated clients" + (f" (bot check: {wall})" if wall else ""))
        return _row(url, "dead", e.code, f"HTTP {e.code}")
    except (urllib.error.URLError, OSError, ValueError) as e:   # includes timeouts and resets
        return _row(url, "dead", None, f"no answer ({type(e).__name__})")


def check(url: str) -> dict:
    """State of one link: {url, state, status, final, title, why}. A result is reused for TTL seconds."""
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < TTL:
        return hit[1]
    if not url.lower().startswith(("https://", "http://")):
        row = _row(url, "dead", None, "not an http address")
    else:
        row = _once(url)
        if row["state"] == "dead" and (row["status"] is None or row["status"] >= 500):   # one more try after a hiccup
            time.sleep(1)
            row = _once(url)
    _cache[url] = (time.monotonic(), row)
    return row


def check_many(urls) -> list[dict]:
    """States of several links, checked side by side. One row per address, in the order first seen."""
    uniq = list(dict.fromkeys(urls))
    if not uniq:
        return []
    with cf.ThreadPoolExecutor(min(8, len(uniq))) as pool:
        return list(pool.map(check, uniq))


def forget() -> None:
    """Drop every stored result, so the next check asks the site again."""
    _cache.clear()


def tally(rows: list[dict]) -> str:
    """One line for a log: how many links are ok, not checkable and dead."""
    n = {s: sum(1 for r in rows if r["state"] == s) for s in ("ok", "unchecked", "dead")}
    return f"{len(rows)} links: {n['ok']} ok, {n['unchecked']} not checkable by code, {n['dead']} dead"
