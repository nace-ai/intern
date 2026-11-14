"""PDF page rendering + citation crops via pypdfium2, cached under data/cache/pages/."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium

from billproof.config import CACHE_DIR, PKG_ROOT, SYN_DIR

DOC_KINDS = ("bill", "eob", "denial", "plan")
PAGES_DIR = CACHE_DIR / "pages"
PAD_PX = 12
SCALE_MIN, SCALE_MAX = 0.25, 6.0


def clamp_scale(scale: float) -> float:
    try:
        return min(max(float(scale), SCALE_MIN), SCALE_MAX)
    except (TypeError, ValueError):
        return 2.0


def pdf_for(case_id: str, doc: str, case: Any = None) -> Path | None:
    """Locate the PDF for (case, doc): the case's DocRef, else the conventional W4 path."""
    if doc not in DOC_KINDS:
        return None
    if case is not None:
        for d in getattr(case, "docs", []) or []:
            if d.kind == doc:
                p = PKG_ROOT / d.pdf
                if p.exists():
                    return p
    p = SYN_DIR / "pdf" / f"{case_id}_{doc}.pdf"
    return p if p.exists() else None


def doc_pages(case_id: str, case: Any = None) -> dict[str, int]:
    """{doc: page_count} from DocRefs, back-filled by probing the conventional pdf paths."""
    pages: dict[str, int] = {}
    if case is not None:
        for d in getattr(case, "docs", []) or []:
            if d.kind in DOC_KINDS:
                pages[d.kind] = d.pages
    for doc in DOC_KINDS:
        if doc in pages:
            continue
        p = pdf_for(case_id, doc, None)
        if p is not None:
            pages[doc] = count_pages(p)
    return pages


def count_pages(pdf_path: Path) -> int:
    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            return len(pdf)
        finally:
            pdf.close()
    except Exception:
        return 1


def render_page(pdf_path: Path, page: int, scale: float):
    """One page -> PIL image. Raises IndexError for out-of-range pages."""
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        n = len(pdf)
        if page < 1 or page > n:
            raise IndexError(f"page {page} out of range 1..{n}")
        bmp = pdf[page - 1].render(scale=scale)
        return bmp.to_pil().convert("RGB")
    finally:
        pdf.close()


def page_png(pdf_path: Path, case_id: str, doc: str, page: int, scale: float) -> Path:
    v = int(pdf_path.stat().st_mtime)  # re-rendered PDFs get fresh images
    out = PAGES_DIR / case_id / doc / f"p{page}@s{scale:g}-{v}.png"
    if not out.exists():
        img = render_page(pdf_path, page, scale)
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, "PNG")
    return out


def parse_poly(raw: str) -> list[tuple[float, float]] | None:
    """Accept `[[x,y],...]` JSON or a flat `x1,y1,x2,y2,...` list (normalized coords)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        v = json.loads(raw)
        if isinstance(v, list) and v and isinstance(v[0], (list, tuple)):
            return [(float(p[0]), float(p[1])) for p in v]
        if isinstance(v, list) and all(isinstance(x, (int, float)) for x in v):
            raw = ",".join(str(x) for x in v)
    except Exception:
        pass
    try:
        nums = [float(t) for t in raw.split(",")]
        if len(nums) >= 4 and len(nums) % 2 == 0:
            return list(zip(nums[::2], nums[1::2]))
    except Exception:
        pass
    return None


def crop_png(
    pdf_path: Path,
    case_id: str,
    doc: str,
    page: int,
    poly: list[tuple[float, float]],
    scale: float,
    pad: int = PAD_PX,
) -> Path:
    """Crop the bbox of a normalized polygon (pad px on the rendered image)."""
    key = hashlib.sha1(
        f"{case_id}|{doc}|{page}|{json.dumps(poly)}|{scale:g}|{pad}|{int(pdf_path.stat().st_mtime)}".encode()
    ).hexdigest()[:16]
    out = PAGES_DIR / "crops" / f"{key}.png"
    if out.exists():
        return out
    img = render_page(pdf_path, page, scale)
    w, h = img.size
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    if max(xs + ys) > 1.5:  # caller passed pixels of this rendered image, not [0,1]
        xs = [x / w for x in xs]
        ys = [y / h for y in ys]
    box = (
        max(0, int(min(xs) * w) - pad),
        max(0, int(min(ys) * h) - pad),
        min(w, int(max(xs) * w + 0.999) + pad),
        min(h, int(max(ys) * h + 0.999) + pad),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("empty crop region")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.crop(box).save(out, "PNG")
    return out
