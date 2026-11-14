#!/usr/bin/env python3
"""Event-morning smoke test for live Drex, NDI, and the frontier token router.

Standalone: httpx plus billproof.config. Does not import adapters.

  cd billproof && .venv/bin/python scripts/smoke_real_apis.py --dry-run
  cd billproof && .venv/bin/python scripts/smoke_real_apis.py
  cd billproof && .venv/bin/python scripts/smoke_real_apis.py --only drex

--dry-run prints the requests and does not touch the network. Keys are never printed,
only whether they are set and a known prefix (nace_sk_, ndi_sk_, sk-, …).
Exit status is non-zero when a live check fails.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

BILLPROOF_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BILLPROOF_ROOT / "src"))

import httpx  # noqa: E402

from billproof.config import CACHE_DIR, REPO_ROOT, settings  # noqa: E402

CLIENT_NAME = "billproof-smoke"
MODEL_FINANCE = "drex-max-finance"
MODEL_MAX = "drex-max"
NACE_DEFAULT = "https://console.nace.ai"
NDI_DEFAULT = "https://ndi-api.nace.ai/api/v1"
DEMO_PDF = BILLPROOF_ROOT / "data" / "synthetic" / "pdf" / "SYN-D01_bill.pdf"
CROP_DIR = CACHE_DIR / "ndi" / "SYN-D01_bill"
_PREFIXES = ("nace_sk_", "ndi_sk_", "sk-ant-", "sk-proj-", "sk-")
_SECRET_RE = re.compile(
    r"nace_sk_[A-Za-z0-9_-]{8,}|ndi_sk_[A-Za-z0-9_-]{8,}"
    r"|sk-ant-[A-Za-z0-9_-]{8,}|sk-proj-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{20,}"
)
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_OK_JOB = {"succeeded", "success", "completed", "complete"}
_BAD_JOB = {"failed", "cancelled", "canceled", "error", "expired"}
_KEY_NAMES = (
    "NACE_API_KEY",
    "NDI_API_KEY",
    "FRONTIER_API_KEY",
    "BRIGHTDATA_API_TOKEN",
    "APIFY_TOKEN",
)
_ENV_ROWS = (
    ("NACE_API_KEY", "key"),
    ("NACE_BASE_URL", "plain"),
    ("DREX_MODEL", "plain"),
    ("NDI_API_KEY", "key"),
    ("NDI_BASE_URL", "plain"),
    ("FRONTIER_BASE_URL", "plain"),
    ("FRONTIER_API_KEY", "key"),
    ("FRONTIER_MODEL", "plain"),
    ("MOCK_MODE", "plain"),
    ("DREX_PRICE_PER_MTOK", "plain"),
    ("FRONTIER_PRICE_PER_MTOK_INPUT", "plain"),
    ("FRONTIER_PRICE_PER_MTOK_OUTPUT", "plain"),
)
_SENSITIVE_KEYS = {
    "token",
    "upload_token",
    "authorization",
    "api_key",
    "apikey",
    "x-api-key",
    "x-upload-token",
    "secret",
    "password",
    "sig",
    "signature",
}

DREX_STATE = {
    "bill": (
        "Line 2 repeats line 1: MRI brain, procedure 70553, "
        "same date of service, same charge."
    )
}
DREX_QUESTIONS = {
    "duplicate": {
        "type": "noul",
        "instructions": "Does the bill charge twice for one service on the same date?",
    },
    "action": {
        "type": "choice",
        "instructions": "What should happen to the repeated charge?",
        "criteria": {
            "OK": "The charge is appropriate",
            "DISPUTE": "Ask the provider to correct the bill",
            "APPEAL": "Appeal a payer denial",
        },
    },
}
EXTRACT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "total_due": {
            "type": "number",
            "description": "Amount due printed on the bill, as a number.",
        },
        "first_procedure": {
            "type": "string",
            "description": "Procedure code or name on the first service line.",
        },
    },
    "required": ["total_due", "first_procedure"],
}
FRONTIER_MESSAGES = [
    {
        "role": "system",
        "content": (
            "Return one JSON object and nothing else. "
            'Keys: duplicate ("yes" or "no"), confidence '
            "(a number from 0 to 1, your self-reported confidence)."
        ),
    },
    {
        "role": "user",
        "content": (
            "A medical bill lists procedure 70553 (MRI brain) twice, "
            "same date of service, same charge. Is the second line a duplicate charge?"
        ),
    },
]

SECRETS: list[str] = []


@dataclass
class Row:
    name: str
    status: str
    detail: str


@dataclass
class NdiTarget:
    base: str
    key: str
    direct: bool
    reason: str


def load_secrets() -> list[str]:
    found = []
    for name in _KEY_NAMES:
        value = os.getenv(name, "")
        if len(value) >= 8:
            found.append(value)
    return found


def key_prefix(value: str) -> str | None:
    for prefix in _PREFIXES:
        if value.startswith(prefix):
            return prefix
    return None


def redact_secret(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return "<unset>"
    prefix = key_prefix(value)
    if prefix:
        return prefix + "…redacted"
    return "<redacted>"


_SENSITIVE_QUERY = re.compile(
    r"(?:^|&)(?:sig|signature|token|x-upload-token|api_key|apikey)=",
    re.I,
)


def safe_url(url: str) -> str:
    """Drop query strings that carry a signature or token. Keep wait_seconds and similar."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<url>"
    if not parts.scheme:
        return url
    query = "" if parts.query and _SENSITIVE_QUERY.search(parts.query) else parts.query
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def strip_url_queries(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        raw = match.group(0).rstrip(").,]}>")
        tail = match.group(0)[len(raw) :]
        return safe_url(raw) + tail

    return _URL_RE.sub(repl, text)


def scrub(text: str) -> str:
    text = _SECRET_RE.sub("<redacted>", text)
    for secret in SECRETS:
        if secret and secret in text:
            text = text.replace(secret, "<redacted>")
    return strip_url_queries(text)


def guard(text: str) -> str:
    cleaned = scrub(text)
    for secret in SECRETS:
        if secret and secret in cleaned:
            raise RuntimeError("redaction missed a credential; output suppressed")
    return cleaned


def redact_tree(obj: object) -> object:
    if isinstance(obj, dict):
        cleaned = {}
        for key, value in obj.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                cleaned[key] = "<redacted>"
            else:
                cleaned[key] = redact_tree(value)
        return cleaned
    if isinstance(obj, list):
        return [redact_tree(item) for item in obj[:200]]
    if isinstance(obj, str):
        if obj.startswith(("http://", "https://")):
            return safe_url(scrub(obj))
        return scrub(obj)
    return obj


def describe_key(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        return "unset"
    prefix = key_prefix(value)
    if prefix:
        return f"set prefix={prefix}"
    return "set prefix=unrecognized"


def parse_env_file(path: Path) -> dict[str, bool]:
    """Name → whether the value is non-empty. Values do not leave this function."""
    found: dict[str, bool] = {}
    if not path.is_file():
        return found
    for line in path.read_text(errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, raw = stripped.partition("=")
        name = name.strip()
        if not name or name.startswith("#"):
            continue
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        found[name] = bool(value.strip())
    return found


def file_state(name: str, root_map: dict[str, bool], pkg_map: dict[str, bool]) -> str:
    parts = []
    if name in root_map:
        parts.append("root:" + ("set" if root_map[name] else "empty"))
    if name in pkg_map:
        parts.append("billproof:" + ("set" if pkg_map[name] else "empty"))
    return ",".join(parts) if parts else "absent"


def plain_resolved(name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    if not value:
        return default or "unset"
    return scrub(value)


def join_api(base: str, suffix: str) -> str:
    """suffix is 'models' or 'documents/extract', with no leading slash."""
    root = (base or "").rstrip("/")
    tail = suffix.lstrip("/")
    if root.endswith("/v1") or root.endswith("/api/v1"):
        return f"{root}/{tail}"
    return f"{root}/v1/{tail}"


def chat_url(base: str) -> str:
    root = (base or "").rstrip("/")
    if not root:
        return ""
    if root.endswith("/chat/completions"):
        return root
    if root.endswith("/v1"):
        return root + "/chat/completions"
    return root + "/v1/chat/completions"


def parse_model_ids(payload: object) -> list[str]:
    if isinstance(payload, list):
        items: object = payload
    elif isinstance(payload, dict):
        items = payload.get("data") or payload.get("models") or payload.get("items") or []
    else:
        items = []
    if isinstance(items, dict):
        items = list(items.values())
    if not isinstance(items, list):
        return []
    ids: list[str] = []
    seen: set[str] = set()
    for item in items:
        ident = item if isinstance(item, str) else None
        if isinstance(item, dict):
            raw = item.get("id") or item.get("name") or item.get("model")
            ident = raw if isinstance(raw, str) else None
        if ident and ident not in seen:
            seen.add(ident)
            ids.append(ident)
    return ids


def recommend_model(ids: list[str]) -> str:
    if MODEL_FINANCE in ids:
        return MODEL_FINANCE
    return MODEL_MAX


def explicit_drex_model() -> str:
    return os.getenv("DREX_MODEL", "").strip()


def retry_after_seconds(resp: httpx.Response, attempt: int) -> float:
    """Prefer retry-after-ms (milliseconds), then Retry-After (seconds or HTTP-date)."""
    ms = resp.headers.get("retry-after-ms")
    if ms is not None and str(ms).strip():
        try:
            return _cap_delay(float(ms) / 1000.0)
        except ValueError:
            pass
    retry_after = resp.headers.get("retry-after")
    if retry_after is not None and str(retry_after).strip():
        try:
            return _cap_delay(float(retry_after))
        except ValueError:
            try:
                when = parsedate_to_datetime(retry_after)
            except (TypeError, ValueError, OverflowError):
                when = None
            if when is not None:
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                return _cap_delay((when - datetime.now(timezone.utc)).total_seconds())
    body = _json_or_none(resp)
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        for key in ("retry_after_ms", "retry-after-ms"):
            raw = err.get(key, body.get(key)) if isinstance(err, dict) else body.get(key)
            if raw is None:
                continue
            try:
                return _cap_delay(float(raw) / 1000.0)
            except (TypeError, ValueError):
                pass
    return _cap_delay(0.5 * (2**attempt))


def _cap_delay(seconds: float) -> float:
    if seconds < 0:
        return 0.0
    return min(30.0, seconds)


def _json_or_none(resp: httpx.Response) -> object:
    try:
        return resp.json()
    except Exception:
        return None


def error_detail(resp: httpx.Response) -> str:
    body = _json_or_none(resp)
    text = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            text = f"{err.get('type') or ''} {err.get('message') or ''}".strip()
        elif isinstance(err, str):
            text = err
        elif body.get("message"):
            text = str(body.get("message"))
    if not text:
        text = (resp.text or "")[:240]
    return scrub(f"HTTP {resp.status_code} {text}".strip())[:300]


def send(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: object | None = None,
    files: list | None = None,
    timeout: float = 30.0,
) -> httpx.Response:
    """Retry 429 and 529 using retry-after-ms, then Retry-After. Also retry transport drops."""
    net_retries = 0
    attempt = 0
    while True:
        attempt += 1
        try:
            kwargs: dict = {"headers": headers, "timeout": timeout}
            if json_body is not None:
                kwargs["json"] = json_body
            if files is not None:
                kwargs["files"] = files
            resp = client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            net_retries += 1
            if net_retries >= 3:
                raise
            delay = _cap_delay(0.5 * (2 ** (net_retries - 1)))
            print(
                f"  transport {type(exc).__name__}; retry in {delay:.2f}s",
                flush=True,
            )
            time.sleep(delay)
            continue
        if resp.status_code in (429, 529) and attempt < 5:
            delay = retry_after_seconds(resp, attempt - 1)
            which = "retry-after-ms" if resp.headers.get("retry-after-ms") else "Retry-After"
            if not resp.headers.get("retry-after-ms") and not resp.headers.get("retry-after"):
                which = "backoff"
            print(
                f"  HTTP {resp.status_code}; waiting {delay:.2f}s ({which}), attempt {attempt}",
                flush=True,
            )
            time.sleep(delay)
            continue
        return resp


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    cleaned = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower == "authorization":
            token = value.split(" ", 1)[1] if " " in value else value
            scheme = value.split(" ", 1)[0] if " " in value else "Bearer"
            cleaned[key] = f"{scheme} {redact_secret(token)}"
        elif lower in ("x-api-key", "x-upload-token"):
            cleaned[key] = redact_secret(value)
        else:
            cleaned[key] = value
    return cleaned


def show_call(
    method: str,
    url: str,
    headers: dict[str, str],
    json_body: object | None = None,
    multipart: str | None = None,
) -> None:
    lines = [f"{method} {url}"]
    for key, value in redact_headers(headers).items():
        lines.append(f"{key}: {value}")
    if json_body is not None:
        lines.append(json.dumps(json_body, indent=2))
    if multipart:
        lines.append(multipart)
    print(guard("\n".join(lines) + "\n"), flush=True)


def auth_headers(key: str, *, direct: bool, json_body: bool) -> dict[str, str]:
    headers = {"Accept": "application/json", "X-Nace-Client": CLIENT_NAME}
    if json_body:
        headers["Content-Type"] = "application/json"
    if direct:
        headers["X-API-Key"] = key
    else:
        headers["Authorization"] = f"Bearer {key}" if key else "Bearer "
    return headers


def find_first(obj: object, key: str, depth: int = 0) -> object:
    if depth > 8:
        return None
    if isinstance(obj, dict):
        if key in obj and obj[key] not in (None, "", [], {}):
            return obj[key]
        for value in obj.values():
            found = find_first(value, key, depth + 1)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(obj, list):
        for value in obj[:40]:
            found = find_first(value, key, depth + 1)
            if found not in (None, "", [], {}):
                return found
    return None


def top_keys(obj: object) -> str:
    if isinstance(obj, dict):
        return ",".join(list(obj)[:12]) or "(empty)"
    return type(obj).__name__


def job_status(obj: object) -> str:
    if not isinstance(obj, dict):
        return ""
    nodes = [obj]
    job = obj.get("job")
    if isinstance(job, dict):
        nodes.append(job)
    for node in nodes:
        status = node.get("status")
        if isinstance(status, str) and status.strip():
            return status.strip().lower()
    return ""


def job_identifier(obj: object) -> str | None:
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("job_id"), str) and obj["job_id"]:
        return obj["job_id"]
    found = find_first(obj, "job_id")
    if isinstance(found, str) and found:
        return found
    ident = obj.get("id")
    if isinstance(ident, str) and ident.startswith("job"):
        return ident
    return None


def extract_fields(obj: object) -> list:
    if not isinstance(obj, dict):
        return []
    result = obj.get("result")
    if isinstance(result, dict) and isinstance(result.get("fields"), list):
        return result["fields"]
    if isinstance(obj.get("fields"), list):
        return obj["fields"]
    found = find_first(obj, "fields")
    return found if isinstance(found, list) else []


def summarize_citations(fields: list, limit: int = 3) -> list[str]:
    lines: list[str] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        path = field.get("path") or "?"
        status = field.get("status") or "?"
        cites = field.get("citations") if isinstance(field.get("citations"), list) else []
        if not cites:
            lines.append(f"{path} status={status} citations=0")
        for cite in cites[:2]:
            loc = cite.get("location") if isinstance(cite, dict) else None
            loc = loc if isinstance(loc, dict) else {}
            polys = loc.get("polygons") if isinstance(loc.get("polygons"), list) else []
            text = ""
            if isinstance(cite, dict) and isinstance(cite.get("source_text"), str):
                text = cite["source_text"].replace("\n", " ")[:80]
            lines.append(
                f"{path} status={status} page={loc.get('page')} "
                f"polygons={len(polys)} text={text!r}"
            )
            if len(lines) >= limit:
                return lines
        if len(lines) >= limit:
            break
    return lines


def first_citation(fields: list) -> tuple[str, int | None, object]:
    for field in fields:
        if not isinstance(field, dict):
            continue
        cites = field.get("citations") if isinstance(field.get("citations"), list) else []
        for cite in cites:
            if not isinstance(cite, dict):
                continue
            loc = cite.get("location") if isinstance(cite.get("location"), dict) else {}
            text = cite.get("source_text") if isinstance(cite.get("source_text"), str) else ""
            page = loc.get("page") if isinstance(loc.get("page"), int) else None
            polys = loc.get("polygons")
            if text.strip() or polys:
                return text.strip()[:400], page, polys
    return "", None, None


def harvest_crop_urls(obj: object, *, job_id: str, base: str) -> list[str]:
    urls: list[str] = []
    refs: list[str] = []

    def walk(node: object, trail: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, trail + (str(key),))
        elif isinstance(node, list):
            for value in node[:80]:
                walk(value, trail)
        elif isinstance(node, str) and node:
            leaf = trail[-1].lower() if trail else ""
            trail_s = "/".join(trail).lower()
            if node.startswith(("http://", "https://")):
                path = urlsplit(node).path.lower()
                if any(token in trail_s or token in path for token in ("crop", "preview")):
                    urls.append(node)
            elif leaf in {"ref", "preview_ref", "crop_ref", "preview_id", "crop_id"}:
                if len(node) <= 200 and " " not in node:
                    refs.append(node)

    walk(obj, ())
    for ref in refs:
        if ref.startswith(("http://", "https://")):
            urls.append(ref)
        elif job_id:
            urls.append(join_api(base, f"jobs/{job_id}/ground-crops/{ref}"))
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


def polygon_points(polygons: object) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []

    def add(x: object, y: object) -> None:
        try:
            points.append((float(x), float(y)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return

    def consume(node: object) -> None:
        if isinstance(node, dict) and "x" in node and "y" in node:
            add(node["x"], node["y"])
        elif isinstance(node, (list, tuple)):
            if len(node) == 2 and all(isinstance(item, (int, float)) for item in node):
                add(node[0], node[1])
            else:
                for item in node:
                    consume(item)

    consume(polygons)
    return points


def bbox_pixels(
    points: list[tuple[float, float]], page_w: float, page_h: float, scale: float
) -> tuple[int, int, int, int] | None:
    if len(points) < 2 or page_w <= 0 or page_h <= 0:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    coords = xs + ys
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    # NDI page_region polygons and our fixtures are top-left origin.
    if max(coords) <= 1.5 and min(coords) >= -0.01:
        left, right = minx * page_w, maxx * page_w
        top, bottom = miny * page_h, maxy * page_h
    elif max(xs) <= page_w * 1.05 and max(ys) <= page_h * 1.05 and min(coords) >= -1:
        left, right, top, bottom = minx, maxx, miny, maxy
    else:
        return None
    pad = 4 / scale
    left, right = max(0.0, left - pad), min(page_w, right + pad)
    top, bottom = max(0.0, top - pad), min(page_h, bottom + pad)
    if right - left < 1 or bottom - top < 1:
        return None
    if (right - left) > 0.98 * page_w and (bottom - top) > 0.98 * page_h:
        return None
    return (
        int(left * scale),
        int(top * scale),
        int(right * scale),
        int(bottom * scale),
    )


def cache_polygon_crop(pdf_path: Path, fields: list, dest: Path) -> Path | None:
    _text, page, polygons = first_citation(fields)
    if not page or polygons is None:
        return None
    points = polygon_points(polygons)
    if len(points) < 2:
        return None
    try:
        import pypdfium2 as pdfium
    except Exception:
        return None
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        if page < 1 or page > len(doc):
            return None
        pdf_page = doc[page - 1]
        page_w, page_h = pdf_page.get_size()
        box = bbox_pixels(points, page_w, page_h, scale=2)
        if box is None:
            return None
        bitmap = pdf_page.render(scale=2)
        try:
            image = bitmap.to_pil()
            width, height = image.size
            left, top, right, bottom = box
            left, top = max(0, left), max(0, top)
            right, bottom = min(width, right), min(height, bottom)
            if right - left < 2 or bottom - top < 2:
                return None
            dest.parent.mkdir(parents=True, exist_ok=True)
            image.crop((left, top, right, bottom)).save(dest)
        finally:
            bitmap.close()
            pdf_page.close()
    finally:
        doc.close()
    return dest


def ndi_target() -> NdiTarget | None:
    cfg = settings()
    ndi = cfg.ndi_api_key
    nace = cfg.nace_api_key
    ndi_base = cfg.ndi_base_url or NDI_DEFAULT
    nace_base = cfg.nace_base_url or NACE_DEFAULT
    if key_prefix(ndi) == "ndi_sk_":
        return NdiTarget(ndi_base, ndi, True, "direct NDI (ndi_sk_)")
    if key_prefix(nace) == "ndi_sk_" and not ndi:
        return NdiTarget(ndi_base, nace, True, "NACE_API_KEY is ndi_sk_; direct NDI host")
    if nace and key_prefix(nace) != "ndi_sk_":
        return NdiTarget(nace_base, nace, False, "console NDI via NACE_API_KEY")
    if key_prefix(ndi) == "nace_sk_":
        return NdiTarget(nace_base, ndi, False, "NDI_API_KEY is nace_sk_; console host")
    if ndi:
        return NdiTarget(ndi_base, ndi, True, "NDI_API_KEY set; direct NDI host")
    return None


def image_ext(content_type: str, data: bytes) -> str | None:
    kind = content_type.split(";")[0].strip().lower()
    if kind == "image/png" or data.startswith(b"\x89PNG"):
        return ".png"
    if kind in ("image/jpeg", "image/jpg") or data.startswith(b"\xff\xd8"):
        return ".jpg"
    if kind == "image/webp" or (data.startswith(b"RIFF") and b"WEBP" in data[:16]):
        return ".webp"
    if kind == "image/gif" or data.startswith(b"GIF8"):
        return ".gif"
    return None


def same_host(url: str, base: str) -> bool:
    return urlsplit(url).netloc == urlsplit(base).netloc and bool(urlsplit(url).netloc)


def download_crop(
    client: httpx.Client, url: str, api_headers_map: dict[str, str], base: str
) -> tuple[bytes, str]:
    headers = {"Accept": "image/*,*/*"}
    if same_host(url, base):
        for name in ("Authorization", "X-API-Key"):
            if api_headers_map.get(name):
                headers[name] = api_headers_map[name]
    resp = send(client, "GET", url, headers=headers, timeout=30)
    if resp.status_code in (301, 302, 303, 307, 308):
        location = resp.headers.get("location")
        if not location:
            raise RuntimeError(error_detail(resp))
        # Drop the API key on the second hop (often a signed object store).
        resp = send(
            client,
            "GET",
            urljoin(url, location),
            headers={"Accept": "image/*,*/*"},
            timeout=30,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"{safe_url(url)} {error_detail(resp)}")
    return resp.content, resp.headers.get("content-type", "")


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact_tree(payload), indent=2) + "\n")


def check_env(rows: list[Row], *, dry: bool) -> None:
    root_map = parse_env_file(REPO_ROOT / ".env")
    pkg_map = parse_env_file(BILLPROOF_ROOT / ".env")
    print("env names (values are not printed; repo-root .env loads first)", flush=True)
    missing = []
    for name, kind in _ENV_ROWS:
        where = file_state(name, root_map, pkg_map)
        if kind == "key":
            resolved = describe_key(name)
            if resolved == "unset":
                missing.append(name)
        else:
            default = ""
            if name == "NACE_BASE_URL":
                default = NACE_DEFAULT + " (default)"
            elif name == "NDI_BASE_URL":
                default = NDI_DEFAULT + " (default)"
            elif name == "DREX_MODEL":
                default = "unset (config default drex-max-finance)"
            elif name == "MOCK_MODE":
                default = "auto (default)"
            resolved = plain_resolved(name, default)
        print(f"  {name:<32} file={where:<22} {resolved}", flush=True)
    detail = "checked " + str(len(_ENV_ROWS)) + " names"
    if missing:
        detail += "; unset " + ",".join(missing)
    rows.append(Row("env", "DRY" if dry else "PASS", detail))
    mode = os.getenv("MOCK_MODE", "").strip() or "auto"
    if mode == "1":
        rows.append(
            Row(
                "mock-mode",
                "WARN",
                "MOCK_MODE=1 forces app simulators. Smoke still calls the network. "
                "Set MOCK_MODE=auto before run_bench.",
            )
        )


def drex_headers(key: str) -> dict[str, str]:
    return auth_headers(key, direct=False, json_body=False)


def check_drex(client: httpx.Client, rows: list[Row], *, dry: bool) -> None:
    cfg = settings()
    key = cfg.nace_api_key
    base = cfg.nace_base_url or NACE_DEFAULT
    models_url = join_api(base, "models")
    headers = drex_headers(key)
    headers_json = auth_headers(key, direct=False, json_body=True)
    explicit = explicit_drex_model()
    placeholder_model = explicit or MODEL_FINANCE
    system_body = {"model": placeholder_model, "state": DREX_STATE, "questions": DREX_QUESTIONS}
    system_url = join_api(base, "systemone")

    if dry:
        print("--- drex: GET /v1/models ---", flush=True)
        show_call("GET", models_url, headers)
        print("--- drex: POST /v1/systemone (noul + choice) ---", flush=True)
        if not explicit:
            print(
                "note: DREX_MODEL unset. Live run replaces model with "
                f"{MODEL_FINANCE} if GET /v1/models lists it, else {MODEL_MAX}.",
                flush=True,
            )
        show_call("POST", system_url, headers_json, system_body)
        rows.append(Row("drex-models", "DRY", "would GET " + safe_url(models_url)))
        rows.append(
            Row(
                "drex-call",
                "DRY",
                f"would POST systemone model={placeholder_model}; assert response.model",
            )
        )
        return

    if not key:
        rows.append(Row("drex-models", "FAIL", "NACE_API_KEY unset"))
        rows.append(Row("drex-call", "FAIL", "not sent; NACE_API_KEY unset"))
        return
    if key_prefix(key) == "ndi_sk_":
        rows.append(
            Row(
                "drex-models",
                "FAIL",
                "NACE_API_KEY prefix is ndi_sk_; console Bearer expects nace_sk_",
            )
        )
        rows.append(Row("drex-call", "FAIL", "not sent; key is an NDI direct key"))
        return

    print(f"GET {safe_url(models_url)}", flush=True)
    try:
        listed = send(client, "GET", models_url, headers=headers, timeout=30)
    except httpx.TransportError as exc:
        rows.append(Row("drex-models", "FAIL", scrub(f"{type(exc).__name__}: {exc}")[:200]))
        rows.append(Row("drex-call", "FAIL", "not sent; models request did not connect"))
        return
    if listed.status_code in (401, 403):
        rows.append(Row("drex-models", "FAIL", error_detail(listed)))
        rows.append(Row("drex-call", "FAIL", "not sent; models auth failed"))
        return
    if listed.status_code >= 400:
        rows.append(Row("drex-models", "FAIL", error_detail(listed)))
        ids: list[str] = []
        recommended = MODEL_MAX
    else:
        payload = _json_or_none(listed)
        ids = parse_model_ids(payload)
        recommended = recommend_model(ids)
        shown = ", ".join(ids) if ids else "(none parsed)"
        print(guard(f"  models: {shown}"), flush=True)
        print(guard(f"  recommend DREX_MODEL={recommended}"), flush=True)
        rows.append(
            Row(
                "drex-models",
                "PASS",
                f"{len(ids)} ids; recommend {recommended}",
            )
        )
        if ids and recommended not in ids:
            rows.append(
                Row(
                    "drex-model",
                    "WARN",
                    f"{recommended} was not in the catalog; pin an id from the list above",
                )
            )

    chosen = explicit or recommended
    if not explicit:
        rows.append(
            Row(
                "drex-model",
                "WARN",
                f"DREX_MODEL unset; this call uses {chosen}. "
                f"Write DREX_MODEL={chosen} into .env before run_bench "
                "(config default is drex-max-finance).",
            )
        )
    elif explicit != recommended:
        extra = "" if not ids or explicit in ids else "; id not in catalog"
        rows.append(
            Row(
                "drex-model",
                "WARN",
                f"DREX_MODEL={explicit}; catalog recommends {recommended}{extra}",
            )
        )
    elif ids and explicit not in ids:
        rows.append(Row("drex-model", "WARN", f"DREX_MODEL={explicit} is not in GET /v1/models"))

    system_body = {"model": chosen, "state": DREX_STATE, "questions": DREX_QUESTIONS}
    print(f"POST {safe_url(system_url)} model={chosen}", flush=True)
    started = time.perf_counter()
    try:
        resp = send(
            client, "POST", system_url, headers=headers_json, json_body=system_body, timeout=70
        )
    except httpx.TransportError as exc:
        rows.append(Row("drex-call", "FAIL", scrub(f"{type(exc).__name__}: {exc}")[:200]))
        return
    client_ms = (time.perf_counter() - started) * 1000
    if resp.status_code >= 400:
        rows.append(Row("drex-call", "FAIL", error_detail(resp)))
        return
    body = _json_or_none(resp)
    if not isinstance(body, dict):
        rows.append(Row("drex-call", "FAIL", "response was not a JSON object"))
        return
    served = body.get("model")
    answers = body.get("answers") if isinstance(body.get("answers"), dict) else {}
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    server_ms = body.get("evaluation_time_ms", body.get("latency_ms"))
    dup = answers.get("duplicate") if isinstance(answers, dict) else None
    action = answers.get("action") if isinstance(answers, dict) else None
    print(guard(f"  response.model={served!r} sent={chosen!r}"), flush=True)
    print(guard(f"  {_fmt_answer('duplicate', dup)}"), flush=True)
    print(guard(f"  {_fmt_answer('action', action)}"), flush=True)
    print(
        guard(
            f"  latency server={server_ms} client_ms={client_ms:.0f} "
            f"usage in={usage.get('input_tokens')} out={usage.get('output_tokens')} "
            f"request_id={body.get('request_id')}"
        ),
        flush=True,
    )
    problems = []
    if served != chosen:
        problems.append(f"response.model={served!r} != DREX_MODEL {chosen!r}")
    noul_value = dup.get("noul") if isinstance(dup, dict) else None
    if isinstance(noul_value, bool) or not isinstance(noul_value, (int, float)):
        problems.append("duplicate answer missing noul")
    elif not 0 <= float(noul_value) <= 1:
        problems.append("duplicate noul out of range")
    if not isinstance(action, dict) or action.get("choice") not in DREX_QUESTIONS["action"]["criteria"]:
        problems.append("action choice missing or outside criteria")
    probs = action.get("probabilities") if isinstance(action, dict) else None
    if not isinstance(probs, dict):
        problems.append("action probabilities missing")
    if problems:
        rows.append(Row("drex-call", "FAIL", "; ".join(problems)))
        return
    noul = float(noul_value)  # type: ignore[arg-type]
    rows.append(
        Row(
            "drex-call",
            "PASS",
            f"model={served} duplicate={noul:.4f} action={action.get('choice')} "
            f"server_ms={server_ms} client_ms={client_ms:.0f} "
            f"in={usage.get('input_tokens')} out={usage.get('output_tokens')}",
        )
    )


def _fmt_answer(name: str, answer: object) -> str:
    if not isinstance(answer, dict):
        return f"{name}: missing"
    if "noul" in answer:
        return f"{name} noul={answer.get('noul')} confidence={answer.get('confidence')}"
    return (
        f"{name} choice={answer.get('choice')} "
        f"probabilities={answer.get('probabilities')} confidence={answer.get('confidence')}"
    )


def post_job(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    body: dict,
    *,
    base: str,
    auth: dict[str, str],
) -> dict:
    resp = send(client, "POST", url, headers=headers, json_body=body, timeout=90)
    if resp.status_code >= 400:
        raise RuntimeError(error_detail(resp))
    payload = _json_or_none(resp)
    if not isinstance(payload, dict):
        raise RuntimeError("job create response was not a JSON object")
    status = job_status(payload)
    if status in _OK_JOB or status in _BAD_JOB or extract_fields(payload):
        if status in _BAD_JOB:
            raise RuntimeError(f"job {status}: {scrub(json.dumps(redact_tree(payload))[:240])}")
        return payload
    job_id = job_identifier(payload)
    if not job_id:
        raise RuntimeError(f"no job_id; keys={top_keys(payload)}")
    return poll_job(client, base, auth, job_id)


def poll_job(client: httpx.Client, base: str, headers: dict[str, str], job_id: str) -> dict:
    url = join_api(base, f"documents/jobs/{job_id}")
    deadline = time.monotonic() + 180
    while True:
        resp = send(client, "GET", url, headers=headers, timeout=40)
        if resp.status_code >= 400:
            raise RuntimeError(error_detail(resp))
        payload = _json_or_none(resp)
        if not isinstance(payload, dict):
            raise RuntimeError("job poll response was not a JSON object")
        status = job_status(payload)
        print(f"  job {job_id} status={status or '?'}", flush=True)
        if status in _BAD_JOB:
            raise RuntimeError(f"job {status}: keys={top_keys(payload)}")
        if status in _OK_JOB or extract_fields(payload):
            return payload
        if time.monotonic() > deadline:
            raise RuntimeError(f"job {job_id} still {status or 'pending'} after 180s")
        time.sleep(2)


def grant_upload(body: object) -> tuple[str, str]:
    if not isinstance(body, dict):
        raise RuntimeError("upload-grant response was not a JSON object")
    upload_url = body.get("upload_url") if isinstance(body.get("upload_url"), str) else None
    token = body.get("token") if isinstance(body.get("token"), str) else None
    if not upload_url:
        found = find_first(body, "upload_url")
        upload_url = found if isinstance(found, str) else None
    if not token:
        for key in ("token", "upload_token"):
            found = body.get(key) if isinstance(body.get(key), str) else find_first(body, key)
            if isinstance(found, str):
                token = found
                break
    if not upload_url or not token:
        raise RuntimeError(f"grant missing upload_url or token; keys={top_keys(body)}")
    return upload_url, token


def workspace_ids(body: object) -> tuple[str, str]:
    if not isinstance(body, dict):
        raise RuntimeError("upload response was not a JSON object")
    file_obj = None
    result = body.get("result")
    if isinstance(result, dict) and isinstance(result.get("file"), dict):
        file_obj = result["file"]
    elif isinstance(body.get("file"), dict):
        file_obj = body["file"]
    workspace_id = file_obj.get("workspace_id") if isinstance(file_obj, dict) else None
    file_id = file_obj.get("file_id") if isinstance(file_obj, dict) else None
    if not isinstance(workspace_id, str):
        found = find_first(body, "workspace_id")
        workspace_id = found if isinstance(found, str) else None
    if not isinstance(file_id, str):
        found = find_first(body, "file_id")
        file_id = found if isinstance(found, str) else None
    if not workspace_id or not file_id:
        raise RuntimeError(f"upload missing workspace_id/file_id; keys={top_keys(body)}")
    return workspace_id, file_id


def source_body(workspace_id: str, file_id: str) -> dict:
    return {"type": "workspace_file", "workspace_id": workspace_id, "file_id": file_id}


def check_ndi(client: httpx.Client, rows: list[Row], *, dry: bool) -> None:
    target = ndi_target()
    pdf_note = f"{DEMO_PDF.relative_to(REPO_ROOT)} ({DEMO_PDF.stat().st_size} bytes)" if DEMO_PDF.is_file() else f"{DEMO_PDF.relative_to(REPO_ROOT)} MISSING"
    if dry:
        base = (target.base if target else NACE_DEFAULT)
        direct = bool(target and target.direct)
        key = target.key if target else ""
        headers = auth_headers(key, direct=direct, json_body=True)
        grant_headers = dict(headers)
        grant_headers["Idempotency-Key"] = "<uuid, reused on 429/529 retries>"
        print(f"--- ndi ({target.reason if target else 'no key'}) ---", flush=True)
        print(f"pdf: {pdf_note}", flush=True)
        show_call(
            "POST",
            join_api(base, "documents/upload-grants"),
            grant_headers,
            {
                "path": "billproof-smoke/SYN-D01_bill.pdf",
                "total_size_bytes": DEMO_PDF.stat().st_size if DEMO_PDF.is_file() else 0,
                "ttl_seconds": 600,
            },
        )
        print("--- ndi: multipart upload (no API key on this hop) ---", flush=True)
        show_call(
            "POST",
            "<upload_url from grant>",
            {"X-Upload-Token": "", "X-Nace-Client": CLIENT_NAME},
            multipart=(
                "multipart file=SYN-D01_bill.pdf application/pdf\n"
                'multipart metadata={"path":"billproof-smoke/SYN-D01_bill.pdf",'
                '"total_size_bytes":<n>} application/json'
            ),
        )
        extract_headers = dict(headers)
        extract_headers["Idempotency-Key"] = "<uuid, reused on 429/529 retries>"
        show_call(
            "POST",
            join_api(base, "documents/extract") + "?wait_seconds=60",
            extract_headers,
            {
                "source": source_body("<workspace_id>", "<file_id>"),
                "schema": EXTRACT_SCHEMA,
                "citations": {"enabled": True, "include_source_text": True},
            },
        )
        poll_headers = auth_headers(key, direct=direct, json_body=False)
        show_call("GET", join_api(base, "documents/jobs/<job_id>"), poll_headers)
        print(
            "--- ndi: ground include_previews (crop URLs expire ~21 min; cached under data/cache/) ---",
            flush=True,
        )
        ground_headers = dict(headers)
        ground_headers["Idempotency-Key"] = "<uuid, reused on 429/529 retries>"
        show_call(
            "POST",
            join_api(base, "documents/ground") + "?wait_seconds=60",
            ground_headers,
            {
                "source": source_body("<workspace_id>", "<file_id>"),
                "targets": [{"id": "cite-0", "text": "<first citation source_text, else Total>"}],
                "options": {"max_matches": 2, "include_previews": True},
            },
        )
        crop_headers = {"Accept": "image/*,*/*"}
        if direct:
            crop_headers["X-API-Key"] = key
        else:
            crop_headers["Authorization"] = f"Bearer {key}" if key else "Bearer "
        show_call(
            "GET",
            join_api(base, "jobs/<ground_job_id>/ground-crops/<ref>"),
            crop_headers,
        )
        print(
            "note: crop auth is sent only when the URL host matches the API host. "
            "Redirects are followed without the API key.",
            flush=True,
        )
        rows.append(Row("ndi-extract", "DRY", "would upload, extract, poll; " + pdf_note))
        rows.append(
            Row("ndi-crops", "DRY", f"would cache previews under {CROP_DIR.relative_to(REPO_ROOT)}")
        )
        return

    if not DEMO_PDF.is_file():
        rows.append(Row("ndi-extract", "SKIP", f"missing {DEMO_PDF.relative_to(REPO_ROOT)}"))
        rows.append(Row("ndi-crops", "SKIP", "pdf not present"))
        return
    if target is None:
        rows.append(Row("ndi-extract", "FAIL", "no NACE_API_KEY or NDI_API_KEY"))
        rows.append(Row("ndi-crops", "FAIL", "not sent; no document key"))
        return

    print(f"ndi via {target.reason} base={safe_url(target.base)}", flush=True)
    headers = auth_headers(target.key, direct=target.direct, json_body=True)
    read_headers = auth_headers(target.key, direct=target.direct, json_body=False)
    pdf_bytes = DEMO_PDF.read_bytes()
    remote_path = "billproof-smoke/SYN-D01_bill.pdf"
    try:
        workspace_id, file_id = upload_pdf(client, target, headers, pdf_bytes, remote_path)
        extract_url = join_api(target.base, "documents/extract") + "?wait_seconds=60"
        extract_headers = dict(headers)
        extract_headers["Idempotency-Key"] = str(uuid.uuid4())
        print(f"POST {safe_url(extract_url)}", flush=True)
        job = post_job(
            client,
            extract_url,
            extract_headers,
            {
                "source": source_body(workspace_id, file_id),
                "schema": EXTRACT_SCHEMA,
                "citations": {"enabled": True, "include_source_text": True},
            },
            base=target.base,
            auth=read_headers,
        )
    except (httpx.TransportError, RuntimeError) as exc:
        rows.append(Row("ndi-extract", "FAIL", scrub(str(exc))[:300]))
        rows.append(Row("ndi-crops", "FAIL", "not sent; extract did not succeed"))
        return

    fields = extract_fields(job)
    lines = summarize_citations(fields)
    print("  citations:", flush=True)
    if lines:
        for line in lines:
            print(f"    {guard(line)}", flush=True)
    else:
        print("    (none)", flush=True)
    result = job.get("result") if isinstance(job.get("result"), dict) else job
    data = result.get("data") if isinstance(result, dict) else None
    if data is not None:
        print(f"  data={guard(json.dumps(redact_tree(data))[:300])}", flush=True)
    try:
        write_json(CROP_DIR / "extract.json", job)
    except OSError as exc:
        print(f"  could not write extract.json ({exc})", flush=True)
    rows.append(
        Row(
            "ndi-extract",
            "PASS",
            f"job={job_identifier(job) or '?'} fields={len(fields)} "
            + (lines[0] if lines else "no citations"),
        )
    )

    text, page, _polys = first_citation(fields)
    target_text = text or "Total"
    ground_target: dict = {"id": "cite-0", "text": target_text}
    if page:
        ground_target["page_hints"] = [page]
    ground_url = join_api(target.base, "documents/ground") + "?wait_seconds=60"
    ground_headers = dict(headers)
    ground_headers["Idempotency-Key"] = str(uuid.uuid4())
    print(f"POST {safe_url(ground_url)} include_previews", flush=True)
    try:
        ground = post_job(
            client,
            ground_url,
            ground_headers,
            {
                "source": source_body(workspace_id, file_id),
                "targets": [ground_target],
                "options": {"max_matches": 2, "include_previews": True},
            },
            base=target.base,
            auth=read_headers,
        )
    except (httpx.TransportError, RuntimeError) as exc:
        saved = _try_local_crop(fields)
        detail = scrub(str(exc))[:220]
        if saved:
            detail += f"; local polygon crop {saved.relative_to(REPO_ROOT)}"
        rows.append(Row("ndi-crops", "FAIL", detail))
        return

    try:
        write_json(CROP_DIR / "ground.json", ground)
    except OSError:
        pass
    ground_id = job_identifier(ground) or ""
    crop_urls = harvest_crop_urls(ground, job_id=ground_id, base=target.base)
    saved_remote: list[Path] = []
    for index, crop_url in enumerate(crop_urls[:3]):
        print(f"  GET crop {safe_url(crop_url)}", flush=True)
        try:
            data_bytes, content_type = download_crop(client, crop_url, read_headers, target.base)
        except (httpx.TransportError, RuntimeError) as exc:
            print(f"    {scrub(str(exc))[:200]}", flush=True)
            continue
        ext = image_ext(content_type, data_bytes)
        if ext is None:
            print("    response was not an image", flush=True)
            continue
        dest = CROP_DIR / f"crop-{index}{ext}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data_bytes)
        saved_remote.append(dest)
        print(f"    cached {dest.relative_to(REPO_ROOT)} ({len(data_bytes)} bytes)", flush=True)
    if saved_remote:
        rows.append(
            Row(
                "ndi-crops",
                "PASS",
                f"{len(saved_remote)} preview(s) -> {saved_remote[0].relative_to(REPO_ROOT)}",
            )
        )
        return
    local = _try_local_crop(fields)
    if local:
        rows.append(
            Row(
                "ndi-crops",
                "PASS",
                "no preview URL; cached citation polygon crop "
                + str(local.relative_to(REPO_ROOT)),
            )
        )
        return
    rows.append(
        Row(
            "ndi-crops",
            "FAIL",
            f"ground job ok but no crop cached; urls={len(crop_urls)} "
            f"keys={top_keys(ground)}",
        )
    )


def _try_local_crop(fields: list) -> Path | None:
    dest = CROP_DIR / "polygon-0.png"
    try:
        return cache_polygon_crop(DEMO_PDF, fields, dest)
    except Exception as exc:
        print(f"  local polygon crop skipped ({type(exc).__name__})", flush=True)
        return None


def upload_pdf(
    client: httpx.Client,
    target: NdiTarget,
    headers: dict[str, str],
    pdf_bytes: bytes,
    remote_path: str,
) -> tuple[str, str]:
    grant_url = join_api(target.base, "documents/upload-grants")
    grant_headers = dict(headers)
    grant_headers["Idempotency-Key"] = str(uuid.uuid4())
    body = {"path": remote_path, "total_size_bytes": len(pdf_bytes), "ttl_seconds": 600}
    print(f"POST {safe_url(grant_url)}", flush=True)
    resp = send(client, "POST", grant_url, headers=grant_headers, json_body=body, timeout=30)
    if resp.status_code == 409:
        remote_path = f"billproof-smoke/{uuid.uuid4().hex[:8]}/SYN-D01_bill.pdf"
        body = {"path": remote_path, "total_size_bytes": len(pdf_bytes), "ttl_seconds": 600}
        grant_headers["Idempotency-Key"] = str(uuid.uuid4())
        print("  path conflict; retrying grant with a new path", flush=True)
        resp = send(client, "POST", grant_url, headers=grant_headers, json_body=body, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(error_detail(resp))
    upload_url, token = grant_upload(_json_or_none(resp))
    if upload_url.startswith("/"):
        upload_url = urljoin(target.base.rstrip("/") + "/", upload_url.lstrip("/"))
    print(f"POST upload {safe_url(upload_url)} ({len(pdf_bytes)} bytes, no API key)", flush=True)
    up_headers = {"X-Upload-Token": token, "X-Nace-Client": CLIENT_NAME}
    files = [
        ("file", ("SYN-D01_bill.pdf", pdf_bytes, "application/pdf")),
        (
            "metadata",
            (
                None,
                json.dumps({"path": remote_path, "total_size_bytes": len(pdf_bytes)}),
                "application/json",
            ),
        ),
    ]
    uploaded = send(client, "POST", upload_url, headers=up_headers, files=files, timeout=60)
    if uploaded.status_code >= 400:
        raise RuntimeError("upload " + error_detail(uploaded))
    return workspace_ids(_json_or_none(uploaded))


def frontier_body(*, strict: bool, model: str) -> dict:
    body: dict = {"model": model, "messages": FRONTIER_MESSAGES}
    if strict:
        body["temperature"] = 0
        body["max_tokens"] = 120
        body["response_format"] = {"type": "json_object"}
    return body


def parse_json_content(content: object) -> dict:
    if isinstance(content, dict):
        return content
    text = str(content).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("frontier content JSON was not an object")
    return parsed


def check_frontier(client: httpx.Client, rows: list[Row], *, dry: bool) -> None:
    cfg = settings()
    url = chat_url(cfg.frontier_base_url)
    model = cfg.frontier_model
    key = cfg.frontier_api_key
    headers = {
        "Authorization": f"Bearer {key}" if key else "Bearer ",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = frontier_body(strict=True, model=model or "<FRONTIER_MODEL unset>")
    if dry:
        print("--- frontier: POST chat/completions JSON ---", flush=True)
        show_call("POST", url or "<FRONTIER_BASE_URL unset>/v1/chat/completions", headers, body)
        print(
            "note: on HTTP 400 mentioning response_format, max_tokens, or temperature, "
            "one compatibility retry is sent with model+messages only.",
            flush=True,
        )
        rows.append(Row("frontier", "DRY", "would POST chat/completions json_object"))
        return
    missing = [
        name
        for name, present in (
            ("FRONTIER_BASE_URL", bool(cfg.frontier_base_url)),
            ("FRONTIER_API_KEY", bool(key)),
            ("FRONTIER_MODEL", bool(model)),
        )
        if not present
    ]
    if missing:
        rows.append(Row("frontier", "FAIL", "unset " + ",".join(missing)))
        return
    print(f"POST {safe_url(url)} model={model}", flush=True)
    started = time.perf_counter()
    try:
        resp = send(client, "POST", url, headers=headers, json_body=body, timeout=60)
        if resp.status_code == 400 and _needs_looser_body(resp):
            print("  router rejected json mode; retrying with model+messages only", flush=True)
            resp = send(
                client,
                "POST",
                url,
                headers=headers,
                json_body=frontier_body(strict=False, model=model),
                timeout=60,
            )
    except httpx.TransportError as exc:
        rows.append(Row("frontier", "FAIL", scrub(f"{type(exc).__name__}: {exc}")[:200]))
        return
    client_ms = (time.perf_counter() - started) * 1000
    if resp.status_code >= 400:
        rows.append(Row("frontier", "FAIL", error_detail(resp)))
        return
    payload = _json_or_none(resp)
    if not isinstance(payload, dict):
        rows.append(Row("frontier", "FAIL", "response was not a JSON object"))
        return
    try:
        content = payload["choices"][0]["message"]["content"]
        parsed = parse_json_content(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
        rows.append(Row("frontier", "FAIL", scrub(f"JSON content: {exc}")[:200]))
        return
    duplicate = str(parsed.get("duplicate", "")).strip().lower()
    confidence = parsed.get("confidence")
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    print(
        f"  duplicate={duplicate!r} self-reported confidence={confidence} "
        f"client_ms={client_ms:.0f} usage={usage.get('prompt_tokens') or usage.get('input_tokens')}"
        f"/{usage.get('completion_tokens') or usage.get('output_tokens')}",
        flush=True,
    )
    if duplicate not in ("yes", "no"):
        rows.append(Row("frontier", "FAIL", f"duplicate={duplicate!r} is not yes/no"))
        return
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        rows.append(Row("frontier", "FAIL", "confidence was not a number"))
        return
    if not 0 <= float(confidence) <= 1:
        rows.append(Row("frontier", "FAIL", "confidence out of range 0..1"))
        return
    served = payload.get("model")
    note = ""
    if isinstance(served, str) and served and served != model:
        note = f" response.model={served}"
        rows.append(
            Row(
                "frontier-model",
                "WARN",
                f"sent {model}; router reported {served}",
            )
        )
    rows.append(
        Row(
            "frontier",
            "PASS",
            f"duplicate={duplicate} self-reported confidence={float(confidence):.3f} "
            f"client_ms={client_ms:.0f}{note}",
        )
    )


def _needs_looser_body(resp: httpx.Response) -> bool:
    text = error_detail(resp).lower()
    return any(token in text for token in ("response_format", "max_tokens", "temperature", "json_object"))


def print_table(rows: list[Row]) -> None:
    print()
    print(f"{'check':<16} {'status':<6} detail")
    print("-" * 78)
    counts: dict[str, int] = {}
    for row in rows:
        detail = guard(" ".join(row.detail.split()))
        print(f"{row.name:<16} {row.status:<6} {detail}")
        counts[row.status] = counts.get(row.status, 0) + 1
    summary = ", ".join(f"{counts[name]} {name}" for name in ("PASS", "FAIL", "SKIP", "WARN", "DRY") if counts.get(name))
    print(summary or "no checks")


def selected(only: str | None) -> set[str]:
    if only:
        return {only}
    return {"drex", "ndi", "frontier"}


def main(argv: list[str] | None = None) -> int:
    global SECRETS
    parser = argparse.ArgumentParser(description="Smoke-test live Drex, NDI, and frontier credentials.")
    parser.add_argument("--only", choices=("drex", "ndi", "frontier"), help="run one service")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the requests with keys redacted; do not call the network",
    )
    args = parser.parse_args(argv)
    SECRETS = load_secrets()
    want = selected(args.only)
    rows: list[Row] = []
    if args.dry_run:
        print("dry-run: no network; keys redacted", flush=True)
    check_env(rows, dry=args.dry_run)
    timeout = httpx.Timeout(70.0, connect=10.0)
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        if "drex" in want:
            check_drex(client, rows, dry=args.dry_run)
        if "ndi" in want:
            check_ndi(client, rows, dry=args.dry_run)
        if "frontier" in want:
            check_frontier(client, rows, dry=args.dry_run)
    print_table(rows)
    return 1 if any(row.status == "FAIL" for row in rows) else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        message = str(exc)
        if "credential" in message:
            print(message, file=sys.stderr)
            raise SystemExit(1) from None
        raise
