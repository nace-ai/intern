"""Pipeline orchestration for the PA full-chain demo.

prepare_case(slug)  : offline — slice policy pages from the source PDF (char
                      spans), render the synthetic packet PDF + page PNGs.
record_case(slug)   : live Nace/router calls — NDI ground (policy criteria +
                      packet records + request payer/date), Drex Search match,
                      Drex per-fact answers + AND/OR decision, ablation per
                      record, GPT-checked example. Writes evidence.json and
                      check.png. Results cached to run.json for resume.
replay_case(slug)   : rebuilds evidence.json + check.png from run.json only.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from billproof.bench.pa.cases import PaCase
from billproof.bench.pa.logic import compute_scope, decide
from billproof.bench.pa.score import facts_from_answers
from billproof.bench.pa.questions import build_questions
from billproof.pa_demo.evidence import render_check, write_evidence
from billproof.pa_demo.letters import build_letter
from billproof.pa_demo.packet import render_packet
from billproof.pa_demo.showcase import SHOWCASE, SRC_DIR, case_dir, load_row

DPI = 150


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _clause_texts(case: PaCase) -> list[str]:
    """Verbatim clause text of the policy criteria (provenance spans preferred)."""
    spans = (case.provenance or {}).get("criterion_evidence") or []
    texts = []
    policy_text = ""
    for sp in spans:
        policy_text = policy_text or (sp.get("policy_text") or "")
        if sp.get("span") and policy_text:
            s, e = sp["span"]
            texts.append(policy_text[s:e])
    if not texts:
        texts = [c.get("text") or "" for c in case.policy.get("criteria_list") or []]
    return [t for t in texts if t]


def locate_criteria_pages(source_pdf: Path, case: PaCase, *,
                          expected: list[int] | None = None) -> list[int]:
    """Page(s) whose text contains ALL criteria (normalized containment)."""
    from pypdf import PdfReader

    pages_text = [_norm(p.extract_text() or "") for p in PdfReader(str(source_pdf)).pages]
    hits_per_clause: list[set[int]] = []
    for text in _clause_texts(case):
        needle = _norm(text)
        hits = {i + 1 for i, t in enumerate(pages_text) if needle and needle in t}
        hits_per_clause.append(hits)
    inter = set.intersection(*hits_per_clause) if hits_per_clause else set()
    if not inter and hits_per_clause:
        union = set.union(*hits_per_clause)
        inter = union or set(expected or [])
    if expected and set(expected) <= set(inter or expected):
        return sorted(expected)
    return sorted(inter)


def prepare_case(slug: str) -> dict[str, Any]:
    """Slice policy pages, render packet + page PNGs. Offline, idempotent."""
    import pypdfium2 as pdfium
    from pypdf import PdfReader, PdfWriter

    spec = SHOWCASE[slug]
    case = load_row(spec["row_id"])
    dir_ = case_dir(slug)
    (dir_ / "pages").mkdir(parents=True, exist_ok=True)
    src = SRC_DIR / spec["source_pdf"]
    if not src.is_file():
        raise FileNotFoundError(f"source PDF missing: {src} — run pa_demo_prepare --fetch")

    pages = locate_criteria_pages(src, case, expected=spec.get("expected_pages"))
    # slice
    reader, writer = PdfReader(str(src)), PdfWriter()
    for p in pages:
        writer.add_page(reader.pages[p - 1])
    slice_path = dir_ / "policy_slice.pdf"
    with open(slice_path, "wb") as fh:
        writer.write(fh)

    packet_path = render_packet(case, spec, dir_ / "packet.pdf")

    def render_pngs(pdf_path: Path, tag: str) -> list[str]:
        out = []
        pdf = pdfium.PdfDocument(str(pdf_path))
        for i in range(len(pdf)):
            bmp = pdf[i].render(scale=DPI / 72)
            p = pdf_path.parent / "pages" / f"{tag}_p{i+1}.png"
            bmp.to_pil().save(p)
            out.append(str(p))
        pdf.close()
        return out

    render_pngs(slice_path, "policy")
    render_pngs(packet_path, "packet")
    meta = {"criteria_pages": pages, "policy_slice": str(slice_path),
            "packet": str(packet_path), "clause_texts": _clause_texts(case)}
    (dir_ / "prepare.json").write_text(json.dumps(meta, indent=2))
    return meta


def _load_run(dir_: Path) -> dict[str, Any]:
    p = dir_ / "run.json"
    return json.loads(p.read_text()) if p.is_file() else {}


def _save_run(dir_: Path, run: dict[str, Any]) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "run.json").write_text(json.dumps(run, indent=2, default=str))


def record_case(slug: str, *, steps: tuple[str, ...] = (
        "ground", "search", "drex", "ablate", "gpt")) -> dict[str, Any]:
    """Live calls; resumable per step via run.json."""
    spec = SHOWCASE[slug]
    case = load_row(spec["row_id"])
    dir_ = case_dir(slug)
    prep = json.loads((dir_ / "prepare.json").read_text())
    run = _load_run(dir_)
    policy_pdf, packet_pdf = Path(prep["policy_slice"]), Path(prep["packet"])

    if "ground" in steps and "ground" not in run:
        from billproof.adapters.ndi import NdiAdapter
        from billproof.pa_demo.ndi import ground_texts, jobs_cost
        t0 = time.monotonic()
        adapter = NdiAdapter()
        crit_targets = [{"id": c.get("fact"), "text": t}
                        for c, t in zip(case.policy.get("criteria_list") or [],
                                        prep["clause_texts"])]
        pol_res = ground_texts(adapter, policy_pdf, crit_targets)
        rec_targets = [{"id": r.get("id"), "text": r.get("text") or ""} for r in case.records]
        rec_targets += [
            {"id": "request::payer", "text": str(case.request.get("payer") or "")},
            {"id": "request::service_date", "text": str(case.request.get("service_date") or "")},
        ]
        rec_res = ground_texts(adapter, packet_pdf, rec_targets)
        run["ground"] = {"policy": pol_res, "packet": rec_res,
                         "seconds": round(time.monotonic() - t0, 1),
                         "cost_usd": jobs_cost(adapter)}
        _save_run(dir_, run)

    if "search" in steps and "search" not in run:
        from billproof.pa_demo.search import run_match
        run["search"] = run_match(slug, case, {"policy": policy_pdf, "packet": packet_pdf})
        _save_run(dir_, run)

    if "drex" in steps and "drex" not in run:
        from billproof.bench.pa import columns as pc
        t0 = time.monotonic()
        out = pc.drex_column(case)
        out["seconds"] = round(time.monotonic() - t0, 1)
        run["drex"] = out
        _save_run(dir_, run)

    if "ablate" in steps and "ablate" not in run:
        from billproof.bench.pa import columns as pc
        base_dec = (run.get("drex") or {}).get("prediction", {}).get("decision")
        runs = []
        deciding = None
        for rec in case.records:
            rid = rec.get("id")
            state = case.state(drop_records={rid})
            out = pc.drex_column(case, state=state)
            dec = (out.get("prediction") or {}).get("decision")
            runs.append({"dropped": rid, "decision": dec,
                         "changed": dec != base_dec,
                         "latency_ms": out.get("latency_ms"), "cost_usd": out.get("cost_usd")})
            if dec != base_dec and deciding is None:
                deciding = rid
        run["ablate"] = {"runs": runs, "deciding_record": deciding}
        _save_run(dir_, run)

    if "gpt" in steps and "gpt" not in run:
        from billproof.bench.pa import columns as pc
        gpt = pc.gpt_structured(case)
        checked = pc.gpt_checked_column(case, gpt, run.get("drex") or {}, cutoff=0.75)
        run["gpt"] = {"structured": {k: v for k, v in gpt.items()},
                      "checked": {k: v for k, v in checked.items()}}
        _save_run(dir_, run)

    return assemble(slug)


def _ndi_cost(n_targets: int) -> float:
    return round(n_targets * 0.012, 4)  # nace price: $0.012 per target


def assemble(slug: str) -> dict[str, Any]:
    """Build evidence.json + check.png from prepare.json + run.json."""
    spec = SHOWCASE[slug]
    case = load_row(spec["row_id"])
    dir_ = case_dir(slug)
    prep = json.loads((dir_ / "prepare.json").read_text())
    run = _load_run(dir_)
    ground = run.get("ground") or {}
    gpol, gpak = ground.get("policy") or {}, ground.get("packet") or {}
    search = run.get("search")
    drex_out = run.get("drex") or {}
    pred = drex_out.get("prediction") or {}
    facts = pred.get("facts") or drex_out.get("facts") or {}
    scope = pred.get("scope") or compute_scope(case.policy, case.request)
    decision = decide(facts, case.criterion_deps, scope)

    smap = {m.get("criterion"): m for m in (search or {}).get("matches") or []}
    criteria = []
    for item in case.policy.get("criteria_list") or []:
        name = item.get("fact")
        fv = facts.get(name)  # fact-level state: the box colour follows the record finding
        st = "met" if fv is True else "unmet" if fv is False else "missing"
        g = gpol.get(name) or {}
        m = smap.get(name) or {}
        link = None
        if m.get("record_id"):
            rec = next((r for r in case.records if r.get("id") == m["record_id"]), {})
            rg = gpak.get(m["record_id"]) or {}
            link = {"record": m["record_id"], "record_page": rg.get("page") or m.get("page"),
                    "record_polygons": rg.get("polygons") or [],
                    "quote": m.get("quote"), "verbatim": m.get("verbatim"),
                    "search_page": m.get("page"), "page_ok": m.get("page_ok"),
                    "via": "search" if m else "none"}
        else:
            for r in case.records:
                rid = r.get("id")
                if facts.get(name) is not None and _norm(_clause_of(case, name)) and \
                        _norm(_clause_of(case, name))[:60] in _norm(r.get("text") or ""):
                    rg = gpak.get(rid) or {}
                    link = {"record": rid, "record_page": rg.get("page"),
                            "record_polygons": rg.get("polygons") or [],
                            "quote": None, "verbatim": None, "via": "text-match"}
                    break
        criteria.append({"criterion": name, "text": item.get("text"),
                         "state": st,
                         "confidence": ((drex_out.get("qa") or {}).get(f"fact::{name}") or {}).get("confidence"),
                         "page": g.get("page"), "polygons": g.get("polygons") or [],
                         "match_method": g.get("match_method"), "link": link})
    records = []
    for r in case.records:
        rid = r.get("id")
        g = gpak.get(rid) or {}
        records.append({"record": rid, "kind": "record", "page": g.get("page"),
                        "polygons": g.get("polygons") or [],
                        "deciding": rid == (run.get("ablate") or {}).get("deciding_record"),
                        "text": r.get("text")})
    for extra_id in ("request::payer", "request::service_date"):
        g = gpak.get(extra_id) or {}
        if g.get("page"):
            records.append({"record": extra_id, "kind": "request_field",
                            "page": g.get("page"), "polygons": g.get("polygons") or [],
                            "deciding": False, "text": ""})

    timings = {"ground_s": ground.get("seconds"),
               "search_s": (search or {}).get("search_seconds"),
               "drex_ms": drex_out.get("latency_ms"),
               "ablate_ms": sum((x.get("latency_ms") or 0) for x in (run.get("ablate") or {}).get("runs", [])),
               "gpt_ms": ((run.get("gpt") or {}).get("structured") or {}).get("latency_ms")}
    costs = {"ndi_ground": ground.get("cost_usd"),
             "drex_search": (search or {}).get("cost_usd"),
             "drex_decide": (drex_out.get("cost_usd") or 0) + sum(
                 (x.get("cost_usd") or 0) for x in (run.get("ablate") or {}).get("runs", [])),
             "gpt": ((run.get("gpt") or {}).get("structured") or {}).get("cost_usd")}
    gpt_run = run.get("gpt") or {}
    letter = build_letter(case, spec, decision, criteria)
    write_evidence(dir_, slug, spec, case, decision=decision,
                   extra={"criterion_states": decision["criteria"],
                          "missing_info": decision["missing_info"], "facts": facts},
                   drex={k: drex_out.get(k) for k in ("qa", "model", "cost_usd", "latency_ms", "calls", "error")},
                   search=search, ablation=run.get("ablate") or {},
                   gpt_check={"gpt_decision": ((gpt_run.get("structured") or {}).get("prediction") or {}).get("decision"),
                              "checked_decision": ((gpt_run.get("checked") or {}).get("prediction") or {}).get("decision"),
                              "overrides": (gpt_run.get("checked") or {}).get("overrides"),
                              "flagged": (gpt_run.get("checked") or {}).get("flagged")} if gpt_run else None,
                   letter=letter, criteria=criteria, records=records,
                   timings=timings, costs=costs)
    render_check(dir_, case, criteria, records)
    return {"slug": slug, "decision": decision["decision"],
            "gold": case.target.get("decision")}


def _clause_of(case: PaCase, fact: str) -> str:
    for item in case.policy.get("criteria_list") or []:
        if item.get("fact") == fact:
            return item.get("text") or ""
    return ""
