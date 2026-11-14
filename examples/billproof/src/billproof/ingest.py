"""Drop-your-bill ingest: uploaded PDFs -> schema.Case (W4b).

Two paths, chosen by ``settings().use_real("ndi")``:

- LIVE: each saved PDF goes through the real NDI adapter's extract job.
  A single ambiguous PDF — or one the caller forces with a ``split:`` key —
  first goes through the adapter's split job and is sliced into per-kind
  PDFs with pypdf, each then extracted on its own. NDI field paths already
  match ``Case.citations`` keys, so fields + their citation polygons map
  straight into a Case. Missing values fall back to plan/field defaults and
  line-level gaps trigger the MISSING_DATA check.
- SIMULATED: PDFs we rendered are recognised by sha256 (and by filename)
  against ``data/synthetic/pdf/`` and return their existing SYN case id.
  Anything else is parsed with pypdfium2 text + regexes tuned to our own
  PDF layout — honest, labelled SIMULATED extraction with text-only
  citations (no polygons).

Uploaded cases persist to ``data/cache/uploads/<case_id>.json`` (PDFs beside
it under ``<case_id>/``) and load via :func:`load_uploaded`. For ``/api/cases``,
page PNGs, ``/api/analyze`` and ``/api/packet`` to work, ``data.load_case``
needs a two-line ``UP-*`` fallback to that function (see W4b summary).
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter

from billproof.config import CACHE_DIR, SYN_DIR, settings
from billproof.schema import (
    Bill,
    BillLine,
    Case,
    Citation,
    Denial,
    DenialCategory,
    DocKind,
    DocRef,
    Eob,
    EobLine,
    Patient,
    PlanSummary,
    Provider,
)

DOC_KINDS: tuple[DocKind, ...] = ("bill", "eob", "denial", "plan")
UPLOAD_DIR = CACHE_DIR / "uploads"
MAX_FILES = 4

_UP_ID = re.compile(r"UP-\d{4,}")
_SYN_PDF = re.compile(r"(SYN-(?:\d{4}|D\d{2}))_(bill|eob|denial|plan)\.pdf$", re.I)
_MONEY = r"-?\$[\d,]+\.\d{2}"
_CODE = re.compile(r"^(?:\d{5}|[A-Z]\d{4}|\d{4}[A-Z]|[A-Z0-9]{5,6})$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ALPHA_MODS = {"RT", "LT", "TC", "XE", "XS", "XP", "XU", "EJ", "GA", "GC", "QW", "KX", "PR", "CO"}


# ---------------------------------------------------------------- public api


def ingest_pdfs(files: dict[str, bytes]) -> str:
    """``{doc_kind | "kind:name.pdf" | filename | "auto": pdf_bytes}`` -> case_id."""
    return ingest(files)["case_id"]


def ingest(files: dict[str, bytes]) -> dict:
    """Full ingest: returns {case_id, mode, fields_found, warnings, ...}."""
    items = _items(files)
    if not items:
        raise ValueError("at least one PDF is required")
    if len(items) > MAX_FILES:
        raise ValueError(f"at most {MAX_FILES} documents per drop")
    warnings: list[str] = []
    kept: list[dict[str, Any]] = []
    for it in items:
        if not it["blob"].lstrip()[:5] == b"%PDF-":
            warnings.append(f"{it['label']}: not a PDF — skipped")
            continue
        it["sha"] = hashlib.sha256(it["blob"]).hexdigest()
        it["text"] = _pdf_text(it["blob"])
        kept.append(it)
    if not kept:
        raise ValueError("no readable PDF in the upload")

    for it in kept:
        if it["kind"] is None:
            it["kind"] = detect_kind(it["text"], it["name"])
            if it["kind"]:
                warnings.append(f"{it['label']}: detected {it['kind']}")
        if it["kind"] is None:
            it["kind"] = "bill"
            warnings.append(f"{it['label']}: kind unclear — treated as bill")

    # Shortcut: every dropped file is one of our own rendered PDFs of the
    # same case -> hand back that case (demo magic; no new files written).
    hit = {_recognize(it) for it in kept}
    if None not in hit and len(hit) == 1:
        cid = hit.pop()
        warnings.append(f"recognised as existing case {cid}")
        return {
            "case_id": cid,
            "mode": _mode(),
            "fields_found": _fixture_field_count(cid),
            "warnings": warnings,
            "recognized": True,
            "docs": {it["kind"]: _npages(it["blob"]) for it in kept},
        }

    docs: dict[str, dict[str, Any]] = {}
    for it in kept:
        if it["kind"] in docs:
            warnings.append(f"{it['label']}: second {it['kind']} document ignored")
            continue
        docs[it["kind"]] = it

    fs_sha = _fileset_sha(docs)
    prev = _index_get(fs_sha)
    if prev and (UPLOAD_DIR / f"{prev}.json").exists():
        warnings.append(f"same documents ingested before as {prev}")
        return {
            "case_id": prev,
            "mode": _mode(),
            "fields_found": _uploaded_field_count(prev),
            "warnings": warnings,
            "recognized": False,
            "deduped": True,
            "docs": {k: _npages(v["blob"]) for k, v in docs.items()},
        }

    case_id = _next_case_id()
    live = _use_real()
    adapter = _ndi_adapter() if live else None

    # LIVE only: one ambiguous PDF (kind auto-detected or unclear) — or an item
    # the caller forced with a `split:` key — is a plausible merged packet:
    # NDI-split it into per-kind PDFs, then extract each. Any failure leaves
    # `docs` untouched and the normal flow continues below.
    if live and hasattr(adapter, "split_pdf"):
        if len(kept) == 1 and (kept[0].get("force_split") or not kept[0].get("explicit")):
            res = _split_packet(adapter, case_id, kept[0], warnings)
            if res is not None:
                return res
        elif any(i.get("force_split") for i in kept):
            warnings.append("split: applies to a single-PDF drop — split skipped")

    fieldsets: dict[str, list[dict[str, Any]]] = {}
    for kind, it in docs.items():
        pdf = _save_pdf(case_id, kind, it["blob"])
        if live:
            try:
                fieldsets[kind] = _live_fields(adapter, case_id, kind, pdf)
            except Exception as exc:
                warnings.append(f"NDI extract failed for {kind}: {exc}")
                fieldsets[kind] = []
        else:
            fieldsets[kind] = _parse_doc(kind, it["text"], warnings)

    case, n_found = _build_case(case_id, fieldsets, docs, warnings)
    _persist(case, fs_sha)
    return {
        "case_id": case_id,
        "mode": "LIVE" if live else "SIMULATED",
        "fields_found": n_found,
        "warnings": warnings,
        "recognized": False,
        "docs": {k: _npages(v["blob"]) for k, v in docs.items()},
    }


def load_uploaded(case_id: str) -> Case:
    """``data.load_case``-compatible lookup for uploaded ``UP-xxxx`` cases."""
    if not _UP_ID.fullmatch(case_id or ""):
        raise ValueError(f"invalid uploaded case id {case_id!r}")
    return Case.model_validate_json((UPLOAD_DIR / f"{case_id}.json").read_text())


# ---------------------------------------------------------------- input prep


def _items(files: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for key, blob in (files or {}).items():
        if not isinstance(blob, (bytes, bytearray)) or not blob:
            continue
        k = str(key).strip().replace("\\", "/")
        force_split = k.partition(":")[0].lower() == "split"
        kind, name = _split_key(k)
        out.append(
            {
                "blob": bytes(blob),
                "kind": kind,
                "name": name,
                "label": name or kind or "document",
                "explicit": kind is not None,
                "force_split": force_split,
            }
        )
    return out


def _split_key(key: str) -> tuple[str | None, str | None]:
    """'bill' -> (bill, None) · 'eob:x.pdf' -> (eob, 'x.pdf') · 'x.pdf' -> (None, 'x.pdf')
    · 'split:x.pdf' -> (None, 'x.pdf') (the item is also marked force-split by _items)."""
    k = key.strip().replace("\\", "/")
    head, _, tail = k.partition(":")
    if head.lower() in DOC_KINDS:
        return head.lower(), Path(tail).name if tail else None
    if head.lower() == "split":
        return None, Path(tail).name if tail else None
    if k.lower() in ("", "auto"):
        return None, None
    name = Path(k).name
    m = _SYN_PDF.search(name)
    if m:
        return m.group(2).lower(), name
    return None, name


def _pdf_text(blob: bytes) -> str:
    try:
        doc = pdfium.PdfDocument(blob)
        try:
            return "\n".join((pg.get_textpage().get_text_range() or "") for pg in doc)
        finally:
            doc.close()
    except Exception:
        return ""


def _npages(blob: bytes) -> int:
    try:
        doc = pdfium.PdfDocument(blob)
        try:
            return len(doc)
        finally:
            doc.close()
    except Exception:
        return 1


def detect_kind(text: str, name: str | None = None) -> str | None:
    """Doc kind from filename, else from printed text ('THIS IS NOT A BILL' ...)."""
    if name:
        m = _SYN_PDF.search(name)
        if m:
            return m.group(2).lower()
        n = name.lower()
        for kind, pat in (
            ("eob", r"eob|explanation|benefit"),
            ("denial", r"denial|adverse|appeal"),
            ("plan", r"plan|summary|coverage"),
            ("bill", r"bill|statement|itemiz|invoice"),
        ):
            if re.search(pat, n):
                return kind
    t = (text or "").upper()
    if not t.strip():
        return None
    if "THIS IS NOT A BILL" in t or "EXPLANATION OF BENEFITS" in t:
        return "eob"
    if "ADVERSE BENEFIT DETERMINATION" in t or "DETERMINATION:" in t or " DENIAL" in t:
        return "denial"
    if "SUMMARY OF BENEFITS" in t or ("DEDUCTIBLE" in t and "COINSURANCE" in t and "OUT-OF-POCKET" in t):
        return "plan"
    if "PATIENT STATEMENT" in t or "BALANCE DUE" in t or "STATEMENT" in t or "ITEMIZED" in t:
        return "bill"
    return None


# ------------------------------------------------------- synthetic recognition


@lru_cache(maxsize=1)
def _syn_sha_index() -> dict[str, tuple[str, str]]:
    """sha256 of every rendered synthetic PDF -> (case_id, doc)."""
    out: dict[str, tuple[str, str]] = {}
    pdf_dir = SYN_DIR / "pdf"
    if not pdf_dir.is_dir():
        return out
    for p in pdf_dir.glob("*.pdf"):
        m = _SYN_PDF.match(p.name)
        if not m:
            continue
        try:
            out[hashlib.sha256(p.read_bytes()).hexdigest()] = (m.group(1).upper(), m.group(2).lower())
        except OSError:
            continue
    return out


def _recognize(it: dict[str, Any]) -> str | None:
    hit = _syn_sha_index().get(it.get("sha") or "")
    if hit:
        return hit[0]
    m = _SYN_PDF.search(it.get("name") or "")
    if m and (SYN_DIR / "cases" / f"{m.group(1).upper()}.json").exists():
        return m.group(1).upper()
    return None


def _fixture_field_count(case_id: str) -> int:
    fx = SYN_DIR / "ndi_fixtures" / f"{case_id}.json"
    if fx.is_file():
        try:
            import json

            docs = (json.loads(fx.read_text()) or {}).get("docs") or {}
            return sum(
                1
                for block in docs.values()
                for f in (block.get("fields") or [])
                if f.get("status") == "found"
            )
        except Exception:
            pass
    try:
        from billproof.data import load_case

        return len(load_case(case_id).citations)
    except Exception:
        return 0


def _uploaded_field_count(case_id: str) -> int:
    try:
        return len(load_uploaded(case_id).citations)
    except Exception:
        return 0


# ------------------------------------------------------------- upload storage


def _fileset_sha(docs: dict[str, dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for kind in sorted(docs):
        h.update(kind.encode())
        h.update(docs[kind]["sha"].encode())
    return h.hexdigest()


def _index_path() -> Path:
    return UPLOAD_DIR / "index.json"


def _index_get(fs_sha: str) -> str | None:
    try:
        import json

        return (json.loads(_index_path().read_text()) or {}).get(fs_sha)
    except Exception:
        return None


def _index_put(fs_sha: str, case_id: str) -> None:
    import json

    try:
        idx = json.loads(_index_path().read_text()) if _index_path().exists() else {}
    except Exception:
        idx = {}
    idx[fs_sha] = case_id
    _index_path().write_text(json.dumps(idx, indent=2) + "\n")


def _next_case_id() -> str:
    best = 0
    if UPLOAD_DIR.is_dir():
        for p in UPLOAD_DIR.glob("UP-*.json"):
            m = re.match(r"UP-(\d+)\.json$", p.name)
            if m:
                best = max(best, int(m.group(1)))
    return f"UP-{best + 1:04d}"


def _save_pdf(case_id: str, kind: str, blob: bytes) -> Path:
    d = UPLOAD_DIR / case_id
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{case_id}_{kind}.pdf"
    out.write_bytes(blob)
    return out


def _persist(case: Case, fs_sha: str) -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / f"{case.case_id}.json").write_text(case.model_dump_json(indent=2) + "\n")
    _index_put(fs_sha, case.case_id)


# ------------------------------------------------------------- live NDI path


def _use_real() -> bool:
    try:
        return settings().use_real("ndi")
    except Exception:
        return False


def _mode() -> str:
    return "LIVE" if _use_real() else "SIMULATED"


def _ndi_adapter():
    from billproof.adapters.ndi import get_ndi

    return get_ndi()


def _live_fields(adapter: Any, case_id: str, kind: str, pdf: Path) -> list[dict[str, Any]]:
    """Same job as ``NdiAdapter.extract`` but on an explicit path.

    ``extract()`` resolves PDFs by case_id through ``data.load_case`` /
    ``data/synthetic/pdf/``; uploads live under ``data/cache/uploads/`` and the
    case JSON does not exist yet, so the extract job is driven directly here.
    """
    from billproof.adapters.ndi import _INSTRUCTIONS, SCHEMAS, convert_fields, pdf_page_sizes

    source = adapter._source_for(case_id, kind, pdf)
    job = adapter._run_job(
        "extract",
        {
            "source": source,
            "schema": SCHEMAS[kind],
            "citations": {"enabled": True, "include_source_text": True},
            "instructions": _INSTRUCTIONS,
        },
    )
    return convert_fields(job.get("result") or {}, kind, pdf_page_sizes(pdf))


def _tmp_pdf(blob: bytes) -> Path:
    fd, name = tempfile.mkstemp(prefix="billproof-split-", suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        fh.write(blob)
    return Path(name)


def _seg_confidence(seg: dict[str, Any]) -> Any:
    """classification_confidence -> a numeric score when present, else the band."""
    conf = seg.get("classification_confidence")
    if isinstance(conf, dict):
        for key in ("score", "confidence", "probability"):
            v = conf.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return v
        for key in ("band", "level", "label"):
            if conf.get(key) is not None:
                return conf[key]
        return None
    return conf if isinstance(conf, (int, float, str)) and not isinstance(conf, bool) else None


def _slice_pages(reader: PdfReader, ranges: list[tuple[int, int]]) -> bytes:
    """Concatenate 1-based inclusive page ranges into one PDF in order."""
    writer = PdfWriter()
    for s, e in ranges:
        for p in range(s - 1, e):
            writer.add_page(reader.pages[p])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _uploaded_doc_pages(case_id: str) -> dict[str, int]:
    try:
        return {d.kind: d.pages for d in load_uploaded(case_id).docs}
    except Exception:
        return {}


def _split_packet(
    adapter: Any, case_id: str, it: dict[str, Any], warnings: list[str]
) -> dict[str, Any] | None:
    """LIVE path for one merged packet: NDI-split, slice per kind, extract each.

    Dedup keys the index on the *packet's* sha (``split:<sha>``, not the derived
    per-kind shas): the same packet re-uploaded returns its case without
    re-running billed jobs. Returns the ingest result dict, or None when split
    produced no usable segments — the caller then falls back to the normal
    kind-detection flow.
    """
    idx_key = f"split:{it['sha']}"
    prev = _index_get(idx_key)
    if prev and (UPLOAD_DIR / f"{prev}.json").exists():
        warnings.append(f"same packet ingested before as {prev}")
        return {
            "case_id": prev,
            "mode": "LIVE",
            "fields_found": _uploaded_field_count(prev),
            "warnings": warnings,
            "recognized": False,
            "deduped": True,
            "split": {"segments": [], "seconds": 0.0, "deduped": True},
            "docs": _uploaded_doc_pages(prev),
        }

    jobs = getattr(adapter, "jobs", None)
    if not isinstance(jobs, list):
        jobs = []
    tmp = _tmp_pdf(it["blob"])
    try:
        n_jobs = len(jobs)
        try:
            segments = adapter.split_pdf(tmp)
        except Exception as exc:
            warnings.append(
                f"NDI split failed/unmatched — fell back to kind detection ({type(exc).__name__}: {exc})"
            )
            return None
    finally:
        tmp.unlink(missing_ok=True)
    seconds = round(
        sum(float(j.get("seconds") or 0.0) for j in jobs[n_jobs:] if j.get("op") == "split"), 3
    )
    if not segments:
        warnings.append("NDI split failed/unmatched — fell back to kind detection")
        return None
    try:
        reader = PdfReader(io.BytesIO(it["blob"]))
        n_pages = len(reader.pages)
    except Exception:
        warnings.append("NDI split failed/unmatched — fell back to kind detection (unreadable PDF)")
        return None

    seg_rows: list[dict[str, Any]] = []
    kind_ranges: dict[str, list[tuple[int, int]]] = {}
    for seg in segments:
        seg = seg if isinstance(seg, dict) else {}
        cls = seg.get("class") if isinstance(seg.get("class"), dict) else {}
        kind = str(cls.get("id") or "").lower()
        seg_rows.append(
            {
                "kind": kind or None,
                "start_page": seg.get("start_page"),
                "end_page": seg.get("end_page"),
                "confidence": _seg_confidence(seg),
            }
        )
        try:
            s, e = int(seg.get("start_page")), int(seg.get("end_page"))
        except (TypeError, ValueError):
            s, e = 0, -1
        if kind not in DOC_KINDS or e < s:
            warnings.append(
                f"unclassified segment pages {seg.get('start_page')}–{seg.get('end_page')} skipped"
            )
            continue
        if s > n_pages:
            warnings.append(f"{kind} segment pages {s}–{e} out of range (packet has {n_pages}) — skipped")
            continue
        if s < 1 or e > n_pages:
            warnings.append(f"{kind} segment pages {s}–{e} clipped to packet pages 1–{n_pages}")
            s, e = max(s, 1), min(e, n_pages)
        kind_ranges.setdefault(kind, []).append((s, e))
    if not kind_ranges:
        warnings.append("NDI split failed/unmatched — fell back to kind detection")
        return None

    docs: dict[str, dict[str, Any]] = {}
    fieldsets: dict[str, list[dict[str, Any]]] = {}
    for kind in DOC_KINDS:
        ranges = sorted(kind_ranges.get(kind) or [])
        if not ranges:
            continue
        if len(ranges) > 1 and any(ranges[i][0] > ranges[i - 1][1] + 1 for i in range(1, len(ranges))):
            spans = ", ".join(f"{s}–{e}" for s, e in ranges)
            warnings.append(f"{kind} spans disjoint page ranges ({spans}) — concatenated")
        blob = _slice_pages(reader, ranges)
        pdf = _save_pdf(case_id, kind, blob)
        docs[kind] = {
            "kind": kind,
            "blob": blob,
            "sha": hashlib.sha256(blob).hexdigest(),
            "name": f"{it['name'] or 'packet.pdf'}#{kind}",
            "label": f"{it['label']} · {kind}",
        }
        try:
            fieldsets[kind] = adapter.extract_pdf(pdf, kind)
        except Exception as exc:
            warnings.append(f"NDI extract failed for {kind}: {exc}")
            fieldsets[kind] = []

    warnings.append(f"split packet into {'+'.join(k for k in DOC_KINDS if k in docs)} ({seconds:.1f} s)")
    case, n_found = _build_case(case_id, fieldsets, docs, warnings)
    _persist(case, idx_key)
    return {
        "case_id": case_id,
        "mode": "LIVE",
        "fields_found": n_found,
        "warnings": warnings,
        "recognized": False,
        "split": {"segments": seg_rows, "seconds": seconds},
        "docs": {k: _npages(v["blob"]) for k, v in docs.items()},
    }


# ---------------------------------------------------------- regex/mock parse


def _lines(text: str) -> list[str]:
    return [s.strip() for s in (text or "").splitlines() if s.strip()]


def _grep1(lines: list[str], pat: str) -> str | None:
    r = re.compile(pat)
    for ln in lines:
        m = r.search(ln)
        if m:
            return m.group(1)
    return None


def _field(path: str, value: Any, kind: str, text: str | None = None) -> dict[str, Any]:
    cits = [{"doc": kind, "page": 1, "polygons": [], "text": text}] if text else []
    return {
        "path": path,
        "value": value,
        "status": "found" if value is not None else "not_found",
        "citations": cits,
    }


def _money_cents(v: Any) -> int | None:
    """int cents -> as-is · 'dollars'/floats/'$1,234.56' -> cents."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(round(v)) if v.is_integer() else int(round(v * 100))
    s = str(v).strip()
    if not s:
        return None
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    digits = re.sub(r"[^\d.]", "", s)
    if not digits:
        return None
    try:
        cents = int(round(float(digits) * 100)) if "." in digits else int(digits)
    except ValueError:
        return None
    return -cents if neg else cents


