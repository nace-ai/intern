"""Compose rules, decision lanes, verdicts and the Drex-selected letter."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from billproof.schema import Case, CaseAnalysis, DecisionResult, GoldItem, Lane, Question, Verdict


def analyze_case(
    case: Case, *, lanes: tuple[Lane, ...] = ("frontier", "drex", "hybrid"),
    include_gold: bool = False,
) -> CaseAnalysis:
    from billproof.data import load_gold

    try:
        gold = load_gold(case.case_id)
    except (FileNotFoundError, KeyError):
        # A caller-created case need not be in the persisted label set.
        from billproof.tamper import gold_for_case
        gold = gold_for_case(case)
    return _analyze(case, gold, lanes=lanes, include_gold=include_gold)


def analyze_with_gold(
    case: Case, gold: dict[str, GoldItem], *,
    lanes: tuple[Lane, ...] = ("frontier", "drex", "hybrid"),
    include_gold: bool = True,
) -> CaseAnalysis:
    """Analyze an in-memory tamper result without looking up its new ID on disk."""
    return _analyze(case, gold, lanes=lanes, include_gold=include_gold)


def _analyze(
    case: Case, gold: dict[str, GoldItem], *, lanes: tuple[Lane, ...], include_gold: bool,
) -> CaseAnalysis:
    from billproof.adapters.drex import get_drex
    from billproof.decisions.lanes import run_lane
    from billproof.decisions.questions import build_questions, build_state
    from billproof.decisions.verdicts import compute_verdict
    from billproof.rules.checks import run_checks

    if not lanes or len(set(lanes)) != len(lanes) or any(
        lane not in ("frontier", "drex", "hybrid") for lane in lanes
    ):
        raise ValueError("lanes must contain unique frontier, drex or hybrid names")
    flags = run_checks(case)
    questions = build_questions(case, flags)
    state = build_state(case, flags)
    results = {
        lane: run_lane(lane, case, questions, state, gold=gold) for lane in lanes
    }
    verdicts = {
        lane: compute_verdict(case, flags, result.answers)
        for lane, result in results.items()
    }
    letter = None
    if "hybrid" in verdicts:
        from billproof.letters.assembler import assemble_letter, letter_questions

        verdict = verdicts["hybrid"]
        lquestions = letter_questions(case, verdict)
        adapter = get_drex()
        # Letter selections derive from the adjudicated verdict, never billing gold.
        lstate = {**state, "verdict": verdict.model_dump(mode="json")}
        kwargs = {"gold": _letter_gold(case, verdict, lquestions)} if adapter.simulated else {}
        answers = adapter.decide(lstate, lquestions, case_id=case.case_id, **kwargs).answers if lquestions else {}
        letter = assemble_letter(case, verdict, answers)
    return CaseAnalysis(
        case_id=case.case_id, flags=flags, questions=questions,
        lanes=results, verdicts=verdicts, letter=letter,
        gold=gold if include_gold else None,
    )


def _letter_gold(case: Case, verdict: Verdict, questions: list[Question]) -> dict[str, GoldItem]:
    """Simulator targets derived solely from the verdict and available evidence."""
    letter_type = {
        "DISPUTE": "dispute", "APPEAL": "appeal", "OK": "none",
        "ASK_PATIENT": "itemized_request", "INSUFFICIENT_EVIDENCE": "itemized_request",
    }[verdict.case_action.value]
    grounds = {
        "DUPLICATE": "duplicate_charge", "MUE_UNITS_EXCEEDED": "mue_units",
        "NCCI_UNBUNDLED": "ncci_unbundling", "OVER_FEE_SCHEDULE": "fee_schedule",
        "EOB_BILL_MISMATCH": "eob_mismatch", "SERVICE_AFTER_DEATH": "after_death",
        "MISSING_DATA": "missing_documentation", "NO_SURPRISES_ACT": "nsa_balance_bill",
    }
    result = {}
    for q in questions:
        options = q.options()
        if q.name == "letter_type":
            target = letter_type
        elif q.name == "letter_recipient":
            target = "insurer" if letter_type == "appeal" else "provider_billing"
        elif q.name.startswith("ground_L"):
            number = q.line_no or int(q.name.removeprefix("ground_L"))
            line = next((v for v in verdict.lines if v.line_no == number), None)
            reasons = line.reasons if line else []
            targets = [item for reason in reasons for item in (reason, grounds.get(reason, ""))]
            if line and line.action.value == "APPEAL" and case.denial:
                targets.extend([case.denial.category.value, case.denial.category.value.lower()])
            target = next((v for v in targets if v in options), "none")
        elif q.name.startswith("evidence_"):
            target = "yes" if any(key in case.citations for key in q.evidence) else "no"
        else:
            raise ValueError(f"Unknown letter question {q.name}")
        if target not in options:
            target = "none" if "none" in options else options[0]
        result[q.name] = GoldItem(answer=target, difficulty=0.0)
    return result


# ================================================================ showcase demo
#
# Staged end-to-end flow for the stage demo:
#   upload  -> locate the packet PDFs (local files)
#   read    -> NDI split/extract/doccheck (fixture, measured cache, or live)
#   rules   -> deterministic checks (always code, never simulated)
#   decide  -> the hybrid lane (Drex first, uncertain escalations verified)
#   ask     -> "ask your documents" (Drex Search; shimmed while T3 lands)
#   browser -> external evidence check (Browser runs; shimmed while T3 lands)
#   verdict -> code-computed verdict and dollars
#   act     -> the Drex-assembled letter
#
# Every emitted event has the SAME stable field set:
#   stage, status, color, key_number {value, unit, label}, duration_ms,
#   elapsed_ms, mode, simulated, provenance, data
# so the UI never has to guess. mode="replay" is fully offline; mode="live"
# only runs against configured credentials and reports blocked stages instead
# of silently falling back to simulation.

DEMO_STAGES: tuple[str, ...] = (
    "upload", "read", "rules", "decide", "ask", "browser", "verdict", "act",
)

_STAGE_COLORS: dict[str, str] = {
    "upload": "#7aa2ff",
    "read": "#38cbe0",
    "rules": "#f5c542",
    "decide": "#34d399",
    "ask": "#a78bfa",
    "browser": "#fb923c",
    "verdict": "#f472b6",
    "act": "#4ade80",
}

_STAGE_LABELS: dict[str, str] = {
    "upload": "Packet",
    "read": "Read (NDI)",
    "rules": "Rules",
    "decide": "Decide (Drex)",
    "ask": "Ask your documents",
    "browser": "External check",
    "verdict": "Verdict",
    "act": "Letter",
}

_DOC_KINDS: tuple[str, ...] = ("bill", "eob", "denial", "plan")

_PROV_FILE = "file"
_PROV_FIXTURE = "fixture"
_PROV_SIMULATOR = "simulator"
_PROV_CACHE = "cache"
_PROV_LIVE = "live"
_PROV_CODE = "code"
_PROV_MANIFEST = "showcase-manifest"
_PROV_UNAVAILABLE = "unavailable"


@dataclass
class _DemoRun:
    """Mutable scratch state shared by the showcase stages."""

    case: Case
    mode: str
    manifest: dict | None = None
    t0: float = field(default_factory=time.monotonic)
    gold: dict[str, GoldItem] | None = None
    documents: list[dict[str, Any]] = field(default_factory=list)
    fields_by_doc: dict[str, list[dict]] = field(default_factory=dict)
    flags: list = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    state: dict = field(default_factory=dict)
    decision: DecisionResult | None = None
    verdict: Verdict | None = None
    letter: Any = None
    letter_support: list[dict] = field(default_factory=list)
    statuses: dict[str, str] = field(default_factory=dict)

    def source_id(self) -> str:
        """The case_id gold and fixtures are keyed under."""
        src = (self.manifest or {}).get("source_case_id")
        return str(src) if src else self.case.case_id


def _prov(kind: str, detail: str = "") -> dict[str, str]:
    return {"kind": kind, "detail": detail}


def _key(value: Any, unit: str, label: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "label": label}


def _event(
    run: _DemoRun, stage: str, status: str, started: float, *,
    key_number: dict | None = None, simulated: bool = False,
    provenance: dict | None = None, data: dict | None = None,
) -> dict:
    now = time.monotonic()
    return {
        "stage": stage,
        "stage_label": _STAGE_LABELS.get(stage, stage),
        "status": status,
        "color": ("amber" if status in {"missing", "blocked", "error"} else "neutral"),
        "key_number": key_number or _key(None, "", "—"),
        "duration_ms": round((now - started) * 1000.0, 1) if status != "running" else None,
        "elapsed_ms": round((now - run.t0) * 1000.0, 1),
        "mode": run.mode,
        "simulated": simulated,
        "provenance": provenance or _prov(_PROV_CODE),
        "data": data or {},
    }


def iter_demo_events(
    case: Case, *, mode: str = "replay", manifest: dict | None = None,
) -> Iterator[dict]:
    """Yield the stable stage events for one showcase run.

    mode="replay": fully offline — measured caches first, fixtures and seeded
    simulators after that, every event honestly labelled. mode="live": opt-in
    real calls; any stage without credentials or its module reports
    ``blocked``/``missing`` instead of silently simulating.
    """
    if mode not in ("replay", "live"):
        raise ValueError("mode must be 'replay' or 'live'")
    run = _DemoRun(case=case, mode=mode, manifest=manifest or {})
    run.gold = _demo_gold(run)
    for stage in DEMO_STAGES:
        started = time.monotonic()
        yield _event(run, stage, "running", started)
        try:
            event = _STAGE_FUNCS[stage](run, started)
        except Exception as exc:  # a stage failure must not kill the stream
            event = _event(
                run, stage, "error", started,
                provenance=_prov(_PROV_UNAVAILABLE, "stage raised"),
                data={"error": f"{type(exc).__name__}: {exc}"},
            )
        run.statuses[stage] = event["status"]
        yield event


def run_showcase(
    case: Case, *, mode: str = "replay", manifest: dict | None = None,
) -> dict:
    """Non-streaming variant: run all stages and return events + summary."""
    events = list(iter_demo_events(case, mode=mode, manifest=manifest))
    return {"events": events, "complete": _complete_payload(case, mode, events)}


def _complete_payload(case: Case, mode: str, events: list[dict]) -> dict:
    done = [e for e in events if e["status"] != "running"]
    by_stage = {e["stage"]: e for e in done}
    verdict = (by_stage.get("verdict") or {}).get("data", {}).get("verdict")
    letter = (by_stage.get("act") or {}).get("data", {}).get("letter")
    return {
        "case_id": case.case_id,
        "mode": mode,
        "stages": {e["stage"]: e["status"] for e in done},
        "key_numbers": {e["stage"]: e["key_number"] for e in done},
        "simulated": any(e["simulated"] for e in done),
        "total_ms": done[-1]["elapsed_ms"] if done else 0.0,
        "verdict": verdict,
        "letter": letter,
        "ok": not any(e["status"] == "error" for e in done),
    }


def _demo_gold(run: _DemoRun) -> dict[str, GoldItem] | None:
    try:
        from billproof.data import load_gold

        return load_gold(run.source_id())
    except Exception:
        pass
    try:
        from billproof.data import load_gold

        return load_gold(run.case.case_id)
    except Exception:
        pass
    try:
        from billproof.tamper import gold_for_case

        return gold_for_case(run.case)
    except Exception:
        return None


def _live_ready(service: str) -> bool:
    from billproof.config import settings

    try:
        return bool(settings().use_real(service))
    except Exception:
        return False


def _blocked(run: _DemoRun, stage: str, started: float, reason: str) -> dict:
    return _event(
        run, stage, "blocked", started,
        key_number=_key(None, "", "blocked"),
        provenance=_prov(_PROV_UNAVAILABLE, reason),
        data={"reason": reason},
    )


def _module(name: str):
    import importlib

    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _call_first(module, names: tuple[str, ...], argsets: list[tuple]):
    """Call the first ``getattr(module, name)(*args)`` that works."""
    for name in names:
        fn = getattr(module, name, None)
        if not callable(fn):
            continue
        for args in argsets:
            try:
                return fn(*args)
            except TypeError:
                continue
    return None


# ---------------------------------------------------------------- upload


def _page_count(pdf: Path) -> int:
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(pdf))
        try:
            return len(doc)
        finally:
            doc.close()
    except Exception:
        return 1


def _doc_pdf(run: _DemoRun, doc: str) -> Path | None:
    """The packet PDF for one doc kind: the case's own DocRefs first (showcase
    cases may not be loadable through data.load_case), then the conventional path."""
    from billproof.adapters.ndi import resolve_pdf
    from billproof.config import PKG_ROOT

    for ref in getattr(run.case, "docs", []) or []:
        if getattr(ref, "kind", None) != doc:
            continue
        candidate = Path(str(ref.pdf))
        if not candidate.is_absolute():
            candidate = PKG_ROOT / candidate
        if candidate.is_file():
            return candidate
    try:
        return resolve_pdf(run.case.case_id, doc)
    except Exception:
        return None


def _stage_upload(run: _DemoRun, started: float) -> dict:
    from billproof.config import PKG_ROOT

    documents: list[dict[str, Any]] = []
    for kind in _DOC_KINDS:
        pdf = _doc_pdf(run, kind)
        if pdf is None:
            continue
        try:
            rel = str(pdf.resolve().relative_to(PKG_ROOT.resolve()))
        except Exception:
            rel = str(pdf)
        documents.append(
            {
                "kind": kind,
                "path": rel,
                "bytes": pdf.stat().st_size,
                "pages": _page_count(pdf),
            }
        )
    run.documents = documents
    total_pages = sum(d["pages"] for d in documents)
    if not documents:
        return _event(
            run, "upload", "missing", started,
            key_number=_key(0, "pages", "no PDFs found"),
            provenance=_prov(_PROV_UNAVAILABLE, "no packet PDFs on disk"),
            data={"documents": []},
        )
    # A split job only matters for a single merged PDF; showcase packets are
    # already one file per document, so segmentation is recorded, not billed.
    segments = [{"class": d["kind"], "pages": d["pages"], "file": d["path"]} for d in documents]
    return _event(
        run, "upload", "ok", started,
        key_number=_key(total_pages, "pages", "PDF pages staged"),
        provenance=_prov(_PROV_FILE, "local packet PDFs"),
        data={
            "documents": documents,
            "total_bytes": sum(d["bytes"] for d in documents),
            "split": {
                "status": "ok",
                "segments": segments,
                "note": "documents already separate; no merged packet to split",
            },
        },
    )


# ---------------------------------------------------------------- read (NDI)


def _extract_doc(run: _DemoRun, doc: str) -> tuple[list[dict], str, bool]:
    """(fields, provenance_kind, simulated) for one document."""
    if run.mode == "live":
        if not _live_ready("ndi"):
            return [], _PROV_UNAVAILABLE, False
        from billproof.adapters.ndi import extract_cache_path, get_ndi

        cached = False
        pdf = _doc_pdf(run, doc)
        if pdf is not None:
            cached = extract_cache_path(run.case.case_id, doc, pdf).is_file()
        return get_ndi().extract(run.case.case_id, doc), (_PROV_CACHE if cached else _PROV_LIVE), False

    # replay: a measured on-disk extract wins over the fixture, then the fixture.
    from billproof.adapters.ndi import FixtureNdi, extract_cache_path
    from billproof.config import DATA_DIR, SYN_DIR

    pdf = _doc_pdf(run, doc)
    cache = extract_cache_path(run.case.case_id, doc, pdf) if pdf is not None else None
    if cache is not None and cache.is_file():
        try:
            return json.loads(cache.read_text()), _PROV_CACHE, False
        except Exception:
            pass
    # Showcase-only fixtures live outside the synthetic tree the adapter scans.
    for fixture_dir in (DATA_DIR / "showcase" / "ndi_fixtures", SYN_DIR / "ndi_fixtures"):
        fixture = fixture_dir / f"{run.case.case_id}.json"
        if fixture.is_file():
            fields = _fixture_fields(fixture, doc)
            if fields is not None:
                return fields, _PROV_FIXTURE, True
    return FixtureNdi().extract(run.case.case_id, doc), _PROV_FIXTURE, True


def _fixture_fields(fixture: Path, doc: str) -> list[dict] | None:
    """``docs[doc].fields`` from an NDI fixture file, lightly normalized."""
    try:
        payload = json.loads(fixture.read_text())
        block = (payload.get("docs") or {}).get(doc) or {}
        rows = []
        for f in block.get("fields") or []:
            if isinstance(f, dict):
                rows.append(
                    {
                        "path": f.get("path") or "",
                        "value": f.get("value"),
                        "status": f.get("status")
                        or ("not_found" if f.get("value") is None else "found"),
                        "citations": f.get("citations") or [],
                    }
                )
        return rows
    except Exception:
        return None


def _doccheck(run: _DemoRun) -> dict:
    """T2's billproof.doccheck may still be landing; shim around it."""
    module = _module("billproof.doccheck")
    if module is not None:
        result = _call_first(
            module,
            ("check_case", "run_checks", "doc_check", "missing_fields", "check"),
            ((run.case, run.fields_by_doc), (run.case,), (run.case.case_id,), tuple()),
        )
        if result is not None:
            return {"status": "ok", "source": "billproof.doccheck", "result": result}
    missing = [
        f["path"]
        for fields in run.fields_by_doc.values()
        for f in fields
        if isinstance(f, dict) and f.get("status") == "not_found"
    ]
    if not run.case.eob:
        missing.append("eob")
    reason = run.case.denial.reason_text.lower() if run.case.denial else ""
    if run.case.denial and (not reason.strip() or "no explanation" in reason or "not legible" in reason):
        missing.append("denial.reason_text")
    return {
        "status": "missing" if missing else "ok",
        "source": "pipeline-shim",
        "missing_fields": missing,
        "note": "billproof.doccheck not available; counted not_found fields",
    }


