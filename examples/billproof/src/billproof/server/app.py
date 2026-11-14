"""BillProof HTTP API — CONTRACT.md §4.

Sibling modules are being written in parallel, so every dependency is imported
lazily inside the handler that needs it: missing module -> 503 {"error","missing"},
missing data -> 404 {"error"}. The server always starts and serves the UI.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import tomllib
import urllib.parse
from functools import lru_cache
from typing import Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from billproof.config import CACHE_DIR, PKG_ROOT, REAL_DIR, RUNS_DIR, STATIC_DIR, SYN_DIR, settings
from billproof.schema import Action, Case
from billproof.server import pages as _pages
from billproof.server import race as _race

app = FastAPI(title="BillProof")

# Sibling route modules land in parallel; optional so the server starts without them.
for _opt in ("audit_routes", "ingest_routes", "demo_routes", "search_routes", "browser_routes", "pa_routes", "cc_routes", "dial_routes", "results_routes", "pa_whatif_routes", "pa_browser_routes", "intern_routes"):
    try:
        app.include_router(importlib.import_module(f"billproof.server.{_opt}").router)
    except ModuleNotFoundError as exc:
        if exc.name != f"billproof.server.{_opt}":
            print(f"[billproof] router {_opt} not loaded: {exc}")
    except Exception as exc:
        print(f"[billproof] router {_opt} not loaded: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- helpers


class _Missing(Exception):
    """A sibling module (or a needed function in it) does not exist yet."""

    def __init__(self, module: str):
        super().__init__(module)
        self.module = module


def _need(module: str, *attrs: str):
    """Lazy import of billproof.<module>; raise _Missing if absent/incomplete."""
    try:
        m = importlib.import_module(f"billproof.{module}")
    except Exception:
        raise _Missing(module)
    if any(not callable(getattr(m, a, None)) for a in attrs):
        raise _Missing(module)
    return m


@app.exception_handler(_Missing)
async def _missing_handler(_: Request, exc: _Missing) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"error": f"module '{exc.module}' is not available yet", "missing": exc.module},
    )


@app.exception_handler(ModuleNotFoundError)
async def _mnf_handler(_: Request, exc: ModuleNotFoundError) -> JSONResponse:
    # a sibling module exists but something it imports is still being written
    return JSONResponse(status_code=503, content={"error": str(exc), "missing": exc.name})


@app.exception_handler(Exception)
async def _error_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: {exc}"})


def j404(msg: str) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": msg})


def j400(msg: str) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": msg})


class NoCache:
    """Pure ASGI (not BaseHTTPMiddleware, which stalls SSE streams): Cache-Control: no-cache on UI + API."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        p = scope.get("path", "") if scope["type"] == "http" else ""
        if not (p == "/" or p.startswith("/static") or p.startswith("/api")):
            return await self.app(scope, receive, send)

        async def send_nc(msg):
            if msg["type"] == "http.response.start":
                hs = [(k, v) for k, v in msg.get("headers", []) if k.lower() != b"cache-control"]
                msg = {**msg, "headers": hs + [(b"cache-control", b"no-cache")]}
            await send(msg)

        await self.app(scope, receive, send_nc)


app.add_middleware(NoCache)


@lru_cache(maxsize=1)
def _version() -> str:
    try:
        with open(PKG_ROOT / "pyproject.toml", "rb") as f:
            return tomllib.load(f)["project"]["version"]
    except Exception:
        return "0.1.0"


def _load_case_or_404(data, case_id: str):
    try:
        return data.load_case(case_id)
    except Exception:
        raise _NotFound(f"case {case_id!r} not found")


class _NotFound(Exception):
    pass


