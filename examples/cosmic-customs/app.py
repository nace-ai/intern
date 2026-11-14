"""Local customs terminal. Keys stay on the server."""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from clients import (
    DrexClient,
    MockDecisionClient,
    OpenRouterClient,
    confirm_published_drex_rate,
    utc_now,
)
from config import APP_MODE, NACE_DREX_MODEL, OPENROUTER_MODEL, ROOT, STATIC_DIR, secret_present
from prompts import input_hash, shared_input_for_case
from schemas import DecisionInput
from storage import ResultStore

app = FastAPI(title="Cosmic Customs")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

CASE_FILE = {"dev": ROOT / "data" / "dev_20.json", "test": ROOT / "data" / "test_100.json"}
PDF_NAME = re.compile(r"^(DEV-\d{2}|TST-\d{3})\.pdf$")
PREVIEW_NAME = re.compile(r"^(DEV-\d{2}|TST-\d{3})-p[123]\.png$")
ASSET_NAME = re.compile(r"^(specimen_0[123]|containment_seal|customs_stamp)\.png$")
ACTIVE: dict[str, Any] = {"run_id": None, "task": None}


def all_cases() -> dict[str, dict[str, Any]]:
    rows = {}
    for path in CASE_FILE.values():
        for case in json.loads(path.read_text(encoding="utf-8")):
            rows[case["case_id"]] = case
    return rows


CASES = all_cases()
SHOWCASE = json.loads((ROOT / "data" / "showcase.json").read_text(encoding="utf-8"))["case_ids"]


def public_case(case: dict[str, Any]) -> dict[str, Any]:
    text = "\n".join(document["text"] for document in case["documents"])
    species = "Unknown specimen"
    reference = case["case_id"]
    if "Specimen common name: " in text:
        species = text.split("Specimen common name: ", 1)[1].split(".", 1)[0]
    if "Reference " in text:
        reference = text.split("Reference ", 1)[1].split(".", 1)[0]
    previews = []
    for page in (1, 2, 3):
        preview = ROOT / "data" / "pdfs" / "previews" / f"{case['case_id']}-p{page}.png"
        if preview.exists():
            previews.append(f"/media/previews/{case['case_id']}-p{page}.png")
    pdf = ROOT / "data" / "pdfs" / f"{case['case_id']}.pdf"
    return {
        "case_id": case["case_id"],
        "species": species,
        "reference": reference,
        "template_family": case["template_family"],
        "documents": case["documents"],
        "source_annotations": case["source_annotations"],
        "expected_verdict": case["expected_verdict"],
        "pdf": f"/media/pdfs/{case['case_id']}.pdf" if pdf.exists() else None,
        "previews": previews,
    }


def safe_file(root: Path, name: str, pattern: re.Pattern[str]) -> Path:
    if not pattern.fullmatch(name):
        raise HTTPException(404, "file not available")
    base = root.resolve()
    path = (base / name).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(404, "file not available")
    return path


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "mode": APP_MODE,
        "time": utc_now(),
        "drex_model": NACE_DREX_MODEL,
        "openrouter_model": OPENROUTER_MODEL,
        "nace_configured": secret_present("NACE_API_KEY"),
        "openrouter_configured": secret_present("OPENROUTER_API_KEY"),
        "inspect_mode": inspect_mode(),
    }


@app.get("/api/shipments")
def shipments() -> dict[str, Any]:
    return {"shipments": [public_case(CASES[case_id]) for case_id in SHOWCASE]}


@app.get("/api/shipments/{case_id}")
def shipment(case_id: str) -> dict[str, Any]:
    case = CASES.get(case_id)
    if case is None or case_id not in SHOWCASE:
        raise HTTPException(404, "shipment not in the local queue")
    return public_case(case)


@app.get("/api/results")
def results(track: str = "decisions", mode: str = "live") -> dict[str, Any]:
    if mode not in ("live", "mock") or track not in ("decisions", "documents"):
        raise HTTPException(400, "unsupported result query")
    path = ResultStore().root / mode / track / "latest.json"
    if not path.exists():
        return {"mode": mode, "track": track, "measurements": None, "run": None}
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["mode"] = mode
    payload["track"] = track
    return payload


def on_vercel() -> bool:
    return bool(os.getenv("VERCEL"))


def inspection_bases(root: Path | None = None) -> list[Path]:
    if root is not None:
        return [root]
    bases = [ResultStore().root]
    if on_vercel():
        bases.insert(0, Path("/tmp/cosmic-customs-results"))
    return bases


def inspection_path(run_id: str, root: Path | None = None) -> Path | None:
    for base in inspection_bases(root):
        resolved = base.resolve()
        for mode in ("live", "mock"):
            path = (resolved / mode / "inspections" / f"{run_id}.json").resolve()
            if path.is_relative_to(resolved) and path.is_file():
                return path
    return None


def inspect_mode() -> str:
    """Live inspection is opt-in: APP_MODE=live plus both keys. Everything else stays mock."""
    if APP_MODE == "live" and secret_present("NACE_API_KEY") and secret_present("OPENROUTER_API_KEY"):
        return "live"
    return "mock"


