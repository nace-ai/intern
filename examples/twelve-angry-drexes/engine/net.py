"""Small stdlib HTTP helper: 30 s timeout, generic User-Agent, 3 tries with backoff on 429 and 5xx."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

RETRY_CODES = {429, 500, 502, 503, 504, 529}


class NetError(RuntimeError):
    """A request failed for good. `status` is the HTTP status when there was one."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _site(url: str) -> str:
    """Last two labels of the host: console.nace.ai and drex.nace.ai are the same site."""
    return ".".join((urllib.parse.urlsplit(url).hostname or "").lower().split(".")[-2:])


class _SameSiteRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse a redirect to another site when the request carries a key, so a key never follows a redirect out."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.has_header("Authorization") and _site(newurl) != _site(req.full_url):
            raise urllib.error.HTTPError(newurl, code, "redirect to another site refused: request carries a key", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(req: urllib.request.Request, timeout: float):
    """The one place a request leaves the process. Tests replace it to prove nothing goes out."""
    return urllib.request.build_opener(_SameSiteRedirect).open(req, timeout=timeout)


def _delay(err: urllib.error.HTTPError | None, attempt: int) -> float:
    """Seconds to wait before the next try: Retry-After when it is a number, else 1, 2, 4 ... capped at 10."""
    after = err.headers.get("Retry-After", "") if err is not None else ""
    return min(10.0, float(after)) if after.isdigit() else min(10.0, 2.0 ** attempt)


def send(method: str, url: str, data: bytes | None = None, headers: dict | None = None,
         timeout: float = config.HTTP_TIMEOUT, tries: int = config.HTTP_TRIES) -> tuple[int, bytes]:
    """Send one request, retrying 429/5xx and connection errors. Returns (status, body bytes)."""
    hdrs = {"User-Agent": config.USER_AGENT, **(headers or {})}
    last: NetError | None = None
    for attempt in range(tries):
        err = None
        if not url.lower().startswith(("https://", "http://")):
            raise ValueError("only http and https addresses are allowed")
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with _open(req, timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            err = e
            last = NetError(f"HTTP {e.code} from {url}: {e.read()[:200].decode('utf-8', 'replace')}", e.code)
            if e.code not in RETRY_CODES:
                raise last from None
        except (urllib.error.URLError, OSError) as e:  # includes timeouts and resets
            last = NetError(f"network error for {url}: {e}")
        if attempt < tries - 1:
            time.sleep(_delay(err, attempt))
    assert last is not None
    raise last


def json_call(method: str, url: str, body: dict | None = None, headers: dict | None = None,
              **kw) -> tuple[int, dict, int]:
    """JSON request and response. Returns (status, parsed JSON, wall milliseconds)."""
    hdrs = {"Accept": "application/json", **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    t0 = time.monotonic()
    status, raw = send(method, url, data, hdrs, **kw)
    ms = int((time.monotonic() - t0) * 1000)
    try:
        return status, json.loads(raw), ms
    except ValueError as e:
        raise NetError(f"non-JSON response from {url}", status) from e