def _find(lines: list[str], exact: str) -> int | None:
    for i, ln in enumerate(lines):
        if ln == exact:
            return i
    return None


def _title(s: str) -> str:
    return s.title() if s.isupper() else s


_BILL_ROW = re.compile(rf"^(?:(\d{{4}}-\d{{2}}-\d{{2}})\s+)?([A-Z0-9]{{3,8}})\s+(.*\S)\s*$")
_ROW_END = re.compile(rf"^(\d+)\s+(?:(\d{{2}})\s+)?({_MONEY})\s*$")
_MOD = re.compile(r"^(?:\d{2}|[A-Z]{2})(?:,(?:\d{2}|[A-Z]{2}))*$")


def _parse_bill(text: str, warns: list[str]) -> list[dict[str, Any]]:
    lines = _lines(text)
    fields: list[dict[str, Any]] = []
    for pat, path in (
        (r"^STATEMENT DATE\s+(\S+)", "bill.statement_date"),
        (r"^ACCOUNT NO\s+(\S+)", "bill.account_no"),
        (r"^PATIENT ID\s+(\S+)", "patient.patient_id"),
        (r"^DOB\s+(\d{4}-\d{2}-\d{2})", "patient.dob"),
        (r"^DECEASED\s+(\d{4}-\d{2}-\d{2})", "patient.date_of_death"),
        (r"^MEMBER\s+(\S+)", "patient.member_id"),
    ):
        v = _grep1(lines, pat)
        fields.append(_field(path, v, "bill", v))

    i = _find(lines, "PROVIDER")
    org = None
    if i is not None and i + 1 < len(lines):
        org = lines[i + 1]
        fields.append(_field("provider.name", org, "bill", org))
        for ln in lines[i + 2 : i + 6]:
            m = re.match(r"^NPI\s+(\S+)", ln)
            if m:
                fields.append(_field("provider.npi", m.group(1), "bill", ln))
            elif ln.startswith("In-network"):
                fields.append(_field("provider.in_network", True, "bill", ln))
            elif ln.startswith("Out-of-network"):
                fields.append(_field("provider.in_network", False, "bill", ln))
            elif not re.match(r"^NPI\b", ln) and "provider.address" not in {f["path"] for f in fields}:
                fields.append(_field("provider.address", ln, "bill", ln))

    # itemized table: rows start "dos code [mod] desc", end "units [pos] $amt".
    rows: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    in_table = False
    for ln in lines:
        if ln.startswith("DATE OF SERVICE"):
            in_table = True
            continue
        if not in_table:
            continue
        if re.match(r"^Total charges\b", ln):
            break
        if re.search(r"· page \d+$|^Page \d+$|· Patient statement ·", ln) or (org and ln == org.upper()):
            continue
        end = _ROW_END.match(ln)
        if end:
            if cur is not None:
                cur["units"], cur["pos"], cur["amount"] = end.group(1), end.group(2), end.group(3)
                rows.append(cur)
                cur = None
            continue
        m = _BILL_ROW.match(ln)
        if m and _CODE.fullmatch(m.group(2)):
            if cur is not None:
                rows.append(cur)
            rest = m.group(3)
            mods: list[str] = []
            head = rest.split(" ", 1)[0]
            if _MOD.fullmatch(head) and rest != head:
                cand = head.split(",")
                if all(c.isdigit() or c in _ALPHA_MODS for c in cand):
                    mods = cand
                    rest = rest.split(" ", 1)[1]
            cur = {"dos": m.group(1), "code": m.group(2), "mods": mods, "desc": [rest], "ref": None,
                   "units": None, "pos": None, "amount": None}
        elif cur is not None:
            rm = re.match(r"^Ref:\s*(.+)$", ln)
            if rm:
                cur["ref"] = rm.group(1)
            else:
                cur["desc"].append(ln)
    if cur is not None:
        rows.append(cur)

    for n, r in enumerate(rows, 1):
        base = f"bill.lines.{n}"
        label = " ".join(r["desc"]).strip() or None
        fields += [
            _field(f"{base}.dos", r["dos"], "bill", r["dos"]),
            _field(f"{base}.code", r["code"], "bill", r["code"]),
            _field(f"{base}.modifiers", r["mods"] or None, "bill", ",".join(r["mods"]) or None),
            _field(f"{base}.label", label, "bill", label),
            _field(f"{base}.rendering_provider", r["ref"], "bill", r["ref"]),
            _field(f"{base}.units", int(r["units"]) if r["units"] else None, "bill", r["units"]),
            _field(f"{base}.pos", r["pos"], "bill", r["pos"]),
            _field(f"{base}.charge_cents", _money_cents(r["amount"]), "bill", r["amount"]),
        ]

    for pat, path, sign in (
        (rf"^Total charges\s+({_MONEY})", "bill.total_charges_cents", 1),
        (rf"^Insurance payments\s+({_MONEY})", "bill.insurance_paid_cents", -1),
        (rf"^Contractual adjustments\s+({_MONEY})", "bill.adjustments_cents", -1),
        (rf"^BALANCE DUE\s+({_MONEY})", "bill.patient_balance_cents", 1),
    ):
        v = _grep1(lines, pat)
        if v is not None:
            c = _money_cents(v)
            fields.append(_field(path, sign * c if c is not None else None, "bill", v))
    return fields