@app.exception_handler(_NotFound)
async def _nf_handler(_: Request, exc: _NotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": str(exc)})


def _dump(obj: Any) -> Any:
    return obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj


async def _analyze(case: Case, lanes: tuple[str, ...]):
    pipe = _need("pipeline", "analyze_case")
    return await asyncio.to_thread(pipe.analyze_case, case, lanes=lanes, include_gold=True)


def _hybrid_verdict(analysis) -> Any:
    v = analysis.verdicts.get("hybrid") or analysis.verdicts.get("drex")
    if v is None and analysis.verdicts:
        v = next(iter(analysis.verdicts.values()))
    return v


async def _packet_parts(case: Case):
    """(analysis, verdict, letter); letter falls back to letters.assembler when needed."""
    analysis = await _analyze(case, ("frontier", "drex", "hybrid"))
    verdict = _hybrid_verdict(analysis)
    letter = analysis.letter
    if letter is None and verdict is not None:
        asm = importlib.import_module("billproof.letters.assembler")  # may raise -> caller 503s
        letter = await asyncio.to_thread(_assemble_fallback, asm, case, verdict, analysis)
    return analysis, verdict, letter


def _assemble_fallback(asm, case, verdict, analysis):
    lqs = asm.letter_questions(case, verdict)
    answers = {}
    if lqs:
        try:
            drex = importlib.import_module("billproof.adapters.drex").get_drex()
            qs = importlib.import_module("billproof.decisions.questions")
            state = qs.build_state(case, analysis.flags)
            answers = drex.decide(state, lqs, case_id=case.case_id).answers
        except Exception:
            answers = {}
    return asm.assemble_letter(case, verdict, answers)


# ---------------------------------------------------------------- status / cases


@app.get("/api/status")
async def api_status():
    s = settings()

    def mode(svc: str) -> str:
        try:
            return s.mode_label(svc)
        except Exception:
            return "SIMULATED"

    def default_run(bench: str) -> dict:
        rid, meta = _race.pick_default_run(bench)
        return {"run_id": rid, "mode": (meta or {}).get("mode")}

    return {
        "modes": {svc: mode(svc) for svc in ("drex", "frontier", "ndi")},
        "drex_model": s.drex_model,
        "frontier_model": s.frontier_model,
        "label_sha256": {
            "synthetic": _race.label_sha256("synthetic"),
            "imr": _race.label_sha256("imr"),
        },
        "default_runs": {b: default_run(b) for b in ("synthetic", "imr")},
        "runs": _race.list_runs(),
        "version": _version(),
    }


@app.get("/api/cases")
async def api_cases(split: str | None = Query(default=None)):
    data = _need("data", "list_cases")
    cases = await asyncio.to_thread(data.list_cases, split)
    return [
        {
            "case_id": c.case_id,
            "split": c.split,
            "title": c.title,
            "n_lines": len(c.bill.lines),
            "balance_cents": c.bill.patient_balance_cents,
            "setting": c.service_setting,
            "has_eob": c.eob is not None,
            "has_denial": c.denial is not None,
        }
        for c in cases
    ]


def _ndi_block(case_id: str, doc_kinds: list[str]) -> dict | None:
    """Per-doc NDI extraction: live/fixture adapter if available, else the raw fixture file."""
    try:
        ndi_mod = importlib.import_module("billproof.adapters.ndi")
        ad = ndi_mod.get_ndi()
        out = {}
        for d in doc_kinds:
            try:
                out[d] = {
                    "fields": ad.extract(case_id, d),
                    "pages": None,
                    "simulated": bool(getattr(ad, "simulated", False)),
                }
            except Exception as e:
                out[d] = {"fields": [], "error": str(e)}
        return out
    except Exception:
        pass
    fx = SYN_DIR / "ndi_fixtures" / f"{case_id}.json"
    if fx.exists():
        try:
            docs = (json.loads(fx.read_text()) or {}).get("docs") or {}
            return {
                k: {"fields": v.get("fields", []), "pages": v.get("pages"), "simulated": True}
                for k, v in docs.items()
                if isinstance(v, dict)
            }
        except Exception:
            return None
    return None


@app.get("/api/cases/{case_id}")
async def api_case(case_id: str):
    data = _need("data", "load_case")
    case = _load_case_or_404(data, case_id)
    pages = await asyncio.to_thread(_pages.doc_pages, case_id, case)
    ndi = await asyncio.to_thread(_ndi_block, case_id, list(pages) or list(_pages.DOC_KINDS))
    return {"case": case.model_dump(mode="json"), "ndi": ndi, "pages": pages}


@app.get("/api/cases/{case_id}/page/{doc}/{page}.png")
async def api_page_png(case_id: str, doc: str, page: int, scale: float = 2.0):
    scale = _pages.clamp_scale(scale)
    case = _try_case(case_id)
    pdf = _pages.pdf_for(case_id, doc, case)
    if pdf is None:
        return j404(f"no {doc} pdf for {case_id}")
    try:
        out = await asyncio.to_thread(_pages.page_png, pdf, case_id, doc, page, scale)
    except IndexError as e:
        return j404(str(e))
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"{type(e).__name__}: {e}"})
    return FileResponse(out, media_type="image/png")