def _stage_read(run: _DemoRun, started: float) -> dict:
    provs: list[str] = []
    errors: list[str] = []
    for kind in _DOC_KINDS:
        if not any(d["kind"] == kind for d in run.documents):
            continue
        fields: list[dict] = []
        try:
            fields, prov, _ = _extract_doc(run, kind)
        except Exception as exc:
            prov = "error"
            errors.append(f"{kind}: {type(exc).__name__}: {exc}")
        run.fields_by_doc[kind] = fields
        provs.append(prov)
    if run.mode == "live" and any(run.fields_by_doc.values()):
        from billproof.ingest import _build_case
        warnings = []
        parsed, _ = _build_case(run.case.case_id, run.fields_by_doc, {}, warnings)
        parsed.docs = run.case.docs
        parsed.title = run.case.title
        run.case = parsed
        errors.extend(warnings)
    found = sum(
        1
        for fields in run.fields_by_doc.values()
        for f in fields
        if isinstance(f, dict) and f.get("status") != "not_found" and f.get("value") is not None
    )
    cited = sum(
        1 for fields in run.fields_by_doc.values() for f in fields if isinstance(f, dict) and f.get("citations")
    )
    if run.mode == "live" and (not provs or all(p == _PROV_UNAVAILABLE for p in provs)):
        return _blocked(run, "read", started, "no NDI credentials configured for live mode")
    if found or cited:
        status = "ok"
    elif errors:
        status = "error"
    else:
        status = "missing"
    if _PROV_LIVE in provs:
        prov = _PROV_LIVE
    elif _PROV_CACHE in provs and _PROV_FIXTURE in provs:
        prov = "mixed"
    elif _PROV_CACHE in provs:
        prov = _PROV_CACHE
    elif _PROV_FIXTURE in provs:
        prov = _PROV_FIXTURE
    else:
        prov = _PROV_UNAVAILABLE
    simulated = prov in (_PROV_FIXTURE, _PROV_SIMULATOR, "mixed")
    detail = {
        _PROV_CACHE: "measured NDI extract, replayed from disk cache",
        _PROV_FIXTURE: "synthetic NDI fixture (our own PDF geometry)",
        _PROV_LIVE: "live NDI extract calls",
        "mixed": "mixed sources: some measured, some simulated",
    }.get(prov, "no extract source available")
    completeness = _doccheck(run)
    event = _event(
        run, "read", status, started,
        key_number=_key(found or cited, "fields", "fields extracted"),
        simulated=simulated,
        provenance=_prov(prov, detail),
        data={
            "docs": {
                k: {
                    "fields": len(v),
                    "found": sum(
                        1 for f in v if isinstance(f, dict) and f.get("status") != "not_found" and f.get("value") is not None
                    ),
                }
                for k, v in run.fields_by_doc.items()
                if v
            },
            "fields": run.fields_by_doc,
            "case_state_source": "prepared synthetic fixture" if run.mode == "replay" else "NDI extraction",
            "fields_found": found,
            "fields_cited": cited,
            "doccheck": completeness,
            **({"errors": errors} if errors else {}),
        },
    )

    if completeness.get("status") == "missing":
        event["color"] = "amber"
    return event


