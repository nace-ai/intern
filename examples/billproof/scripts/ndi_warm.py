#!/usr/bin/env python3
"""Warm the NDI caches for the demo cases and grade extraction quality.

Only the demo cases' *key evidence* is warmed — extract per document plus a
ground call per unique fired-flag evidence text (a few targets per case, not
the ~390 citations of the old every-citation pass).

  .venv/bin/python scripts/ndi_warm.py                      # SYN-D01..D06
  .venv/bin/python scripts/ndi_warm.py --case SYN-D01,SYN-D02
  .venv/bin/python scripts/ndi_warm.py --refresh --grounds all --max-usd 1.5

A cost estimate prints BEFORE anything is billed; the run aborts when the
estimate (or the running total) exceeds --max-usd. Disk-cached extracts and
grounds are $0 and labelled as such. Metrics land in data/metrics/ndi_*.json.

event_day.py delegates here: `ndi-warm` -> this file's main(argv).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BILLPROOF_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BILLPROOF_ROOT / "src"))

from billproof.config import DATA_DIR, SYN_DIR, settings  # noqa: E402

DEMO_CASES = [f"SYN-D{i:02d}" for i in range(1, 7)]
_DOC_KINDS = ("bill", "eob", "denial", "plan")
PRICE_EXTRACT_PAGE = 0.012  # low effort, $12 / 1k pages
PRICE_GROUND_TARGET = 0.012  # $12 / 1k targets
PRICE_SPLIT_PAGE = 0.005


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _doc_kinds(case_id: str, case) -> list[str]:
    kinds = {d.kind for d in getattr(case, "docs", []) or [] if d.kind in _DOC_KINDS}
    for kind in _DOC_KINDS:
        if (SYN_DIR / "pdf" / f"{case_id}_{kind}.pdf").exists():
            kinds.add(kind)
    return [k for k in _DOC_KINDS if k in kinds]


def _field_family(path: str) -> str:
    parts = str(path).split(".")
    if len(parts) > 1 and parts[1] == "lines":
        return f"{parts[0]}.lines"
    return parts[0]


def _key_evidence_citations(case) -> list:
    """Citations behind the flags that fired — the boxes that glow on stage."""
    from billproof.rules.checks import run_checks

    flags = run_checks(case)
    keys = [k for f in flags if f.fired for k in f.evidence]
    if not keys:  # clean case: still ground one evidence text per evaluated flag family
        keys = [k for f in flags for k in f.evidence][:6]
    out, seen = [], set()
    for k in keys:
        cit = (case.citations or {}).get(k)
        if cit is None or not (cit.text or "").strip():
            continue
        sig = (cit.doc, cit.page, cit.text.strip())
        if sig in seen:
            continue
        seen.add(sig)
        out.append(cit)
    return out


def _write_metric(name: str, value, n: int, source_run: str, simulated: bool, extra: dict | None = None) -> None:
    path = DATA_DIR / "metrics" / f"ndi_{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "value": value,
        "n": n,
        "source_run": source_run,
        "simulated": simulated,
        "computed_at": _utc_now(),
    }
    if extra:
        row.update(extra)
    path.write_text(json.dumps(row, indent=2) + "\n")


def _est(op: str, units: float) -> float:
    if op == "extract":
        return units * PRICE_EXTRACT_PAGE
    if op == "ground":
        return units * PRICE_GROUND_TARGET
    if op == "split":
        return units * PRICE_SPLIT_PAGE
    return 0.0


def cmd_warm(args: argparse.Namespace) -> int:
    from billproof.adapters import ndi as ndi_mod
    from billproof.data import load_case

    try:
        live = settings().use_real("ndi")
        adapter = ndi_mod.get_ndi()
    except RuntimeError as exc:
        print(f"error: {exc}")
        return 2
    simulated = bool(getattr(adapter, "simulated", not live))
    print("MODE:", "SIMULATED — fixtures, nothing is billed" if simulated else "LIVE — real NDI calls")
    if not live and not args.allow_simulated:
        print("\nrefusing: no NDI credentials in .env — a warm run would only re-read fixtures.")
        print("Fill .env and re-run, or pass --allow-simulated.")
        return 2

    case_ids = [] if args.only else list(DEMO_CASES)
    for raw in args.case + args.only:
        for cid in str(raw).split(","):
            cid = cid.strip()
            if cid and cid not in case_ids:
                case_ids.append(cid)

    # ------------------------------------------------ plan + cost estimate
    plans = []  # {case_id, case, docs: [(doc, pdf, pages, cached)], grounds: [Citation]}
    est_total = 0.0
    n_extract = n_cached = n_ground = n_ground_cached = 0
    for case_id in case_ids:
        try:
            case = load_case(case_id)
        except Exception:
            print(f"{case_id}: no case file — skipped")
            continue
        docs = []
        for doc in _doc_kinds(case_id, case):
            try:
                pdf = ndi_mod.resolve_pdf(case_id, doc)
            except FileNotFoundError:
                continue
            pages = len(ndi_mod.pdf_page_sizes(pdf)) or 1
            cached = (
                ndi_mod.extract_cache_path(case_id, doc, pdf).is_file() and not args.refresh
            ) or simulated
            if not cached:
                est_total += _est("extract", pages)
                n_extract += 1
            else:
                n_cached += 1
            docs.append((doc, pdf, pages, cached))
        grounds = []
        if args.grounds != "none":
            cits = _key_evidence_citations(case)
            if args.grounds == "key":
                cits = cits[: args.max_grounds]
            for cit in cits:
                g_cached = simulated or ndi_mod.ndi_cache_path(case_id, cit).is_file()
                if g_cached:
                    n_ground_cached += 1
                else:
                    est_total += _est("ground", 1)
                    n_ground += 1
                grounds.append((cit, g_cached))
        plans.append({"case_id": case_id, "case": case, "docs": docs, "grounds": grounds})

    print(f"\n== cost estimate (low-effort prices; cache hits $0) ==")
    print(
        f"  extract: {n_extract} doc(s) fresh (+{n_cached} cached) · "
        f"ground: {n_ground} target(s) fresh (+{n_ground_cached} cached)"
    )
    print(f"  estimated bill: ${est_total:.3f}   cap: "
          + (f"${args.max_usd:.2f}" if args.max_usd is not None else "none"))
    if args.max_usd is not None and est_total > args.max_usd:
        print(f"ABORT: estimate ${est_total:.3f} exceeds --max-usd ${args.max_usd:.2f} before any spend.")
        return 3

    # ------------------------------------------------ run
    fam_stats: dict[str, list[int]] = {}
    misses: list[dict] = []
    spent = 0.0
    errors = 0
    jobs_before = len(adapter.jobs) if hasattr(adapter, "jobs") else 0
    run_id = f"ndi_warm_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    found_total = cites_total = scored_total = match_total = 0
    fields_docs = 0
    fresh_secs: list[float] = []  # wall-clock seconds of fresh (billed) extracts
    fresh_pages: list[int] = []
    fresh_grd_secs: list[float] = []

    def charge(job_index_before: int, op: str, units: float) -> float:
        """Observed cost of the job(s) just appended, else the price-table estimate."""
        if not hasattr(adapter, "jobs") or len(adapter.jobs) <= job_index_before:
            return _est(op, units)
        observed = adapter.jobs[-1].get("cost_usd")
        return float(observed) if observed is not None else _est(op, units)

    print(f"\n{'case':<9} {'doc':<7} {'fields':>7} {'cites':>6} {'sec':>7} {'est $':>8}")
    stopped = False
    for plan in plans:
        if stopped:
            break
        case_id, case = plan["case_id"], plan["case"]
        for doc, pdf, pages, cached in plan["docs"]:
            jb = len(adapter.jobs) if hasattr(adapter, "jobs") else 0
            t0 = time.monotonic()
            try:
                fields = adapter.extract(case_id, doc, refresh=args.refresh and not simulated)
            except Exception as exc:
                errors += 1
                print(f"{case_id:<9} {doc:<7} extract FAILED: {exc}")
                continue
            secs = time.monotonic() - t0
            cost = 0.0 if cached else charge(jb, "extract", pages)
            spent += cost
            if not cached:
                fresh_secs.append(secs)
                fresh_pages.append(pages)
            found = sum(1 for f in fields if f.get("status") == "found")
            cites = sum(len(f.get("citations") or []) for f in fields)
            fields_docs += 1
            found_total += found
            cites_total += cites
            verdict = ndi_mod.score_fields(case, fields, case_id=case_id, doc=doc)
            scored_total += verdict["scored"]
            match_total += verdict["matched"]
            for row in verdict["rows"]:
                fam = fam_stats.setdefault(_field_family(row["path"]), [0, 0])
                fam[1] += 1
                fam[0] += int(row["match"])
                if not row["match"]:
                    misses.append({"case": case_id, "doc": doc, **row})
            print(
                f"{case_id:<9} {doc:<7} {found:>3}/{len(fields):<3} {cites:>6} {secs:>6.1f} ${cost:>7.4f}"
                + ("  (cache)" if cached else "")
            )
            if args.max_usd is not None and spent > args.max_usd:
                print(f"BUDGET exceeded (${spent:.3f} > ${args.max_usd:.2f}); stopping before grounds.")
                stopped = True
                break
        for cit, g_cached in plan["grounds"]:
            if stopped:
                break
            jb = len(adapter.jobs) if hasattr(adapter, "jobs") else 0
            t0 = time.monotonic()
            try:
                blob = adapter.ground(case_id, cit)
            except Exception:
                errors += 1
                continue
            secs = time.monotonic() - t0
            cost = 0.0 if g_cached else charge(jb, "ground", 1)
            spent += cost
            if not g_cached:
                fresh_grd_secs.append(secs)
            if blob is None:
                print(f"{case_id:<9} {cit.doc:<7} ground MISS: {str(cit.text)[:48]!r}")
            if args.max_usd is not None and spent > args.max_usd:
                print(f"BUDGET exceeded (${spent:.3f}); stopping.")
                stopped = True
                break

    # ------------------------------------------------ report + metrics
    jobs = (adapter.jobs or [])[jobs_before:] if hasattr(adapter, "jobs") else []
    print(f"\n== NDI extract vs Case values (leaf-cell exact match{' · SIMULATED' if simulated else ''}) ==")
    for fam in sorted(fam_stats):
        m, n = fam_stats[fam]
        print(f"  {fam:<14} {m:>3}/{n:<3}  {m / n:>6.1%}")
    if scored_total:
        print(f"  {'ALL':<14} {match_total:>3}/{scored_total:<3}  {match_total / scored_total:>6.1%}"
              f"   <- \"NDI read {match_total / scored_total:.0%} of printed fields exactly\"")
    if misses:
        show = misses[:12]
        print(f"  misses ({len(misses)}): " + "; ".join(f"{m['case']} {m['path']}" for m in show)
              + (" …" if len(misses) > len(show) else ""))
    print(f"\nspent this run: ${spent:.3f} (reported) · jobs: {len(jobs)}" +
          (f" of ${args.max_usd:.2f}" if args.max_usd is not None else ""))
    if simulated:
        print("SIMULATED: fields came from fixtures, not the NDI API — never quote as measured.")
        return 1 if errors else 0

    ext_jobs = [j for j in jobs if j["op"] == "extract"]
    grd_jobs = [j for j in jobs if j["op"] == "ground"]
    meta = {"run_id": run_id, "cases": case_ids, "grounds_mode": args.grounds}
    if fresh_secs:
        _write_metric(
            "extract_seconds_per_doc",
            round(sum(fresh_secs) / len(fresh_secs), 2),
            len(fresh_secs), run_id, False, meta | {"total_seconds": round(sum(fresh_secs), 1)},
        )
        _write_metric(
            "extract_seconds_per_page",
            round(sum(fresh_secs) / max(1, sum(fresh_pages)), 2),
            sum(fresh_pages), run_id, False, meta,
        )
    if fresh_grd_secs:
        _write_metric(
            "ground_seconds_per_target",
            round(sum(fresh_grd_secs) / len(fresh_grd_secs), 2),
            len(fresh_grd_secs), run_id, False, meta,
        )
    if fields_docs:
        _write_metric("fields_read", found_total, fields_docs, run_id, False, meta)
        _write_metric(
            "citations_per_field",
            round(cites_total / max(1, found_total), 3),
            found_total, run_id, False, meta | {"citations_total": cites_total},
        )
    if scored_total:
        _write_metric(
            "exact_match",
            round(match_total / scored_total, 4),
            scored_total, run_id, False,
            meta | {"matched": match_total, "misses": len(misses)},
        )
    _write_metric(
        "spend_usd",
        round(spent, 4),
        len(jobs), run_id, False,
        meta | {"extract_jobs": len(ext_jobs), "ground_jobs": len(grd_jobs), "max_usd": args.max_usd},
    )
    print(f"metrics -> {DATA_DIR / 'metrics'}/ndi_*.json (run {run_id})")
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", action="append", default=[],
                        help="extra case id to warm (repeatable or comma-separated)")
    parser.add_argument("--only", action="append", default=[],
                        help="warm ONLY these case ids, not the demo six")
    parser.add_argument("--refresh", action="store_true", help="bypass the on-disk extract cache")
    parser.add_argument("--grounds", choices=("key", "all", "none"), default="key",
                        help="ground key-evidence citations (key), every case citation (all), or none")
    parser.add_argument("--max-grounds", type=int, default=12,
                        help="per-case cap on 'key' ground targets (default 12)")
    parser.add_argument("--max-usd", type=float, default=1.5,
                        help="abort when the estimate or running total exceeds this (default 1.5)")
    parser.add_argument("--allow-simulated", action="store_true",
                        help="permit the run with no keys (fixture fields; output SIMULATED)")
    args = parser.parse_args(argv)
    return cmd_warm(args)


if __name__ == "__main__":
    raise SystemExit(main())
