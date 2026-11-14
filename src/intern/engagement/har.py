"""HAR traffic evidence: the network exchange behind a browser-driven proof. Recorded HARs carry
live credentials, so they are sanitised (auth headers, cookies, token-shaped values masked; bodies
capped) before they become evidence; the report shows a readable request summary."""

from __future__ import annotations

import json
import re
from typing import Any

from intern.engagement.screenshot import redact_text

MASK = "••••"
SECRET_HEADER = re.compile(
    r"^(authorization|proxy-authorization|cookie|set-cookie|x-api-key|x-auth-token|x-csrf-token"
    r"|x-xsrf-token|.*-token|.*secret.*)$",
    re.IGNORECASE,
)
BODY_MAX = 64 * 1024
STATIC = re.compile(r"\.(css|js|mjs|map|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|otf)(\?|$)", re.IGNORECASE)
_TRAILING_NONSPACE = re.compile(r"\S+$")


def _mask_headers(headers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for h in headers:
        value = str(h.get("value", ""))
        if SECRET_HEADER.match(str(h.get("name", ""))):
            head = _TRAILING_NONSPACE.sub("", value[:7])
            h = {**h, "value": head + MASK}
        out.append(h)
    return out


def _mask_cookies(cookies: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if cookies is None:
        return None
    return [{**c, "value": MASK} for c in cookies]


def _mask_body(text: str | None, extra: list[str]) -> str | None:
    if text is None:
        return None
    if len(text) > BODY_MAX:
        text = f"{text[:BODY_MAX]}\n… ({len(text) - BODY_MAX} bytes truncated)"
    return redact_text(text, extra)


def sanitize_har(raw: dict[str, Any] | str, extra_secrets: list[str] | None = None) -> str:
    """A sanitised copy of a HAR document (the input is not modified), as a JSON string."""
    extra = extra_secrets or []
    har: Any = json.loads(raw) if isinstance(raw, str) else raw
    entries = []
    for e in har["log"]["entries"]:
        request: Any = dict(e["request"])
        request["headers"] = _mask_headers(request.get("headers", []))
        request["cookies"] = _mask_cookies(request.get("cookies"))
        if request.get("postData") is not None:
            request["postData"] = {**request["postData"], "text": _mask_body(request["postData"].get("text"), extra)}
        response: Any = dict(e["response"])
        response["headers"] = _mask_headers(response.get("headers", []))
        response["cookies"] = _mask_cookies(response.get("cookies"))
        if response.get("content") is not None:
            content = response["content"]
            if content.get("encoding") == "base64":
                response["content"] = {**content, "text": None}
            else:
                response["content"] = {**content, "text": _mask_body(content.get("text"), extra)}
        entries.append({**e, "request": request, "response": response})
    return json.dumps({**har, "log": {**har["log"], "entries": entries}}, indent=1)


def _kb(n: int | None) -> str:
    if n is None or n < 0:
        return "?"
    if n < 1024:
        return f"{n} B"
    return f"{n / 1024:.1f} kB"


def summarize_har(raw: dict[str, Any] | str, max: int = 14) -> str | None:
    """One line per meaningful request (documents / XHR / fetch; static assets skipped)."""

    try:
        har = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None
    entries = ((har or {}).get("log") or {}).get("entries")
    if not entries:
        return None
    rows = [
        e
        for e in entries
        if not STATIC.search(e["request"]["url"]) and e.get("_resourceType") not in ("image", "stylesheet", "font")
    ]
    lines = [
        f"{e['request']['method']:<6} {e['request']['url']}  →  {e['response']['status']} "
        f"{(e['response'].get('content') or {}).get('mimeType', '').split(';')[0]} "
        f"{_kb((e['response'].get('content') or {}).get('size'))}".rstrip()
        for e in rows[:max]
    ]
    if len(rows) > max:
        lines.append(f"… {len(rows) - max} more request(s) in the HAR file")
    return "\n".join(lines) if lines else None