# ---------------------------------------------------------------- rules


def _stage_rules(run: _DemoRun, started: float) -> dict:
    from billproof.rules.checks import run_checks

    run.flags = run_checks(run.case)
    fired = [f for f in run.flags if f.fired]
    at_stake = sum(max(0, f.amount_cents) for f in fired)
    return _event(
        run, "rules", "ok", started,
        key_number=_key(len(fired), "flags", "rule flags fired"),
        provenance=_prov(_PROV_CODE, "deterministic CMS rules; never simulated"),
        data={
            "n_checks": len(run.flags),
            "flags": [
                {
                    "check": f.check.value if hasattr(f.check, "value") else str(f.check),
                    "line_no": f.line_no,
                    "detail": f.detail,
                    "rule_cite": f.rule_cite,
                    "amount_cents": f.amount_cents,
                }
                for f in fired
            ],
            "at_stake_cents": at_stake,
        },
    )


# ---------------------------------------------------------------- decide


def _sim_hybrid(run: _DemoRun) -> DecisionResult:
    """The hybrid lane over explicit simulators — replay needs no env flips."""
    from billproof.adapters.drex import SimulatedDrexAdapter
    from billproof.adapters.frontier import FrontierSimulator
    from billproof.decisions.escalation import route
    from billproof.decisions.lanes import get_frontier_model
    from billproof.schema import Answer

    case, questions, state, gold = run.case, run.questions, run.state, run.gold
    drex = SimulatedDrexAdapter().decide(state, questions, case_id=case.case_id, gold=gold)
    flags = state.get("flags", [])
    answers: dict[str, Answer] = {}
    verify_questions: list[Question] = []
    for question in questions:
        answer = drex.answers[question.name]
        selected = route(question, answer, case, flags)
        if selected == "frontier_verified":
            verify_questions.append(question)
        else:
            answer.route = selected
            answers[question.name] = answer
    frontier = None
    if verify_questions:
        drafts = {q.name: drex.answers[q.name] for q in verify_questions}
        keys = {key for q in verify_questions for key in q.evidence}
        verify_state = {**state, "evidence": {
            key: case.citations[key].model_dump(include={"doc", "page", "text"})
            for key in keys if key in case.citations
        }}
        frontier = FrontierSimulator().verify(
            verify_state, verify_questions, drafts, case_id=case.case_id, gold=gold
        )
        for question in verify_questions:
            answer = frontier.answers[question.name]
            answer.route = "human" if frontier.error or answer.raw.get("error") else "frontier_verified"
            answers[question.name] = answer
    results = [drex] + ([frontier] if frontier is not None else [])
    return DecisionResult(
        lane="hybrid",
        model=f"{drex.model}+{frontier.model if frontier else get_frontier_model()}",
        answers=answers,
        latency_ms=sum(r.latency_ms for r in results),
        input_tokens=sum(r.input_tokens for r in results),
        output_tokens=sum(r.output_tokens for r in results),
        cost_usd=sum(r.cost_usd for r in results),
        simulated=True,
        calls=sum(r.calls for r in results),
        error="; ".join(r.error for r in results if r.error) or None,
    )