_EOB_ROW = re.compile(rf"^(\d+)\s+([A-Z0-9]{{3,8}})\s+(\d+)\s+({_MONEY})\s+({_MONEY})\s+({_MONEY})\s+({_MONEY})\s*(.*)$")


def _parse_eob(text: str, warns: list[str]) -> list[dict[str, Any]]:
    lines = _lines(text)
    fields: list[dict[str, Any]] = []
    if lines:
        fields.append(_field("eob.payer", _title(lines[0]), "eob", lines[0]))
    for pat, path in (
        (r"^CLAIM ID\s+(\S+)", "eob.claim_id"),
        (r"^PROCESSED\s+(\d{4}-\d{2}-\d{2})", "eob.processed_date"),
        (r"^Member ID\s+(\S+)", "patient.member_id"),
        (r"^Patient\s+(\S+)", "patient.patient_id"),
    ):
        v = _grep1(lines, pat)
        fields.append(_field(path, v, "eob", v))
    i = _find(lines, "PROVIDER")
    if i is not None and i + 1 < len(lines):
        fields.append(_field("provider.name", lines[i + 1], "eob", lines[i + 1]))
    for j, ln in enumerate(lines):
        if ln in ("In-network", "Out-of-network"):
            fields.append(_field("provider.in_network", ln == "In-network", "eob", ln))
            break

    in_table = False
    cur: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    for ln in lines:
        if ln.startswith("LINE CODE UNITS"):
            in_table = True
            continue
        if not in_table:
            continue
        if ln.startswith("TOTALS") or ln.startswith("ADJUSTMENT AND REMARK"):
            if cur is not None:
                rows.append(cur)
            break
        if re.search(r"· page \d+$|^Page \d+$|· Explanation of Benefits ·", ln):
            continue
        m = _EOB_ROW.match(ln)
        if m:
            if cur is not None:
                rows.append(cur)
            trail = m.group(8) or ""
            carc = re.findall(r"[A-Z]{2}-\d+", trail)
            remark = re.sub(r"[A-Z]{2}-\d+", "", trail).strip(" ,")
            cur = {"n": int(m.group(1)), "code": m.group(2), "units": int(m.group(3)),
                   "billed": m.group(4), "allowed": m.group(5), "paid": m.group(6),
                   "cost": m.group(7), "carc": carc, "remark": [remark] if remark else []}
        elif cur is not None:
            cur["remark"].append(ln)
    for n, r in enumerate(rows, 1):
        base = f"eob.lines.{n}"
        remark = " ".join(r["remark"]).strip() or None
        fields += [
            _field(f"{base}.code", r["code"], "eob", r["code"]),
            _field(f"{base}.units", r["units"], "eob", str(r["units"])),
            _field(f"{base}.billed_cents", _money_cents(r["billed"]), "eob", r["billed"]),
            _field(f"{base}.allowed_cents", _money_cents(r["allowed"]), "eob", r["allowed"]),
            _field(f"{base}.plan_paid_cents", _money_cents(r["paid"]), "eob", r["paid"]),
            _field(f"{base}.patient_resp_cents", _money_cents(r["cost"]), "eob", r["cost"]),
            _field(f"{base}.carc", r["carc"] or None, "eob", ", ".join(r["carc"]) or None),
            _field(f"{base}.remark", remark, "eob", remark),
        ]
    v = _grep1(lines, rf"TOTAL YOUR COST\s+({_MONEY})")
    fields.append(_field("eob.patient_resp_total_cents", _money_cents(v), "eob", v))
    return fields