@app.post("/api/inspect")
async def inspect(body: dict[str, Any]) -> JSONResponse:
    case_id = body.get("case_id")
    if case_id not in SHOWCASE:
        raise HTTPException(400, "case is not in the showcase queue")
    if APP_MODE == "live" and not (secret_present("NACE_API_KEY") and secret_present("OPENROUTER_API_KEY")):
        # Refuse loudly instead of silently substituting a mock for a live request.
        raise HTTPException(400, "live inspection is configured but API keys are missing; no mock fallback was run")
    if ACTIVE["run_id"]:
        return JSONResponse({"run_id": ACTIVE["run_id"], "status": "already_running"}, status_code=409)
    mode = inspect_mode()
    run_id = "inspect-" + uuid.uuid4().hex[:10]
    ACTIVE["run_id"] = run_id
    record = {"run_id": run_id, "status": "running", "case_id": case_id, "mode": mode}
    write_root = Path("/tmp/cosmic-customs-results") if on_vercel() else ResultStore().root
    path = write_root / mode / "inspections" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")

    async def finish() -> None:
        try:
            case = CASES[case_id]
            shared = shared_input_for_case(case)
            request = DecisionInput(
                case_id=case_id,
                shared_input=shared,
                input_hash=input_hash(shared),
                evidence_mode="raw_text",
            )
            if mode == "mock":
                drex, router = MockDecisionClient("drex"), MockDecisionClient("openrouter")
                left, right = await drex.decide(request), await router.decide(request)
                measurements = "simulated"
            else:
                async with httpx.AsyncClient() as http:
                    drex = DrexClient(http)
                    router = OpenRouterClient(http)
                    try:
                        price_confirmed = await confirm_published_drex_rate(http)
                    except Exception:
                        price_confirmed = False
                    if not price_confirmed:
                        # Same refusal as the benchmark: an unconfirmed rate means unknown cost, not a guessed price.
                        drex.usd_per_input_token = None
                    left, right = await asyncio.gather(drex.decide(request), router.decide(request))
                record["drex_price_confirmed"] = price_confirmed
                measurements = "measured"
            record.update(
                {
                    "status": "completed",
                    "measurements": measurements,
                    "finished_at": utc_now(),
                    "drex": left.dump_public(),
                    "openrouter": right.dump_public(),
                }
            )
        except Exception as exc:  # surface the failure to the poller without hiding it
            record.update({"status": "error", "error": type(exc).__name__})
        finally:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(record), encoding="utf-8")
            except OSError:
                if not on_vercel():
                    raise
            ACTIVE["run_id"] = None

    if on_vercel():
        # The hosted filesystem does not keep a write for the next request.
        await finish()
    else:
        ACTIVE["task"] = asyncio.create_task(finish())
    if record.get("status") in ("completed", "error"):
        return JSONResponse(record, status_code=202)
    return JSONResponse({"run_id": run_id, "status": "running"}, status_code=202)


@app.post("/api/scan")
def scan(body: dict[str, Any]) -> dict[str, Any]:
    case_id = body.get("case_id")
    if case_id not in SHOWCASE:
        raise HTTPException(400, "case is not in the showcase queue")
    case = CASES[case_id]
    note = next(document["text"] for document in case["documents"] if document["kind"] == "inspector_note")
    text = note + "\nUltrasonic sweep shows the interior mass sits low in the cask."
    return {
        "version_id": "ultra-v1-" + case_id.lower(),
        "case_id": case_id,
        "evidence_mode": "ultrasonic",
        "text": text,
    }


@app.get("/api/runs/{run_id}")
def run(run_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"inspect-[a-f0-9]{10}", run_id):
        raise HTTPException(404, "run not found")
    path = inspection_path(run_id)
    if path is None:
        raise HTTPException(404, "run not found")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/media/pdfs/{name}")
def pdf(name: str) -> FileResponse:
    if name.removesuffix(".pdf") not in SHOWCASE:
        raise HTTPException(404, "file not available")
    return FileResponse(safe_file(ROOT / "data" / "pdfs", name, PDF_NAME))


@app.get("/media/previews/{name}")
def preview(name: str) -> FileResponse:
    case_id = name.split("-p", 1)[0]
    if case_id not in SHOWCASE:
        raise HTTPException(404, "file not available")
    return FileResponse(safe_file(ROOT / "data" / "pdfs" / "previews", name, PREVIEW_NAME))


@app.get("/media/artwork/{name}")
def artwork(name: str) -> FileResponse:
    generated = ROOT / "assets" / "generated" / name
    firefly = ROOT / "assets" / "adobe_firefly" / name
    if not ASSET_NAME.fullmatch(name):
        raise HTTPException(404, "file not available")
    path = firefly if firefly.is_file() else generated
    if not path.is_file():
        raise HTTPException(404, "file not available")
    return FileResponse(path.resolve())