def _stage_decide(run: _DemoRun, started: float) -> dict:
    from billproof.decisions.questions import build_questions, build_state

    run.questions = build_questions(run.case, run.flags)
    run.state = build_state(run.case, run.flags)
    if run.mode == "live":
        if run.statuses.get("read") in {"blocked", "error", "missing"}:
            return _blocked(run, "decide", started, "NDI read did not complete")
        if not _live_ready("drex"):
            return _blocked(run, "decide", started, "no NACE_API_KEY configured for live mode")
        from billproof.decisions.lanes import run_lane

        result = run_lane("hybrid", run.case, run.questions, run.state, gold=run.gold)
    else:
        result = _sim_hybrid(run)
    run.decision = result
    routes: dict[str, int] = {}
    for answer in result.answers.values():
        key = answer.route or ("frontier_verified" if result.lane == "frontier" else "drex_kept")
        routes[key] = routes.get(key, 0) + 1
    return _event(
        run, "decide", "ok", started,
        key_number=_key(len(run.questions), "questions", "typed decisions"),
        simulated=result.simulated,
        provenance=_prov(
            _PROV_SIMULATOR if result.simulated else _PROV_LIVE,
            f"{result.model} · {result.calls} call(s)",
        ),
        data={
            "lane": result.lane,
            "model": result.model,
            "calls": result.calls,
            "latency_ms": round(result.latency_ms, 1),
            "cost_usd": result.cost_usd,
            "input_tokens": result.input_tokens,
            "routes": routes,
            "error": result.error,
            "answers": {
                name: {
                    "choice": a.choice,
                    "confidence": round(float(a.confidence), 3),
                    "route": a.route,
                    "probabilities": a.probabilities,
                }
                for name, a in result.answers.items()
            },
        },
    )


