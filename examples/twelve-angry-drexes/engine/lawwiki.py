"""LawWiki statutes. Order: the fixture in data/statutes, then the local store built from LawWiki's own
snapshots (engine/lawstore.py), then the public API (20 requests a minute) as the last resort. Network errors return nothing."""
from __future__ import annotations

import json
import re
import urllib.parse

from . import config, lawstore, net

KEY_RE = re.compile(r"^[A-Z]{2}/[A-Za-z][A-Za-z0-9\-]*/[0-9A-Za-z.\-]+$")  # JUR/CODE/SECTION, also blocks path tricks


def _fixture(key: str) -> dict | None:
    path = config.STATUTES_DIR / (key.replace("/", "_") + ".json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def statute(key: str) -> dict | None:
    """Statute record for `JUR/CODE/SECTION`: local fixture first, else the API, else None."""
    if not KEY_RE.match(key):
        raise ValueError(f"bad statute key {key!r}; expected JUR/CODE/SECTION")
    found = _fixture(key)
    if found is not None:
        return found
    found = lawstore.get(key)
    if found is not None:
        return found
    try:
        _, data, _ = net.json_call("GET", f"{config.LAWWIKI_BASE}/statutes/{key}", tries=1)
    except net.NetError:
        return None
    # Response envelope {"data": {...}} as in tools/fetch_lawwiki.py.
    return data.get("data") if isinstance(data, dict) else None


def search(q: str, jurisdiction: str | None = None, limit: int = 5, scope: list[str] | None = None) -> list[dict]:
    """Search sections: the local store when it exists (`scope` limits it to a role's codes), else the public API.
    [] on any network or parse failure."""
    if lawstore.available():
        return lawstore.search(q, jurisdiction, scope, limit)
    params = {"q": q, "limit": str(limit)}
    if jurisdiction:
        params["jurisdiction"] = jurisdiction
    url = f"{config.LAWWIKI_BASE}/search?{urllib.parse.urlencode(params)}"
    try:
        _, data, _ = net.json_call("GET", url, tries=1)
    except net.NetError:
        return []
    # Unconfirmed: the search envelope was not saved. Accept {"data": [...]}, {"data": {"results": [...]}} or a bare list.
    body = data.get("data", data) if isinstance(data, dict) else data
    if isinstance(body, dict):
        body = body.get("results", [])
    return [h for h in body if isinstance(h, dict)][:limit] if isinstance(body, list) else []


def link(key: str) -> str:
    """Public LawWiki page for a statute key."""
    rec = _fixture(key)
    if rec and rec.get("lawwiki_url"):
        return rec["lawwiki_url"]
    jur, code, sec = key.lower().split("/")
    return f"https://www.lawwiki.ai/{jur}/{code}/{sec}"
