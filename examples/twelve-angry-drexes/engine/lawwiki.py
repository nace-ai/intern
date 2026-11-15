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


PUBLISHED = frozenset({"CA", "FL", "US"})
"""Jurisdictions lawwiki.ai publishes. Texas and New York are loaded there but not published yet, so every page of
theirs answers 404 (checked 2026-11-14 with tools/link_audit.py). Add a jurisdiction here when LawWiki publishes it."""


def official_url(jur: str, code: str, sec: str) -> str | None:
    """Address of a section on the official site LawWiki took the text from, for a record that does not carry one."""
    if jur == "TX":
        return f"https://statutes.capitol.texas.gov/Docs/{code.upper()}/htm/{code.upper()}.{sec.split('.')[0]}.htm#{sec}"
    if jur == "NY":
        return f"https://www.nysenate.gov/legislation/laws/{code.upper()}/{sec.upper()}"
    return None


def url_for(rec: dict) -> str:
    """Public page for a statute record: its LawWiki page when LawWiki publishes the jurisdiction, else the official
    source (the record's own `source_url`), so a link never points at a page LawWiki does not serve."""
    jur, code, sec = str(rec["jurisdiction"]).upper(), str(rec["code"]), str(rec["section"])
    lawwiki_page = rec.get("lawwiki_url") or f"https://www.lawwiki.ai/{jur.lower()}/{code.lower()}/{sec.lower()}"
    if jur in PUBLISHED:
        return lawwiki_page
    return rec.get("source_url") or official_url(jur, code, sec) or lawwiki_page


def link(key: str) -> str:
    """Public page for a statute key (see `url_for`)."""
    rec = _fixture(key)
    if rec:
        return url_for(rec)
    jur, code, sec = key.split("/")
    return url_for({"jurisdiction": jur, "code": code, "section": sec})