def _parse_denial(text: str, warns: list[str]) -> list[dict[str, Any]]:
    lines = _lines(text)
    fields: list[dict[str, Any]] = []
    v = _grep1(lines, r"^DATE OF NOTICE\s+(\S+)")
    fields.append(_field("denial.letter_date", v, "denial", v))
    v = _grep1(lines, r"^Member:\s*(\S+)")
    fields.append(_field("patient.patient_id", v, "denial", v))
    v = _grep1(lines, r"Member ID\s+(\S+)")
    fields.append(_field("patient.member_id", v, "denial", v))
    v = _grep1(lines, r"^Service in question:\s*(\S+)")
    fields.append(_field("denial.service_code", v, "denial", v))
    v = _grep1(lines, r"^Plan reference:\s*(.+)")
    fields.append(_field("denial.plan_section", v, "denial", v))
    v = _grep1(lines, r"APPEAL DEADLINE:\s*(\S+)")
    fields.append(_field("denial.appeal_deadline", v, "denial", v))

    for i, ln in enumerate(lines):
        m = re.match(rf"^Determination:\s*([A-Z]{{2}}-\d+)\s*(?:·\s*(.+))?$", ln)
        if not m:
            continue
        fields.append(_field("denial.carc", m.group(1), "denial", m.group(1)))
        words = (m.group(2) or "").strip()
        cat = words.upper().replace(" ", "_") if words else None
        fields.append(_field("denial.category", cat, "denial", words or None))
        reason: list[str] = []
        for ln2 in lines[i + 1 :]:
            if re.match(r"^Plan reference:|^YOUR RIGHT|^Sincerely", ln2):
                break
            reason.append(ln2)
        txt = " ".join(reason).strip() or None
        fields.append(_field("denial.reason_text", txt, "denial", txt))
        break
    return fields