@app.get("/api/cases/{case_id}/crop")
async def api_crop(
    case_id: str,
    doc: str,
    page: int,
    poly: str,
    scale: float = 2.0,
):
    scale = _pages.clamp_scale(scale)
    pts = _pages.parse_poly(poly)
    if not pts:
        return j400("poly must be [[x,y],...] JSON or x1,y1,x2,y2,... (normalized)")
    case = _try_case(case_id)
    pdf = _pages.pdf_for(case_id, doc, case)
    if pdf is None:
        return j404(f"no {doc} pdf for {case_id}")
    try:
        out = await asyncio.to_thread(_pages.crop_png, pdf, case_id, doc, page, pts, scale)
    except (IndexError, ValueError) as e:
        return j404(str(e))
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"{type(e).__name__}: {e}"})
    return FileResponse(out, media_type="image/png")


def _try_case(case_id: str) -> Case | None:
    try:
        data = importlib.import_module("billproof.data")
        return data.load_case(case_id)
    except Exception:
        return None


# ---------------------------------------------------------------- analyze / tamper


class AnalyzeBody(BaseModel):
    case_id: str
    lanes: list[Literal["frontier", "drex", "hybrid"]] | None = None


@app.post("/api/analyze")
async def api_analyze(body: AnalyzeBody):
    data = _need("data", "load_case")
    case = _load_case_or_404(data, body.case_id)
    lanes = tuple(body.lanes) if body.lanes else ("frontier", "drex", "hybrid")
    try:
        res = await _analyze(case, lanes)
    except (_Missing, ModuleNotFoundError):
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"{type(e).__name__}: {e}"})
    return res.model_dump(mode="json")


class TamperBody(BaseModel):
    case_id: str
    ops: list[dict[str, Any]]


@app.post("/api/tamper")
async def api_tamper(body: TamperBody):
    data = _need("data", "load_case")
    case = _load_case_or_404(data, body.case_id)
    tm = _need("tamper", "tamper", "naive_check")
    allowed = set(getattr(tm, "TAMPER_OPS", None) or ())
    bad = [o.get("op") for o in body.ops if o.get("op") not in allowed]
    if bad:
        return j400(f"unknown tamper ops {bad}; allowed: {sorted(allowed)}")
    try:
        new_case, gold = await asyncio.to_thread(tm.tamper, case, body.ops)
        naive = await asyncio.to_thread(tm.naive_check, new_case)
        analysis = await asyncio.to_thread(
            _need("pipeline", "analyze_case").analyze_case, new_case, include_gold=False
        )
    except (_Missing, ModuleNotFoundError):
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"{type(e).__name__}: {e}"})
    caught = any(v.case_action != Action.OK for v in analysis.verdicts.values()) or any(
        f.fired for f in analysis.flags
    )
    return {
        "case": new_case.model_dump(mode="json"),
        "naive": naive,
        "billproof": analysis.model_dump(mode="json"),
        "gold": {k: _dump(g) for k, g in (gold or {}).items()},
        "caught": caught,
    }


# ---------------------------------------------------------------- race stream


@app.get("/api/race/stream")
async def api_race_stream(
    request: Request,
    bench: Literal["synthetic", "imr"] = "synthetic",
    split: str = "test",
    source: Literal["replay", "sim", "live"] = "replay",
    run_id: str | None = None,
    speed: float = 1.0,
):
    speed = min(max(speed, 0.1), 100.0)
    gen = _race.stream(request, bench=bench, split=split, source=source, run_id=run_id, speed=speed)
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------- report / calibration


async def _report(bench: str, run_id: str | None):
    rid = run_id or _race.newest_run_id(bench)
    if not rid:
        return j404(f"no runs found for bench={bench}"), None
    rp = RUNS_DIR / f"{rid}.report.json"
    if rp.exists():
        try:
            return None, (json.loads(rp.read_text()) | {"run_id": rp.name[: -len(".report.json")]})
        except Exception:
            pass
    metrics = _need("bench.metrics", "build_report")
    try:
        rows, meta = await asyncio.to_thread(_race.load_rows, rid)
    except FileNotFoundError:
        return j404(f"run {rid} not found"), None
    if not rows:
        return j404(f"run {rid} is empty"), None
    report = await asyncio.to_thread(metrics.build_report, rows, meta)
    return None, (report | {"run_id": rid} if isinstance(report, dict) else {"report": report, "run_id": rid})


@app.get("/api/report")
async def api_report(bench: str = "synthetic", run_id: str | None = None):
    err, rep = await _report(bench, run_id)
    return err or rep


