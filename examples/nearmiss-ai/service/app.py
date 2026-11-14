"""Local FastAPI boundary (contracts.md "Local API proposed surface").

  GET  /health           -> readiness + whether a Nace key is configured (never the key)
  GET  /evidence/status  -> NDI evidence cache readiness
  POST /analyze          -> CaseContext in, InvestigationResult out

Invalid request shape -> HTTP 422 (FastAPI/pydantic). A valid request whose
external processing fails -> HTTP 200 with status "incomplete" and errors.

Run:  uvicorn service.app:app --port 8000
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from fastapi import FastAPI

from agents.drex_client import NaceDrexDecider
from agents.orchestrator import run_investigation
from evidence.store import EvidenceStore
from service.config import REPO_ROOT, get_settings
from service.contracts import CaseContext, EvidenceStatusResponse, HealthResponse, InvestigationResult

logger = logging.getLogger("nearmiss.service")
RUNS_DIR = REPO_ROOT / "service" / "runs"

app = FastAPI(
    title="NearMiss AI investigation service",
    version="1.0",
    description="Builder B backend: Drex multi-agent investigation with NDI-cited evidence.",
)


def _store() -> EvidenceStore:
    # Re-read per request so a fresh `python -m evidence.ingest` is picked up without restarting.
    return EvidenceStore(get_settings().evidence_cache_dir)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    ready = settings.nace_configured
    return HealthResponse(status="ready" if ready else "degraded", nace_configured=ready)


@app.get("/evidence/status", response_model=EvidenceStatusResponse)
def evidence_status() -> EvidenceStatusResponse:
    return EvidenceStatusResponse(**_store().status())


def _save_run(result: InvestigationResult) -> None:
    """Keep every real run locally so successful/challenged runs can be reused as labeled cached demos."""
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        safe_case_id = re.sub(r"[^A-Za-z0-9_-]", "_", result.case_id)[:64] or "case"
        path: Path = RUNS_DIR / f"{safe_case_id}__{result.run_id}.json"
        path.write_text(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        logger.warning("could not save run %s", result.run_id)


@app.post("/analyze", response_model=InvestigationResult)
def analyze(case: CaseContext) -> InvestigationResult:
    # Sync endpoint: FastAPI runs it in a worker thread; Drex calls are sequential by design.
    settings = get_settings()
    result = run_investigation(
        case,
        decider_factory=lambda: NaceDrexDecider(settings.decision_model, settings.drex_timeout_seconds),
        store=_store(),
        requested_model=settings.decision_model,
    )
    _save_run(result)
    return result