# ------------------------------------------------- ask / browser (T3 shims)


def _manifest_evidence(run: _DemoRun, *names: str):
    evidence = (run.manifest or {}).get("evidence")
    if isinstance(evidence, dict):
        for name in names:
            if name in evidence:
                return evidence[name]
    for name in names:
        if name in (run.manifest or {}):
            return run.manifest[name]
    return None


def _cached_evidence(run: _DemoRun, service: str):
    from billproof.config import CACHE_DIR

    for pattern in (f"{run.case.case_id}.json", f"{run.case.case_id}_*.json", f"{run.source_id()}.json"):
        hits = sorted((CACHE_DIR / service).glob(pattern))
        if hits:
            try:
                return json.loads(hits[0].read_text())
            except Exception:
                continue
    return None


def _t3_stage(
    run: _DemoRun, stage: str, started: float, *,
    modules: tuple[str, ...], functions: tuple[str, ...],
    service: str, evidence_names: tuple[str, ...], label: str,
) -> dict:
    """Call only T3's explicit offline-safe entry points; never guess signatures."""
    module = _module("billproof.search" if stage == "ask" else "billproof.browser")
    fn = getattr(module, "ask_case" if stage == "ask" else "run_case", None)
    if not callable(fn):
        return _event(run, stage, "missing", started,
            provenance=_prov(_PROV_UNAVAILABLE, "T3 entry point unavailable"))
    if run.mode == "live" and not _live_ready("drex"):
        return _blocked(run, stage, started, f"no NACE_API_KEY for live {label}")
    result = fn(run.case, live=run.mode == "live")
    if result.get("status") in {"not_recorded", "no_task"}:
        return _event(run, stage, "missing", started,
            provenance=_prov(_PROV_UNAVAILABLE, result["status"]), data={"evidence": result})
    replayed = bool(result.get("replayed"))
    if stage == "ask":
        search = result.get("search") or {}
        verified = [q for q in search.get("clauses", [])
                    if q.get("verbatim") and q.get("page_ok")]
        # A negative control may contain quotes without supporting this denial.
        # Only the selected, page-checked quote may reach the draft.
        run.letter_support = [item for item in result.get("letter_support", [])
            if search.get("supports_coverage") is True and any(
                item.get("quote") == q.get("quote") and item.get("page") == q.get("page")
                and item.get("doc_label") == q.get("doc_label") for q in verified)]
        count = len(run.letter_support)
        number = _key(count, "citations", "supporting plan clauses")
        seconds = search.get("seconds")
        cost = search.get("cost_usd")
        extra = {"supports_coverage": search.get("supports_coverage"),
                 "letter_support": run.letter_support, "drex": result.get("drex"),
                 "negative_control": search.get("supports_coverage") is False}
        successful = search.get("status") == "succeeded"
    else:
        verification = result.get("verification") or {}
        quotes = verification.get("quotes") or []
        successful = result.get("status") == "succeeded" and bool(quotes) and all(q.get("verbatim") for q in quotes)
        number = _key(sum(bool(q.get("verbatim")) for q in quotes), "quotes", "verified public-source quotes")
        seconds, cost = result.get("seconds"), result.get("cost_usd")
        extra = {"stand_in": result.get("stand_in"), "proposal_status": "pending_user_approval"}
        if not successful:
            for item in run.letter_support:
                item["policy"] = None
    event = _event(run, stage, "ok" if successful else "missing", started,
        key_number=number, simulated=False,
        provenance=_prov("recorded_live" if replayed else _PROV_LIVE,
                         "recorded live; offline replay" if replayed else "live Nace call"),
        data={"evidence": result, "recorded_service_seconds": seconds if replayed else None,
              "service_seconds": seconds, "cost_usd": cost, **extra})
    if stage == "ask" and not run.letter_support:
        event["color"] = "amber"
    return event


