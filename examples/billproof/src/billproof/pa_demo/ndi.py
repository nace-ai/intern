"""NDI ground wrapper: verbatim text in, page polygons out.

Reuses NdiAdapter's upload + job machinery (POST /v1/documents/ground). Each
target is {id, text, page_hint?}; each result is the first match's
page_region: {status, page, polygons (normalized 0-1 top-left), matched_text,
match_method, confidence}. Targets that exact/normalized match misses are
re-ground once with semantic=true (still NDI, flagged in the result).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from billproof.adapters.ndi import NdiAdapter, normalize_polygons, pdf_page_sizes
from billproof.config import CACHE_DIR

_GROUND_CACHE = CACHE_DIR / "ndi" / "pa_ground"


def _cache_key(pdf: Path, targets: list[dict[str, Any]], semantic: bool) -> Path:
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()[:16]
    th = hashlib.sha256(json.dumps(targets, sort_keys=True).encode()).hexdigest()[:12]
    return _GROUND_CACHE / f"{pdf.stem}_{sha}_{th}_{'sem' if semantic else 'ex'}.json"


def _matches_of(job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    result = job.get("result") or {}
    for target in result.get("targets") or []:
        tid = target.get("id")
        matches = target.get("matches") or []
        best = matches[0] if matches else {}
        loc = best.get("location") or {}
        out[str(tid)] = {
            "status": target.get("status"),
            "page": loc.get("page"),
            "polygons_raw": loc.get("polygons") or [],
            "matched_text": best.get("matched_text"),
            "match_method": best.get("match_method"),
            "confidence": best.get("confidence"),
            "match_count": target.get("match_count", len(matches)),
            "error": target.get("error"),
        }
    return out


def _unique_alias(pdf: Path) -> Path:
    """NDI uploads by bare filename; give each case's PDF a workspace-unique name."""
    pdf = Path(pdf)
    alias = pdf.with_name(f"{pdf.parent.name}_{pdf.name}")
    if not alias.exists() or alias.stat().st_size != pdf.stat().st_size:
        try:
            alias.hardlink_to(pdf)
        except OSError:
            import shutil
            shutil.copy2(pdf, alias)
    return alias


def ground_texts(adapter: NdiAdapter, pdf: Path, targets: list[dict[str, Any]],
                 *, allow_semantic: bool = True) -> dict[str, dict[str, Any]]:
    """Ground each target text in `pdf`. Returns {target_id: result}.

    Uses the on-disk cache: re-runs are free (same PDF bytes + targets).
    """
    sizes = pdf_page_sizes(pdf)
    cache = _cache_key(pdf, targets, False)
    upload_pdf = _unique_alias(pdf)
    if cache.is_file():
        first = json.loads(cache.read_text())
    else:
        req = [
            {"id": str(t["id"]), "text": t["text"],
             **({"page_hints": [int(t["page_hint"])]} if t.get("page_hint") else {}),
             **({"hint": str(t["hint"])} if t.get("hint") else {})}
            for t in targets
        ]
        job = adapter._run_job("ground", {
            "source": adapter._source_for_path(upload_pdf),
            "targets": req,
            "options": {"max_matches": 1, "include_previews": False},
        })
        first = _matches_of(job)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(first))

    misses = [t for t in targets
              if (first.get(str(t["id"])) or {}).get("status") != "found" or
              not (first.get(str(t["id"])) or {}).get("polygons_raw")]
    if misses and allow_semantic:
        scache = _cache_key(pdf, misses, True)
        if scache.is_file():
            second = json.loads(scache.read_text())
        else:
            req = [{"id": str(t["id"]), "text": t["text"], "semantic": True,
                    **({"page_hints": [int(t["page_hint"])]} if t.get("page_hint") else {})}
                   for t in misses]
            job = adapter._run_job("ground", {
                "source": adapter._source_for_path(upload_pdf),
                "targets": req,
                "options": {"max_matches": 1, "include_previews": False},
            })
            second = _matches_of(job)
            scache.write_text(json.dumps(second))
        for tid, res in second.items():
            if res.get("status") == "found":
                res["semantic_fallback"] = True
                first[tid] = res

    for tid, res in first.items():
        size = sizes.get(res.get("page") or -1)
        res["polygons"] = normalize_polygons(res.pop("polygons_raw", []),
                                             page_width=size[0] if size else None,
                                             page_height=size[1] if size else None)
    return first


def jobs_cost(adapter: NdiAdapter) -> float:
    return round(sum(j.get("cost_usd") or 0.0 for j in adapter.jobs), 5)
