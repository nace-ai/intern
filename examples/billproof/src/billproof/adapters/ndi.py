"""NDI (Nace Document Intelligence) adapter.

Real client speaks the Console wire format: upload grant, then
``POST /v1/documents/{split,extract,ground}`` and poll ``GET /v1/documents/jobs/{id}``.
Credentials: ``NDI_API_KEY`` + ``NDI_BASE_URL`` when set (``X-API-Key``), otherwise
``NACE_API_KEY`` + ``NACE_BASE_URL`` (``Authorization: Bearer``).

With an empty ``.env``, ``get_ndi()`` returns the fixture adapter.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from billproof.config import CACHE_DIR, PKG_ROOT, SYN_DIR, settings
from billproof.schema import Citation

# Polygons in the docs are normalized 0–1. Values above 1 are page units
# (PDF points or pixels) and are scaled into that same top-left space.
_UNIT_MAX = 1.001
_LETTER = (612.0, 792.0)
_PAD_PX = 12
_RENDER_SCALE = 2

_INSTRUCTIONS = (
    "Read only what is printed. Money fields are integer cents (1234 means $12.34), never dollars. "
    "Dates are YYYY-MM-DD. Leave a field null when it is not on the page. "
    "Cite the exact printed region for every field you find. "
    "Each entry in `lines` is one printed service row: cite the row the entry was read from."
)

# Bump per doc kind when SCHEMAS or _INSTRUCTIONS change that kind's produced
# field shape; it salts the on-disk extract cache so stale fieldsets are never
# served as current (and untouched kinds keep their valid caches).
EXTRACT_VERSION: dict[str, str] = {"bill": "v4", "eob": "v3", "denial": "v3", "plan": "v3"}
_EXTRACT_VERSION_DEFAULT = "v3"


def _str(description: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string"}
    if description:
        schema["description"] = description
    return schema


def _int(description: str) -> dict[str, Any]:
    return {"type": "integer", "description": description}


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "properties": properties}


def _arr(item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": item}


_BILL_LINE = _obj(
    {
        "line_no": _int("1-based line number printed on the bill"),
        "dos": _str("Date of service YYYY-MM-DD"),
        "code": _str("CPT or HCPCS code as printed"),
        "modifiers": _arr(_str()),
        "label": _str(
            "Service description from the DESCRIPTION column only; "
            "never include the separate REF column text"
        ),
        "units": _int("Units billed"),
        "charge_cents": _int("Line charge in integer cents"),
        "pos": _str("Place of service code, such as 11 or 23"),
        "rendering_provider": _str("Rendering provider name as printed"),
    }
)

_EOB_LINE = _obj(
    {
        "line_no": _int("1-based line number, matching the bill line"),
        "code": _str("CPT or HCPCS code as printed"),
        "units": _int("Units on this EOB line"),
        "billed_cents": _int("Billed amount in integer cents"),
        "allowed_cents": _int("Allowed amount in integer cents"),
        "plan_paid_cents": _int("Amount the plan paid in integer cents"),
        "patient_resp_cents": _int("Patient responsibility for this line in integer cents"),
        "carc": _arr(_str("Claim adjustment reason code as printed, such as CO-97")),
        "remark": _str("Remark printed on this line"),
    }
)

SCHEMAS: dict[str, dict[str, Any]] = {
    "bill": _obj(
        {
            "patient": _obj(
                {
                    "patient_id": _str("Pseudonymous patient id as printed"),
                    "display_name": _str(
                        "Patient name — the word(s) printed after BILLED TO / PATIENT NAME"
                    ),
                    "dob": _str("Date of birth YYYY-MM-DD"),
                    "date_of_death": _str("Date of death YYYY-MM-DD, only if printed"),
                    "member_id": _str("Member id as printed"),
                }
            ),
            "provider": _obj(
                {
                    "name": _str("Facility or billing provider name"),
                    "npi": _str("National provider identifier as printed"),
                    "in_network": {"type": "boolean", "description": "True when the bill says in network"},
                    "address": _str("Provider address as printed"),
                }
            ),
            "bill": _obj(
                {
                    "account_no": _str("Account or statement number"),
                    "statement_date": _str("Statement date YYYY-MM-DD"),
                    "total_charges_cents": _int("Total charges in integer cents"),
                    "insurance_paid_cents": _int("Insurance payments in integer cents"),
                    "adjustments_cents": _int("Adjustments in integer cents"),
                    "patient_balance_cents": _int("Amount the bill asks the patient to pay, integer cents"),
                }
            ),
            # Top-level on purpose: NDI emits one field entry (with its own
            # row-level citation) per item of a top-level array.
            "lines": _arr(_BILL_LINE),
        }
    ),
    "eob": _obj(
        {
            "eob": _obj(
                {
                    "payer": _str("Insurer name as printed"),
                    "claim_id": _str("Claim id as printed"),
                    "processed_date": _str("Date the claim was processed YYYY-MM-DD"),
                    "patient_resp_total_cents": _int("Total patient responsibility in integer cents"),
                }
            ),
            "lines": _arr(_EOB_LINE),
        }
    ),
    "denial": _obj(
        {
            "denial": _obj(
                {
                    "letter_date": _str("Letter date YYYY-MM-DD"),
                    "carc": _str("Claim adjustment reason code as printed, such as CO-50"),
                    "category": {
                        "type": "string",
                        "enum": [
                            "MEDICAL_NECESSITY",
                            "PRIOR_AUTH",
                            "NOT_COVERED",
                            "OUT_OF_NETWORK",
                            "CODING_BUNDLING",
                            "DUPLICATE_CLAIM",
                            "TIMELY_FILING",
                            "ELIGIBILITY",
                            "MISSING_INFO",
                        ],
                        "description": "Denial family that best matches the letter",
                    },
                    "reason_text": _str("The denial reason paragraph as printed"),
                    "service_code": _str("Denied service code as printed"),
                    "appeal_deadline": _str("Appeal deadline YYYY-MM-DD"),
                    "plan_section": _str("Plan section cited by the letter"),
                }
            ),
        }
    ),
    "plan": _obj(
        {
            "plan": _obj(
                {
                    "plan_name": _str("Plan name as printed"),
                    "plan_type": {
                        "type": "string",
                        "enum": ["PPO", "HMO", "EPO", "HDHP"],
                    },
                    "deductible_cents": _int("Individual deductible in integer cents"),
                    "deductible_met_cents": _int("Deductible already met, integer cents"),
                    "coinsurance_pct": _int("Coinsurance percent as an integer, such as 20"),
                    "copay_cents": _int("Copay in integer cents"),
                    "oop_max_cents": _int("Individual out-of-pocket maximum in integer cents"),
                    "emergency_nsa_protected": {
                        "type": "boolean",
                        "description": "True when emergency care is protected from balance billing",
                    },
                }
            ),
        }
    ),
}

SPLIT_CLASSES: list[dict[str, str]] = [
    {
        "id": "bill",
        "label": "Itemized bill",
        "description": "A provider itemized medical bill listing service lines and charges.",
    },
    {
        "id": "eob",
        "label": "Explanation of benefits",
        "description": "An insurer explanation of benefits with allowed amounts and patient responsibility.",
    },
    {
        "id": "denial",
        "label": "Denial letter",
        "description": "A letter denying a claim and stating the reason and appeal rights.",
    },
    {
        "id": "plan",
        "label": "Plan summary",
        "description": "A summary of benefits: deductible, coinsurance, and out-of-pocket maximum.",
    },
]


def resolve_credentials() -> tuple[str, str, str]:
    """Return ``(base_url, api_key, auth)`` where auth is ``bearer`` or ``x-api-key``."""
    cfg = settings()
    if cfg.ndi_api_key:
        return cfg.ndi_base_url, cfg.ndi_api_key, "x-api-key"
    return cfg.nace_base_url, cfg.nace_api_key, "bearer"


def api_url(base: str, route: str) -> str:
    """Join a base URL with a route that starts with ``/documents`` or ``/jobs``.

    Console base is ``https://console.nace.ai`` (no version). The direct NDI default
    in config already ends in ``/v1``, so it is not versioned twice.
    """
    root = base.rstrip("/")
    path = route if route.startswith("/") else f"/{route}"
    if root.endswith("/v1"):
        return root + path
    return root + "/v1" + path


def resolve_pdf(case_id: str, doc: str) -> Path:
    direct = SYN_DIR / "pdf" / f"{case_id}_{doc}.pdf"
    if direct.is_file():
        return direct
    case = _try_load_case(case_id)
    if case is not None:
        for ref in case.docs:
            if ref.kind != doc:
                continue
            candidate = Path(ref.pdf)
            if not candidate.is_absolute():
                candidate = PKG_ROOT / candidate
            if candidate.is_file():
                return candidate
    raise FileNotFoundError(f"no PDF for {case_id} {doc}")


def pointer_to_case_path(pointer: str) -> str:
    """JSON Pointer ``/bill/lines/0/code`` → ``bill.lines.1.code``.

    Array indexes under ``lines`` become 1-based line numbers, matching
    ``Case.citations`` keys such as ``bill.lines.3.charge_cents``.
    """
    if pointer in ("", "/"):
        return ""
    parts = pointer.lstrip("/").split("/")
    out: list[str] = []
    prev: str | None = None
    for raw in parts:
        part = raw.replace("~1", "/").replace("~0", "~")
        if part.isdigit() and prev == "lines":
            out.append(str(int(part) + 1))
        else:
            out.append(part)
        prev = part
    return ".".join(out)


def _pairs(raw: Any) -> list[tuple[float, float]]:
    if not isinstance(raw, (list, tuple)) or not raw:
        return []
    first = raw[0]
    if isinstance(first, (int, float)):
        if len(raw) % 2:
            return []
        return [(float(raw[i]), float(raw[i + 1])) for i in range(0, len(raw), 2)]
    if isinstance(first, dict):
        return [(float(p["x"]), float(p["y"])) for p in raw if "x" in p and "y" in p]
    if isinstance(first, (list, tuple)) and len(first) >= 2 and isinstance(first[0], (int, float)):
        return [(float(p[0]), float(p[1])) for p in raw]
    return []


def parse_polygons(raw: Any) -> list[list[tuple[float, float]]]:
    if not isinstance(raw, (list, tuple)) or not raw:
        return []
    first = raw[0]
    if isinstance(first, (int, float)):
        pts = _pairs(raw)
        return [pts] if pts else []
    # One polygon of [x, y] points, rather than a list of polygons.
    if isinstance(first, (list, tuple)) and first and isinstance(first[0], (int, float)):
        pts = _pairs(raw)
        return [pts] if pts else []
    polys: list[list[tuple[float, float]]] = []
    for poly in raw:
        pts = _pairs(poly)
        if pts:
            polys.append(pts)
    return polys


def normalize_polygons(
    raw: Any,
    *,
    page_width: float | None = None,
    page_height: float | None = None,
    origin: str | None = None,
) -> list[list[tuple[float, float]]]:
    """Return polygons in normalized [0, 1] top-left coordinates.

    Values already inside that range are kept. Larger values are page units and
    are divided by the page size. Y is flipped only when ``origin`` says the
    source is bottom-left (PDF user space).
    """
    if (
        isinstance(raw, list)
        and raw
        and isinstance(raw[0], (list, tuple))
        and raw[0]
        and isinstance(raw[0][0], tuple)
    ):
        parsed: list[list[tuple[float, float]]] = raw
    else:
        parsed = parse_polygons(raw)
    coords = [c for poly in parsed for x, y in poly for c in (x, y)]
    if not coords:
        return []
    if max(abs(c) for c in coords) <= _UNIT_MAX:
        return [[(float(x), float(y)) for x, y in poly] for poly in parsed]
    width = float(page_width) if page_width else _LETTER[0]
    height = float(page_height) if page_height else _LETTER[1]
    if width == 0 or height == 0:
        return [[(float(x), float(y)) for x, y in poly] for poly in parsed]
    flip = (origin or "").lower().replace("_", "-") in {"bottom-left", "pdf", "bottom"}
    out: list[list[tuple[float, float]]] = []
    for poly in parsed:
        pts: list[tuple[float, float]] = []
        for x, y in poly:
            ny = float(y) / height
            if flip:
                ny = 1.0 - ny
            pts.append((float(x) / width, ny))
        out.append(pts)
    return out


def pdf_page_sizes(pdf: Path) -> dict[int, tuple[float, float]]:
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(pdf))
    except Exception:
        return {}
    try:
        return {i + 1: (float(doc[i].get_size()[0]), float(doc[i].get_size()[1])) for i in range(len(doc))}
    except Exception:
        return {}
    finally:
        doc.close()


def _page_box(location: dict[str, Any], pdf_size: tuple[float, float] | None) -> tuple[Any, Any, Any]:
    width = location.get("width", location.get("page_width"))
    height = location.get("height", location.get("page_height"))
    if (width is None or height is None) and pdf_size:
        width, height = pdf_size
    return width, height, location.get("origin")


def citation_from_ndi(raw: dict[str, Any], *, doc: str, page_size: tuple[float, float] | None) -> dict[str, Any]:
    location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
    page = int(location.get("page") or raw.get("page") or 1)
    width, height, origin = _page_box(location, page_size)
    polygons = normalize_polygons(
        location.get("polygons") if location else raw.get("polygons"),
        page_width=width,
        page_height=height,
        origin=origin,
    )
    text = raw.get("source_text")
    if text is None:
        text = raw.get("text")
    return Citation(doc=doc, page=page, polygons=polygons, text=text).model_dump(mode="json")  # type: ignore[arg-type]


def _maybe_json(value: Any) -> Any:
    """NDI sometimes writes an object/array field as a JSON-encoded string."""
    if isinstance(value, str):
        s = value.strip()
        if s[:1] in "{[":
            try:
                return json.loads(s)
            except (json.JSONDecodeError, ValueError):
                return value
    return value


def _qualify_path(path: str, doc: str) -> str:
    """Top-level ``lines`` items map under the doc's line paths (``eob.lines.2``)."""
    if path == "lines":
        return f"{doc}.lines"
    if path.startswith("lines."):
        return f"{doc}.{path}"
    return path