def _stage_ask(run: _DemoRun, started: float) -> dict:
    return _t3_stage(
        run, "ask", started,
        modules=("billproof.search", "billproof.search.ask"),
        functions=("ask_case", "ask", "answer", "search_case", "search", "run"),
        service="search",
        evidence_names=("ask", "search", "ask_docs"),
        label="ask your documents",
    )


def _stage_browser(run: _DemoRun, started: float) -> dict:
    return _t3_stage(
        run, "browser", started,
        modules=("billproof.browser", "billproof.browser.runs"),
        functions=("run_case", "check", "verify", "browse", "run"),
        service="browser",
        evidence_names=("browser", "browser_check", "external"),
        label="browser evidence",
    )


# ---------------------------------------------------------------- verdict


def _stage_verdict(run: _DemoRun, started: float) -> dict:
    if run.decision is None:
        return _blocked(run, "verdict", started, "no decision answers (decide stage did not run)")
    from billproof.decisions.verdicts import compute_verdict

    run.verdict = compute_verdict(run.case, run.flags, run.decision.answers)
    v = run.verdict
    action = v.case_action.value if hasattr(v.case_action, "value") else str(v.case_action)
    known_owed = run.case.eob is not None
    if action in {"ASK_PATIENT", "INSUFFICIENT_EVIDENCE"}:
        key_number = _key(None, "cents", "responsibility unknown")
    elif action == "OK":
        key_number = _key(v.owed_cents, "cents", "checked responsibility")
    elif action == "APPEAL":
        key_number = _key((run.manifest or {}).get("dollars_at_stake_cents"), "cents", "denied charge under appeal")
    else:
        key_number = _key(v.disputed_cents, "cents", "patient charge disputed")
    event = _event(
        run, "verdict", "ok", started,
        key_number=key_number,
        simulated=run.decision.simulated,
        provenance=_prov(_PROV_CODE, "code-computed from answers; models never set dollars"),
        data={
            "verdict": {**v.model_dump(mode="json"), "owed_cents": v.owed_cents if known_owed else None},
            "case_action": action,
            "billed_balance_cents": v.billed_balance_cents,
            "owed_cents": v.owed_cents if known_owed else None,
            "owed_known": known_owed,
            "disputed_cents": v.disputed_cents,
            "needs_human": v.needs_human,
            "human_reasons": v.human_reasons,
            "deadline": v.deadline,
        },
    )

    event["color"] = ("amber" if action in {"APPEAL", "ASK_PATIENT", "INSUFFICIENT_EVIDENCE"} or any(r.startswith("human_review") for r in v.human_reasons) else "green" if action == "OK" else "red")
    return event