def _parse_plan(text: str, warns: list[str]) -> list[dict[str, Any]]:
    lines = _lines(text)
    fields: list[dict[str, Any]] = []
    v = _grep1(lines, r"^MEMBER\s+(\S+)")
    fields.append(_field("patient.member_id", v, "plan", v))
    for ln in lines:
        mm = re.match(r"^Plan:\s*(.+)\s+(PPO|HMO|EPO|HDHP)\s+Coverage year", ln)
        if mm:
            fields.append(_field("plan.plan_name", mm.group(1), "plan", ln))
            fields.append(_field("plan.plan_type", mm.group(2), "plan", mm.group(2)))
            break
    for pat, path, is_pct in (
        (rf"^Annual deductible \(individual\)\s+({_MONEY})", "plan.deductible_cents", False),
        (rf"^Deductible met to date\s+({_MONEY})", "plan.deductible_met_cents", False),
        (rf"^Office visit copay\s+({_MONEY})", "plan.copay_cents", False),
        (r"^Coinsurance \(you pay\)\s+(\d+)%", "plan.coinsurance_pct", True),
        (rf"^Out-of-pocket maximum\s+({_MONEY})", "plan.oop_max_cents", False),
    ):
        v = _grep1(lines, pat)
        if v is not None:
            fields.append(_field(path, int(v) if is_pct else _money_cents(v), "plan", v))
    v = _grep1(lines, r"No Surprises Act protection \(emergency\)\s+(Applies|Does not apply)")
    if v:
        fields.append(_field("plan.emergency_nsa_protected", v == "Applies", "plan", v))
    return fields