def explode_value(path: str, value: Any, status: str, citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        rows: list[dict[str, Any]] = []
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            rows.extend(explode_value(child_path, child, status, citations))
        return rows
    if isinstance(value, list):
        rows = []
        for index, child in enumerate(value):
            child_path = f"{path}.{index + 1}" if path else str(index + 1)
            rows.extend(explode_value(child_path, child, status, citations))
        return rows
    if not path:
        return []
    return [{"path": path, "value": value, "status": status, "citations": citations}]


def convert_fields(
    result: dict[str, Any],
    doc: str,
    page_sizes: dict[int, tuple[float, float]],
) -> list[dict[str, Any]]:
    """NDI extract ``result`` → field dicts keyed like ``Case.citations``."""
    out: dict[str, dict[str, Any]] = {}
    containers: list[tuple[str, Any, str, list[dict[str, Any]]]] = []
    for field in result.get("fields") or []:
        path = _qualify_path(pointer_to_case_path(str(field.get("path") or "")), doc)
        if not path:
            continue
        citations: list[dict[str, Any]] = []
        for raw in field.get("citations") or []:
            if not isinstance(raw, dict):
                continue
            location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
            page = int(location.get("page") or 1)
            citations.append(citation_from_ndi(raw, doc=doc, page_size=page_sizes.get(page)))
        value = _maybe_json(field.get("value"))
        status = field.get("status") or ("not_found" if value is None else "found")
        if isinstance(value, (dict, list)):
            containers.append((path, value, status, citations))
        else:
            out[path] = {"path": path, "value": value, "status": status, "citations": citations}
    for path, value, status, citations in containers:
        for row in explode_value(path, value, status, citations):
            out.setdefault(row["path"], row)
    return list(out.values())


# ---------------------------------------------------------------- grading


def explode_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Field rows with container values expanded to leaf rows."""
    out: dict[str, dict[str, Any]] = {}
    for f in fields:
        path = f.get("path") or ""
        value = _maybe_json(f.get("value"))
        status = f.get("status") or ("not_found" if value is None else "found")
        citations = f.get("citations") or []
        if isinstance(value, (dict, list)):
            for row in explode_value(path, value, status, citations):
                out.setdefault(row["path"], row)
        elif path:
            out.setdefault(path, {**f, "value": value, "status": status})
    return list(out.values())


def _norm_value(v: Any) -> Any:
    v = _maybe_json(v)
    if isinstance(v, str):
        s = " ".join(v.split()).casefold()
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return s
    if isinstance(v, float):
        return int(v) if v.is_integer() else v
    if isinstance(v, list):
        return [_norm_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _norm_value(x) for k, x in v.items()}
    return v


def score_fields(
    case: Any,
    fields: list[dict[str, Any]],
    *,
    case_id: str | None = None,
    doc: str | None = None,
) -> dict[str, Any]:
    """Exact-match accuracy of extract ``fields`` against a case, per leaf cell.

    A field is scored only when the case carries a value at that path AND the
    value is printed on the PDF — cells NDI leaves null because the value never
    appears on that document are skipped, not counted as misses. ``extra``
    counts values NDI produced where the case has nothing (possible over-read).
    Money is compared by magnitude: bills print credits as ``-$17.37``.
    """
    blob: str | None = None
    if case_id and doc:
        try:
            blob = _pdf_text_blob(resolve_pdf(case_id, doc))
        except FileNotFoundError:
            blob = None
    rows = explode_fields(fields)
    scored: list[dict[str, Any]] = []
    extra = skipped = 0
    for row in rows:
        path = row["path"]
        gold = _dig_case(case, path)
        if gold in (None, "", [], {}):
            if row.get("value") not in (None, "", [], {}):
                extra += 1
            continue
        value = row.get("value")
        if value in (None, "", [], {}):
            # NDI found nothing. If the gold value cannot be located in the
            # page text either, the cell was never printed — skip it.
            if blob is None or not _printed(gold, blob):
                skipped += 1
                continue
        hit = _same_value(path, value, gold)
        scored.append({"path": path, "value": value, "gold": gold, "match": hit})
    matched = sum(1 for r in scored if r["match"])
    return {
        "matched": matched,
        "scored": len(scored),
        "skipped": skipped,
        "accuracy": matched / len(scored) if scored else None,
        "extra": extra,
        "rows": scored,
    }


def _same_value(path: str, a: Any, b: Any) -> bool:
    na, nb = _norm_value(a), _norm_value(b)
    if na == nb:
        return True
    # Credits/payments print negative on bills ("-$17.37"); the case stores the
    # magnitude. Compare absolute value for *_cents leaf cells.
    if path.endswith("_cents") and isinstance(na, (int, float)) and isinstance(nb, (int, float)):
        return abs(na) == abs(nb)
    return False


def _pdf_text_blob(pdf: Path) -> str:
    """Whole-PDF text layer squashed to lowercase alnum for printability checks."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return ""
    try:
        doc = pdfium.PdfDocument(str(pdf))
        text = "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    except Exception:
        return ""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _printed(gold: Any, blob: str) -> bool:
    """True when a case value plausibly appears in the PDF text layer."""
    if not blob:
        return False
    if isinstance(gold, bool):
        return False  # can't verify booleans — treat as not printed
    if isinstance(gold, (int, float)):
        v = abs(float(gold))
        needles = {f"{v:,.2f}", f"{v:.2f}"}
        dollars = v / 100
        needles |= {f"{dollars:,.2f}", f"{dollars:.2f}"}
        if v == int(v):
            needles.add(str(int(v)))
    else:
        needles = {str(gold)}
    for needle in needles:
        norm = re.sub(r"[^a-z0-9]", "", str(needle).lower())
        if norm and norm in blob:
            return True
    return False


def _try_load_case(case_id: str) -> Any:
    try:
        from billproof.data import load_case
    except ImportError:
        return None
    try:
        return load_case(case_id)
    except Exception:
        return None


def _dig_case(case: Any, path: str) -> Any:
    from pydantic import BaseModel

    cur: Any = case
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            if not part.isdigit():
                return None
            number = int(part)
            found = None
            for item in cur:
                line_no = item.get("line_no") if isinstance(item, dict) else getattr(item, "line_no", None)
                if line_no == number:
                    found = item
                    break
            if found is None and 1 <= number <= len(cur):
                found = cur[number - 1]
            cur = found
            continue
        if isinstance(cur, BaseModel):
            cur = getattr(cur, part, None)
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
            continue
        return None
    if isinstance(cur, BaseModel):
        return cur.model_dump(mode="json")
    return cur


def _cache_name(case_id: str, citation: Citation) -> str:
    payload = json.dumps(
        {
            "case_id": case_id,
            "doc": citation.doc,
            "page": citation.page,
            "text": citation.text,
            "polygons": citation.model_dump(mode="json")["polygons"],
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def ndi_cache_path(case_id: str, citation: Citation) -> Path:
    digest = _cache_name(case_id, citation)
    return CACHE_DIR / "ndi" / case_id / f"{citation.doc}_p{citation.page}_{digest}.png"


def crop_cache_path(case_id: str, citation: Citation) -> Path:
    digest = _cache_name(case_id, citation)
    return CACHE_DIR / "crops" / f"{case_id}_{citation.doc}_p{citation.page}_{digest}.png"


def extract_cache_path(case_id: str, doc: str, pdf: Path) -> Path:
    """Extract results keyed by (case_id, doc, sha256 of the PDF bytes, version)."""
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()[:16]
    version = EXTRACT_VERSION.get(doc, _EXTRACT_VERSION_DEFAULT)
    return CACHE_DIR / "ndi" / "extract" / case_id / f"{doc}_{sha}_{version}.json"


def crop_pdf_page(pdf: Path, citation: Citation) -> bytes | None:
    """Rasterize one page and crop the polygon bbox plus 12px of padding."""
    if not citation.polygons:
        return None
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        index = citation.page - 1
        if index < 0 or index >= len(doc):
            return None
        image = doc[index].render(scale=_RENDER_SCALE).to_pil()
    finally:
        doc.close()
    width, height = image.size
    xs = [float(x) * width for poly in citation.polygons for x, y in poly]
    ys = [float(y) * height for poly in citation.polygons for x, y in poly]
    if not xs:
        return None
    left = max(0, int(min(xs)) - _PAD_PX)
    top = max(0, int(min(ys)) - _PAD_PX)
    right = min(width, int(max(xs)) + _PAD_PX)
    bottom = min(height, int(max(ys)) + _PAD_PX)
    if right <= left or bottom <= top:
        return None
    buf = io.BytesIO()
    image.crop((left, top, right, bottom)).save(buf, format="PNG")
    return buf.getvalue()


def _as_citation(citation: Citation | dict[str, Any]) -> Citation:
    if isinstance(citation, Citation):
        return citation
    return Citation.model_validate(citation)


def _coerce_fixture_citation(raw: dict[str, Any], doc: str) -> dict[str, Any]:
    if "location" in raw and "doc" not in raw:
        return citation_from_ndi(raw, doc=doc, page_size=None)
    data = dict(raw)
    data["doc"] = data.get("doc") or doc
    width = data.pop("width", data.pop("page_width", None))
    height = data.pop("height", data.pop("page_height", None))
    origin = data.pop("origin", None)
    polygons = normalize_polygons(data.get("polygons"), page_width=width, page_height=height, origin=origin)
    data["polygons"] = polygons
    return Citation.model_validate(data).model_dump(mode="json")


def _normalize_fixture_field(field: dict[str, Any], doc: str) -> dict[str, Any]:
    citations = []
    for raw in field.get("citations") or []:
        if isinstance(raw, dict):
            citations.append(_coerce_fixture_citation(raw, doc))
    return {
        "path": field.get("path") or "",
        "value": field.get("value"),
        "status": field.get("status") or ("not_found" if field.get("value") is None else "found"),
        "citations": citations,
    }


def _fields_from_case(case_id: str, doc: str) -> list[dict[str, Any]]:
    case = _try_load_case(case_id)
    if case is None:
        return []
    rows: list[dict[str, Any]] = []
    for path, cit in (case.citations or {}).items():
        if cit.doc != doc:
            continue
        value = _dig_case(case, path)
        rows.append(
            {
                "path": path,
                "value": value,
                "status": "found" if value is not None else "not_found",
                "citations": [cit.model_dump(mode="json")],
            }
        )
    return rows


class NdiAdapter:
    """Live NDI client. ``simulated`` is False."""

    simulated = False

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        auth: str | None = None,
        poll_interval: float = 0.4,
        timeout_s: float = 180.0,
    ) -> None:
        if api_key is None and base_url is None:
            base_url, api_key, auth = resolve_credentials()
        else:
            if base_url is None or api_key is None or auth is None:
                resolved_base, resolved_key, resolved_auth = resolve_credentials()
                base_url = base_url or resolved_base
                api_key = resolved_key if api_key is None else api_key
                auth = auth or resolved_auth
            if auth is None:
                auth = "x-api-key" if str(api_key).startswith("ndi_") or "ndi-api" in str(base_url) else "bearer"
        self._base = str(base_url).rstrip("/")
        self._key = str(api_key)
        self._auth = auth
        self._poll_interval = poll_interval
        self._timeout_s = timeout_s
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0))
        self._uploaded: dict[tuple[str, str], dict[str, str]] = {}
        self.jobs: list[dict[str, Any]] = []  # one record per billed job, in order

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def extract(self, case_id: str, doc: str, refresh: bool = False) -> list[dict[str, Any]]:
        if doc not in SCHEMAS:
            raise ValueError(f"unknown document {doc!r}")
        pdf = resolve_pdf(case_id, doc)
        cache = extract_cache_path(case_id, doc, pdf)
        if not refresh and cache.is_file():
            try:
                return json.loads(cache.read_text())
            except json.JSONDecodeError:
                pass
        fields = self.extract_pdf(pdf, doc)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(fields))
        return fields

    def extract_pdf(self, pdf: Path, doc: str) -> list[dict[str, Any]]:
        """Extract one PDF by path — for uploads that have no case_id yet."""
        if doc not in SCHEMAS:
            raise ValueError(f"unknown document {doc!r}")
        job = self._run_job(
            "extract",
            {
                "source": self._source_for_path(pdf),
                "schema": SCHEMAS[doc],
                "citations": {"enabled": True, "include_source_text": True},
                "instructions": _INSTRUCTIONS,
            },
        )
        return convert_fields(job.get("result") or {}, doc, pdf_page_sizes(pdf))

    def split(self, case_id: str, *, doc: str = "packet") -> list[dict[str, Any]]:
        return self.split_pdf(resolve_pdf(case_id, doc))

    def split_pdf(self, pdf: Path) -> list[dict[str, Any]]:
        """Split one PDF by path → segment dicts (class/start_page/end_page)."""
        job = self._run_job(
            "split",
            {
                "source": self._source_for_path(pdf),
                "classes": SPLIT_CLASSES,
                "unknown_policy": "include",
                "output": {"include_content": False, "materialize_files": False},
            },
        )
        result = job.get("result") or {}
        return list(result.get("segments") or [])

    def ground(self, case_id: str, citation: Citation) -> bytes | None:
        parsed = _as_citation(citation)
        cached = ndi_cache_path(case_id, parsed)
        if cached.is_file():
            return cached.read_bytes()
        text = (parsed.text or "").strip()
        if not text:
            return None
        pdf = resolve_pdf(case_id, parsed.doc)
        source = self._source_for(case_id, parsed.doc, pdf)
        target: dict[str, Any] = {"id": "cite", "text": text, "page_hints": [parsed.page]}
        job = self._run_job(
            "ground",
            {
                "source": source,
                "targets": [target],
                "options": {"max_matches": 1, "include_previews": True},
            },
        )
        url = _first_crop_url(job.get("result") or {})
        if not url:
            return None
        blob = self._download_crop(url)
        if not blob:
            return None
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(blob)
        return blob

    def upload(self, path: Path) -> dict[str, str]:
        data = path.read_bytes()
        name = path.name
        grant_resp = self._send(
            "POST",
            api_url(self._base, "/documents/upload-grants"),
            headers=self._auth_headers(),
            json_body={"path": name, "total_size_bytes": len(data), "ttl_seconds": 600},
        )
        grant = grant_resp.json()
        uploaded = self._client.post(
            grant["upload_url"],
            headers={"X-Upload-Token": grant["token"]},
            files={
                "file": (name, data, "application/pdf"),
                "metadata": (
                    None,
                    json.dumps({"path": name, "total_size_bytes": len(data)}),
                    "application/json",
                ),
            },
        )
        if uploaded.status_code >= 400:
            raise RuntimeError(f"NDI upload -> {uploaded.status_code}: {uploaded.text[:300]}")
        payload = uploaded.json()
        file_info = (payload.get("result") or {}).get("file") or payload.get("file") or payload
        workspace_id = file_info.get("workspace_id") or grant["workspace_id"]
        file_id = file_info["file_id"]
        return {"type": "workspace_file", "workspace_id": workspace_id, "file_id": file_id}

    def _source_for(self, case_id: str, doc: str, pdf: Path) -> dict[str, str]:
        key = (case_id, doc)
        source = self._uploaded.get(key)
        if source is None:
            source = self.upload(pdf)
            self._uploaded[key] = source
        return source

    def _source_for_path(self, pdf: Path) -> dict[str, str]:
        try:
            key = ("path", str(pdf.resolve()), str(pdf.stat().st_mtime_ns))
        except OSError:
            key = ("path", str(pdf), "0")
        source = self._uploaded.get(key)
        if source is None:
            source = self.upload(pdf)
            self._uploaded[key] = source
        return source

    def _run_job(self, op: str, body: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        response = self._send(
            "POST",
            api_url(self._base, f"/documents/{op}"),
            headers=self._auth_headers(idempotency=str(uuid.uuid4())),
            json_body=body,
            params={"wait_seconds": 0},
        )
        job = self._wait_job(response.json())
        result = job.get("result") or {}
        cost = job.get("cost")
        if isinstance(cost, dict):
            cost = cost.get("usd", cost.get("total_usd"))
        try:
            cost = float(cost) if cost is not None else None
        except (TypeError, ValueError):
            cost = None
        self.jobs.append(
            {
                "op": op,
                "job_id": job.get("job_id"),
                "seconds": round(time.monotonic() - started, 3),
                "units": result.get("units"),
                "cost_usd": cost,
            }
        )
        return job

    def _wait_job(self, created: dict[str, Any]) -> dict[str, Any]:
        status = created.get("status")
        if status in {"failed", "cancelled"}:
            _raise_terminal(created)
        if status == "succeeded" and created.get("result"):
            return created
        job_id = created.get("job_id")
        if not job_id:
            raise RuntimeError("NDI create response missing job_id")
        deadline = time.monotonic() + self._timeout_s
        while True:
            response = self._send(
                "GET",
                api_url(self._base, f"/documents/jobs/{job_id}"),
                headers=self._auth_headers(json_body=False),
            )
            job = response.json()
            status = job.get("status")
            if status in {"succeeded", "failed", "cancelled"}:
                if status != "succeeded" or not job.get("result"):
                    _raise_terminal(job)
                return job
            if time.monotonic() > deadline:
                raise TimeoutError(f"NDI job {job_id} still {status}")
            if self._poll_interval:
                time.sleep(self._poll_interval)

    def _download_crop(self, url: str) -> bytes:
        absolute = _absolute_url(self._base, url)
        on_api = "/documents/jobs/" in absolute or "/v1/jobs/" in absolute
        headers = self._auth_headers(json_body=False) if on_api else {}
        params = {"redirect": "false"} if on_api else None
        response = self._client.get(absolute, headers=headers, params=params)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location")
            if not location:
                raise RuntimeError("NDI crop redirect missing location")
            response = self._client.get(location)
        elif response.status_code == 200 and _looks_like_json(response):
            data = response.json()
            signed = data.get("url") if isinstance(data, dict) else None
            if not signed:
                raise RuntimeError("NDI crop response had no image url")
            response = self._client.get(signed)
        if response.status_code >= 400:
            raise RuntimeError(f"NDI crop download -> {response.status_code}: {response.text[:200]}")
        return response.content

    def _auth_headers(self, *, idempotency: str | None = None, json_body: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {}
        if json_body:
            headers["Content-Type"] = "application/json"
        if self._auth == "x-api-key":
            headers["X-API-Key"] = self._key
        else:
            headers["Authorization"] = f"Bearer {self._key}"
        if idempotency:
            headers["Idempotency-Key"] = idempotency
        return headers

    def _send(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        attempts: int = 4,
    ) -> httpx.Response:
        response: httpx.Response | None = None
        for attempt in range(attempts):
            response = self._client.request(method, url, headers=headers, json=json_body, params=params)
            if response.status_code in {429, 529} and attempt + 1 < attempts:
                time.sleep(_retry_delay(response))
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"NDI {method} {url} -> {response.status_code}: {response.text[:300]}")
            return response
        assert response is not None
        raise RuntimeError(f"NDI {method} {url} -> {response.status_code}")


class FixtureNdi:
    """Reads ``data/synthetic/ndi_fixtures/<case_id>.json``. No network."""

    simulated = True

    def extract(self, case_id: str, doc: str, refresh: bool = False) -> list[dict[str, Any]]:
        path = SYN_DIR / "ndi_fixtures" / f"{case_id}.json"
        if path.is_file():
            try:
                payload = json.loads(path.read_text())
            except json.JSONDecodeError:
                return []
            block = (payload.get("docs") or {}).get(doc) or {}
            return [_normalize_fixture_field(field, doc) for field in block.get("fields") or []]
        return _fields_from_case(case_id, doc)

    def ground(self, case_id: str, citation: Citation) -> bytes | None:
        parsed = _as_citation(citation)
        cached = crop_cache_path(case_id, parsed)
        if cached.is_file():
            return cached.read_bytes()
        try:
            pdf = resolve_pdf(case_id, parsed.doc)
        except FileNotFoundError:
            return None
        # Fixture polygons are already normalized; still scale page-unit leftovers.
        normalized = parsed.model_copy(
            update={
                "polygons": normalize_polygons(parsed.polygons),
            }
        )
        blob = crop_pdf_page(pdf, normalized)
        if not blob:
            return None
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(blob)
        return blob


def get_ndi() -> NdiAdapter | FixtureNdi:
    if settings().use_real("ndi"):
        return NdiAdapter()
    return FixtureNdi()


def _raise_terminal(job: dict[str, Any]) -> None:
    status = job.get("status")
    if status == "succeeded":
        raise RuntimeError(f"NDI job {job.get('job_id')} succeeded without a result")
    err = job.get("error") or {}
    message = err.get("message") if isinstance(err, dict) else err
    raise RuntimeError(f"NDI job {job.get('job_id')} {status}: {message or err}")


def _retry_delay(response: httpx.Response) -> float:
    millis = response.headers.get("retry-after-ms")
    if millis:
        return min(float(millis) / 1000.0, 8.0)
    seconds = response.headers.get("retry-after")
    if seconds:
        try:
            return min(float(seconds), 8.0)
        except ValueError:
            return 1.0
    return 0.5


def _first_crop_url(result: dict[str, Any]) -> str | None:
    for target in result.get("targets") or []:
        for match in target.get("matches") or []:
            url = match.get("cropped_image_url") or match.get("preview_url") or match.get("crop_url")
            if url:
                return str(url)
    return None


def _absolute_url(base: str, url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    root = base.rstrip("/")
    if url.startswith("/v1/") and root.endswith("/v1"):
        return root + url[len("/v1") :]
    if url.startswith("/"):
        host = root[: -len("/v1")] if root.endswith("/v1") else root
        return host + url
    return url


def _looks_like_json(response: httpx.Response) -> bool:
    ctype = response.headers.get("content-type", "")
    if "json" in ctype:
        return True
    body = response.content.lstrip()[:1]
    return body in {b"{", b"["}