@app.get("/api/calibration")
async def api_calibration(bench: str = "synthetic", run_id: str | None = None):
    err, rep = await _report(bench, run_id)
    if err is not None:
        return err
    return {
        "run_id": rep.get("run_id"),
        "meta": rep.get("meta"),
        "reliability": rep.get("reliability"),
        "sweep": rep.get("sweep"),
        "knee": rep.get("knee"),
        "heldout": rep.get("heldout"),
        "selective": rep.get("selective"),
        "lanes": rep.get("lanes"),
        "headline": rep.get("headline"),
    }


# ---------------------------------------------------------------- packet


def _evidence(case: Case, analysis, letter) -> list[dict]:
    out, seen = [], set()

    def add(c):
        pts = [pt for poly in (c.polygons or []) for pt in poly]
        if not pts:
            return
        key = (c.doc, c.page, tuple(pts))
        if key in seen:
            return
        seen.add(key)
        q = urllib.parse.quote(json.dumps(pts, separators=(",", ":")))
        out.append(
            {
                "citation": c.model_dump(mode="json"),
                "crop_url": f"/api/cases/{case.case_id}/crop?doc={c.doc}&page={c.page}&poly={q}&scale=2",
            }
        )

    if letter is not None:
        for cl in letter.clauses:
            for c in cl.evidence:
                add(c)
    if analysis is not None:
        for f in analysis.flags:
            for k in f.evidence:
                if k in case.citations:
                    add(case.citations[k])
    return out


@app.get("/api/packet/{case_id}.pdf")
async def api_packet_pdf(case_id: str):
    data = _need("data", "load_case")
    case = _load_case_or_404(data, case_id)
    render = _need("letters.render", "render_packet_pdf")
    try:
        _, verdict, letter = await _packet_parts(case)
    except (_Missing, ModuleNotFoundError):
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"{type(e).__name__}: {e}"})
    if verdict is None or letter is None:
        return j404("no verdict/letter for this case")
    out = CACHE_DIR / "packets" / f"{case_id}.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        path = await asyncio.to_thread(render.render_packet_pdf, case, verdict, letter, out)
    except ModuleNotFoundError:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"{type(e).__name__}: {e}"})
    return FileResponse(path or out, media_type="application/pdf", filename=f"billproof-{case_id}.pdf")


@app.get("/api/packet/{case_id}")
async def api_packet(case_id: str):
    data = _need("data", "load_case")
    case = _load_case_or_404(data, case_id)
    try:
        analysis, verdict, letter = await _packet_parts(case)
    except (_Missing, ModuleNotFoundError):
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"{type(e).__name__}: {e}"})
    return {
        "case_id": case_id,
        "verdict": _dump(verdict),
        "letter": _dump(letter),
        "evidence": _evidence(case, analysis, letter),
        "owed_cents": verdict.owed_cents if verdict else None,
        "deadline": (verdict.deadline if verdict else None)
        or (case.denial.appeal_deadline if case.denial else None),
    }


# ---------------------------------------------------------------- IMR items


def _imr_gold(case_id: str) -> dict | None:
    f = REAL_DIR / "imr_labels.jsonl"
    if f.exists():
        try:
            for line in f.read_text().splitlines():
                row = json.loads(line)
                if row.get("case_id") == case_id:
                    return row.get("gold")
        except Exception:
            pass
    try:
        m = importlib.import_module("billproof.bench.real_imr")
        return {k: _dump(v) for k, v in m.load_gold(case_id).items()}
    except Exception:
        return None


@app.get("/api/imr/items")
async def api_imr_items(split: str | None = "test", gold: int = 0):
    m = _need("bench.real_imr", "list_items")
    items = await asyncio.to_thread(m.list_items, split)
    if gold:
        return [dict(it, gold=_imr_gold(it.get("case_id"))) for it in items]
    return items


@app.get("/api/imr/items/{item_id}")
async def api_imr_item(item_id: str, gold: int = 0):
    m = _need("bench.real_imr", "list_items")
    items = await asyncio.to_thread(m.list_items, None)
    it = next((i for i in items if i.get("case_id") == item_id), None)
    if it is None:
        return j404(f"IMR item {item_id!r} not found")
    if gold:
        it = dict(it, gold=_imr_gold(item_id))
    return it


# ---------------------------------------------------------------- UI


@app.get("/", response_class=HTMLResponse)
async def index():
    idx = STATIC_DIR / "index.html"
    if idx.exists():
        return HTMLResponse(idx.read_text())
    return HTMLResponse(
        "<!doctype html><title>BillProof</title><body style='background:#0b1020;color:#dde;"
        "font-family:system-ui;padding:3em'>"
        "<h1>BillProof</h1><p>UI shell pending — <a style='color:#7cf' href='/api/status'>/api/status</a></p>"
        "</body>"
    )


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