_PARSERS = {"bill": _parse_bill, "eob": _parse_eob, "denial": _parse_denial, "plan": _parse_plan}


def _parse_doc(kind: str, text: str, warnings: list[str]) -> list[dict[str, Any]]:
    try:
        return _PARSERS[kind](text, warnings)
    except Exception as exc:
        warnings.append(f"{kind} parse error: {exc}")
        return []


# ---------------------------------------------------------------- case build


def _isod(v: Any) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s if _ISO.match(s) else None


def _str(v: Any, default: str) -> str:
    s = str(v).strip() if v is not None else ""
    return s or default


def _str_or_none(v: Any) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(float(str(v)))
    except (TypeError, ValueError):
        return default


def _cents(v: Any, default: int = 0) -> int:
    c = _money_cents(v)
    return default if c is None else c


def _bool(v: Any, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in ("true", "yes", "1", "y"):
        return True
    if s in ("false", "no", "0", "n"):
        return False
    return default


def _lst(v: Any) -> list[str]:
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip() if v is not None else ""
    return [x.strip() for x in s.split(",") if x.strip()] if s else []


def _cat(v: Any, warns: list[str]) -> DenialCategory:
    try:
        return DenialCategory(str(v).strip().upper())
    except ValueError:
        warns.append(f"denial category {v!r} unrecognised — using MISSING_INFO")
        return DenialCategory.MISSING_INFO


def _build_case(
    case_id: str,
    fieldsets: dict[str, list[dict[str, Any]]],
    docs: dict[str, dict[str, Any]],
    warnings: list[str],
) -> tuple[Case, int]:
    vals: dict[str, Any] = {}
    cits: dict[str, Citation] = {}
    for kind in DOC_KINDS:
        for frow in fieldsets.get(kind) or []:
            path, value = frow.get("path"), frow.get("value")
            if not path:
                continue
            if value is not None and path not in vals:
                vals[path] = value
            for c in frow.get("citations") or []:
                if path not in cits:
                    try:
                        cits[path] = Citation.model_validate(c)
                    except Exception:
                        pass
                break

    def v(path: str, default: Any = None) -> Any:
        out = vals.get(path)
        return default if out is None else out

    today = date.today().isoformat()
    line_ns = sorted(
        {int(m.group(1)) for p in vals for m in [re.match(r"bill\.lines\.(\d+)\.", p)] if m}
    )
    blines = [
        BillLine(
            line_no=n,
            dos=_isod(v(f"bill.lines.{n}.dos")),
            code=_str(v(f"bill.lines.{n}.code"), "?"),
            modifiers=_lst(v(f"bill.lines.{n}.modifiers")),
            label=_str(v(f"bill.lines.{n}.label"), "(unlabeled service)"),
            units=max(0, _int(v(f"bill.lines.{n}.units"))),
            charge_cents=max(0, _cents(v(f"bill.lines.{n}.charge_cents"))),
            pos=_str_or_none(v(f"bill.lines.{n}.pos")),
            rendering_provider=_str_or_none(v(f"bill.lines.{n}.rendering_provider")),
        )
        for n in line_ns
    ]
    if "bill" not in fieldsets:
        warnings.append("no bill document uploaded — placeholder bill line triggers MISSING_DATA")
        blines = [
            BillLine(line_no=1, dos=None, code="?", label="No readable bill document", units=0, charge_cents=0)
        ]
    elif not blines:
        warnings.append("bill parsed with no service lines — placeholder triggers MISSING_DATA")
        blines = [
            BillLine(line_no=1, dos=None, code="?", label="No readable bill lines", units=0, charge_cents=0)
        ]
    missing_dos = sum(1 for ln in blines if not ln.dos and ln.code != "?")
    if missing_dos:
        warnings.append(f"{missing_dos} bill line(s) missing a service date")

    total = _cents(v("bill.total_charges_cents"), sum(ln.charge_cents for ln in blines))
    paid = _cents(v("bill.insurance_paid_cents"))
    adj = _cents(v("bill.adjustments_cents"))
    if v("bill.patient_balance_cents") is None:
        warnings.append("balance due not found — computed from totals")
    balance = _cents(v("bill.patient_balance_cents"), max(0, total - paid - adj))
    if v("bill.statement_date") is None:
        warnings.append("statement date not found — using upload date")
    bill = Bill(
        account_no=_str(v("bill.account_no"), case_id),
        statement_date=_isod(v("bill.statement_date")) or today,
        lines=blines,
        total_charges_cents=total,
        insurance_paid_cents=paid,
        adjustments_cents=adj,
        patient_balance_cents=balance,
    )

    eob = None
    if "eob" in fieldsets:
        e_ns = sorted(
            {int(m.group(1)) for p in vals for m in [re.match(r"eob\.lines\.(\d+)\.", p)] if m}
        )
        elines = [
            EobLine(
                line_no=n,
                code=_str(v(f"eob.lines.{n}.code"), "?"),
                units=max(0, _int(v(f"eob.lines.{n}.units"), 1)),
                billed_cents=_cents(v(f"eob.lines.{n}.billed_cents")),
                allowed_cents=_cents(v(f"eob.lines.{n}.allowed_cents")),
                plan_paid_cents=_cents(v(f"eob.lines.{n}.plan_paid_cents")),
                patient_resp_cents=_cents(v(f"eob.lines.{n}.patient_resp_cents")),
                carc=_lst(v(f"eob.lines.{n}.carc")),
                remark=_str_or_none(v(f"eob.lines.{n}.remark")),
            )
            for n in e_ns
        ]
        eob = Eob(
            payer=_str(v("eob.payer"), "Unknown payer"),
            claim_id=_str(v("eob.claim_id"), case_id),
            processed_date=_isod(v("eob.processed_date")) or today,
            lines=elines,
            patient_resp_total_cents=_cents(
                v("eob.patient_resp_total_cents"), sum(ln.patient_resp_cents for ln in elines)
            ),
        )

    denial = None
    if "denial" in fieldsets:
        if v("denial.letter_date") is None:
            warnings.append("denial letter date not found — using upload date")
        denial = Denial(
            letter_date=_isod(v("denial.letter_date")) or today,
            carc=_str(v("denial.carc"), "CO-?"),
            category=_cat(v("denial.category", "MISSING_INFO"), warnings),
            reason_text=_str(v("denial.reason_text"), "Denial reason not legible."),
            service_code=_str_or_none(v("denial.service_code")),
            appeal_deadline=_isod(v("denial.appeal_deadline")),
            plan_section=_str_or_none(v("denial.plan_section")),
        )

    plan = PlanSummary(
        plan_name=_str(v("plan.plan_name"), "Unknown plan"),
        plan_type=v("plan.plan_type") if v("plan.plan_type") in ("PPO", "HMO", "EPO", "HDHP") else "PPO",
        deductible_cents=_cents(v("plan.deductible_cents")),
        deductible_met_cents=_cents(v("plan.deductible_met_cents")),
        coinsurance_pct=max(0, _int(v("plan.coinsurance_pct"), 20)),
        copay_cents=_cents(v("plan.copay_cents")),
        oop_max_cents=_cents(v("plan.oop_max_cents")),
        emergency_nsa_protected=_bool(v("plan.emergency_nsa_protected"), True),
    )
    if "plan" not in fieldsets:
        warnings.append("no plan document — default plan terms (deductible $0, 20% coinsurance)")

    patient = Patient(
        patient_id=_str(v("patient.patient_id"), "PT-UPLOAD"),
        dob=_isod(v("patient.dob")),
        date_of_death=_isod(v("patient.date_of_death")),
        member_id=_str_or_none(v("patient.member_id")),
    )
    provider_name = _str(v("provider.name"), "Uploaded provider")
    if provider_name == "Uploaded provider":
        warnings.append("provider name not found — using placeholder")
    provider = Provider(
        name=provider_name,
        npi=_str_or_none(v("provider.npi")),
        in_network=_bool(v("provider.in_network"), True),
        address=_str_or_none(v("provider.address")),
    )

    pos = {ln.pos for ln in blines if ln.pos}
    if "23" in pos:
        setting = "emergency"
    elif "21" in pos:
        setting = "inpatient"
    elif pos & {"19", "22", "24"}:
        setting = "outpatient"
    elif "81" in pos:
        setting = "lab"
    elif blines and all(ln.code.startswith("7") for ln in blines):
        setting = "imaging"
    else:
        setting = "office"

    kinds = [k for k in DOC_KINDS if k in docs]
    title = f"Uploaded {'+'.join(kinds)} · {provider_name}"
    case = Case(
        case_id=case_id,
        split="test",
        title=title,
        patient=patient,
        provider=provider,
        bill=bill,
        eob=eob,
        denial=denial,
        plan=plan,
        service_setting=setting,  # type: ignore[arg-type]
        docs=[
            DocRef(
                kind=k,
                pdf=f"data/cache/uploads/{case_id}/{case_id}_{k}.pdf",
                pages=_npages(docs[k]["blob"]),
            )
            for k in kinds
        ],
        citations=cits,
    )
    return case, len(vals)