# ---------------------------------------------------------------- act


def _stage_act(run: _DemoRun, started: float) -> dict:
    if run.verdict is None:
        return _blocked(run, "act", started, "no verdict (upstream stage blocked)")
    from billproof.letters.assembler import assemble_letter, letter_questions

    case, verdict = run.case, run.verdict
    lquestions = letter_questions(case, verdict)
    lstate = {**run.state, "verdict": verdict.model_dump(mode="json")}
    if run.mode == "live":
        if not _live_ready("drex"):
            return _blocked(run, "act", started, "no NACE_API_KEY configured for live mode")
        from billproof.adapters.drex import get_drex

        adapter = get_drex()
        kwargs = {"gold": _letter_gold(case, verdict, lquestions)} if adapter.simulated else {}
    else:
        from billproof.adapters.drex import SimulatedDrexAdapter

        adapter = SimulatedDrexAdapter()
        kwargs = {"gold": _letter_gold(case, verdict, lquestions)}
    answers = (
        adapter.decide(lstate, lquestions, case_id=case.case_id, **kwargs).answers
        if lquestions
        else {}
    )
    letter = assemble_letter(case, verdict, answers, support=run.letter_support)
    run.letter = letter
    event = _event(
        run, "act", "ok", started,
        key_number=(_key(None, "cents", "responsibility unknown") if case.eob is None else _key((run.manifest or {}).get("dollars_at_stake_cents", letter.amount_cents) if verdict.case_action.value == "APPEAL" else verdict.owed_cents if verdict.case_action.value == "OK" else letter.amount_cents, "cents", "denied charge under appeal" if verdict.case_action.value == "APPEAL" else "checked responsibility" if verdict.case_action.value == "OK" else "amount addressed in draft")),
        simulated=bool(getattr(adapter, "simulated", False)),
        provenance=_prov(
            _PROV_SIMULATOR if getattr(adapter, "simulated", False) else _PROV_LIVE,
            "Drex picks grounds; code fills every fact",
        ),
        data={
            "support_documents": (run.manifest or {}).get("evidence_documents", []) if run.letter_support else [],
            "letter_support": run.letter_support,
            "browser_proposal_status": "pending_user_approval" if any(x.get("policy") for x in run.letter_support) else None,
            "letter": {
                "letter_type": letter.letter_type,
                "recipient": letter.recipient,
                "subject": letter.subject,
                "clauses": [c.model_dump(mode="json") for c in letter.clauses],
                "n_clauses": len(letter.clauses),
                "clause_ids": [c.clause_id for c in letter.clauses],
                "grounds": [
                    {
                        "clause_id": c.clause_id,
                        "title": c.title,
                        "picked_by": c.picked_by,
                        "probability": c.probability,
                        "evidence": [
                            {
                                "doc": ev.doc.value if hasattr(ev.doc, "value") else str(ev.doc),
                                "page": ev.page,
                                "text": ev.text,
                            }
                            for ev in c.evidence
                        ],
                    }
                    for c in letter.clauses
                ],
                "amount_cents": letter.amount_cents,
                "deadline": letter.deadline,
                "generated_words": letter.generated_words,
            }
        },
    )

    event["color"] = "green" if verdict.case_action.value == "OK" else "amber"
    event["data"]["human_review_required"] = verdict.case_action.value != "OK"
    return event


_STAGE_FUNCS = {
    "upload": _stage_upload,
    "read": _stage_read,
    "rules": _stage_rules,
    "decide": _stage_decide,
    "ask": _stage_ask,
    "browser": _stage_browser,
    "verdict": _stage_verdict,
    "act": _stage_act,
}
