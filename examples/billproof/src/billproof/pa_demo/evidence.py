"""evidence.json assembly + static check PNG (boxes drawn over page images).

Page images: data/pa_demo/<slug>/pages/{policy,packet}_p<n>.png (rendered at
prepare time, PNGs are what pa_routes serves). Boxes are NDI-normalized 0-1
polygons scaled to pixel coords. Criterion colours: met green, unmet red,
missing amber; record boxes blue; links drawn on the check PNG.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from billproof.bench.pa.cases import PaCase
from billproof.bench.pa.logic import decide

_STATE_COLOR = {"met": "#1D9E4B", "unmet": "#D0342C", "missing": "#E8A020"}
_RECORD_COLOR = "#2E62C4"


def _scale(polys: list[list[list[float]]], w: int, h: int) -> list[list[tuple[float, float]]]:
    return [[(x * w, y * h) for x, y in poly] for poly in polys]


def _draw_polys(img: Image.Image, polys, color: str, label: str, *, width: int = 3) -> None:
    if not polys:
        return
    draw = ImageDraw.Draw(img, "RGBA")
    fill = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5)) + (46,)
    for poly in polys:
        pts = [(float(x), float(y)) for x, y in poly]
        if len(pts) >= 2:
            draw.polygon(pts, outline=color, fill=fill, width=width)
    x0 = min(p[0] for poly in polys for p in poly)
    y0 = max(0, min(p[1] for poly in polys for p in poly) - 14)
    draw.rectangle([x0, y0, x0 + max(40, len(label) * 7), y0 + 13], fill=color)
    draw.text((x0 + 3, y0 + 1), label, fill="white")


def page_image(dir_: Path, doc: str, page: int | None) -> Path | None:
    if not page:
        return None
    p = dir_ / "pages" / f"{doc}_p{page}.png"
    return p if p.is_file() else None


def render_check(dir_: Path, case: PaCase, criteria: list[dict], records: list[dict]) -> Path:
    """Static PNG: policy page | packet page(s), boxes drawn for eyeballing."""
    dir_ = Path(dir_)
    imgs: list[tuple[str, Image.Image]] = []
    seen: set[tuple[str, int]] = set()
    for c in criteria:
        img = page_image(dir_, "policy", c.get("page"))
        if img and ("p", c["page"]) not in seen:
            seen.add(("p", c["page"]))
            im = Image.open(img).convert("RGB")
            W, H = im.size
            for cc in criteria:
                if cc.get("page") == c["page"]:
                    _draw_polys(im, _scale(cc.get("polygons") or [], W, H),
                                _STATE_COLOR[cc["state"]], cc["criterion"][:28])
            imgs.append((f"policy p{c['page']}", im))
    for r in records:
        img = page_image(dir_, "packet", r.get("page"))
        if img and ("k", r.get("page")) not in seen:
            seen.add(("k", r.get("page")))
            im = Image.open(img).convert("RGB")
            W, H = im.size
            for rr in records:
                if rr.get("page") == r.get("page") and rr.get("polygons"):
                    label = rr["record"].replace("request::", "req ")
                    _draw_polys(im, _scale(rr.get("polygons") or [], W, H),
                                _RECORD_COLOR, label)
            imgs.append((f"packet p{r.get('page')}", im))
    if not imgs:
        stub = Image.new("RGB", (640, 120), "#333")
        ImageDraw.Draw(stub).text((12, 48), "no page images", fill="white")
        out = dir_ / "check.png"
        stub.save(out)
        return out
    target_h = 1000
    scaled = []
    for label, im in imgs:
        w = int(im.size[0] * target_h / im.size[1])
        scaled.append((label, im.resize((w, target_h))))
    gap = 26
    total_w = sum(im.size[0] for _, im in scaled) + gap * (len(scaled) + 1)
    canvas = Image.new("RGB", (total_w, target_h + 56 + gap), "#F4F1EC")
    d = ImageDraw.Draw(canvas)
    x = gap
    for label, im in scaled:
        d.text((x, gap - 18), label, fill="#333")
        canvas.paste(im, (x, gap))
        x += im.size[0] + gap
    out = dir_ / "check.png"
    canvas.save(out)
    return out


def write_evidence(dir_: Path, slug: str, spec: dict, case: PaCase, *,
                   decision: dict, drex: dict, search: dict | None,
                   ablation: dict, gpt_check: dict | None, letter: dict | None,
                   criteria: list[dict], records: list[dict], timings: dict,
                   costs: dict, extra: dict | None = None) -> Path:
    """Assemble <slug>/evidence.json."""
    dir_ = Path(dir_)
    gold = case.target.get("decision")
    provenance = case.provenance or {}
    policy_url = (provenance.get("policy_source") or {}).get("url")
    pages = {
        "policy": sorted({c["page"] for c in criteria if c.get("page")}),
        "packet": sorted({r["page"] for r in records if r.get("page")}),
    }
    ev = {
        "case_id": slug, "row_id": case.row_id, "title": spec["title"],
        "drug": spec["drug"], "beat": spec["beat"], "note": spec["note"],
        "payer": case.policy.get("payer"), "policy_id": case.policy.get("id"),
        "request": case.request, "connective": case.connective,
        "scenario": case.scenario, "service_date": case.request.get("service_date"),
        "policy_pages": pages["policy"], "packet_pages": pages["packet"],
        "gold_decision": gold, "decision": decision["decision"],
        "decision_action": decision["action"], "scope": decision["scope"],
        "correct": decision["decision"] == gold,
        "criteria": criteria, "records": records,
        "drex": drex, "deciding_record": ablation.get("deciding_record"),
        "ablation": ablation.get("runs", []),
        "search": search, "gpt_check": gpt_check, "letter": letter,
        "timings": timings, "costs": costs,
        "total_cost_usd": round(sum(v for v in costs.values() if v), 5),
        "provenance": {
            "dataset": "Hammer Labs criteria-gym policy_checklist (validation split, hackathon demo use)",
            "row_id": case.row_id, "policy_source_url": policy_url,
            "policy_document_title": case.policy.get("document_title"),
            "source_pdf": spec["source_pdf"],
            "char_spans": provenance.get("criterion_evidence"),
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }
    if extra:
        ev.update(extra)
    out = dir_ / "evidence.json"
    dir_.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ev, indent=2, default=str))
    return out
