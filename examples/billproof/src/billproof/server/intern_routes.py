"""Recorded Intern sessions (Nace harness).

  GET /api/intern/sessions
  GET /api/intern/session/{id}

Reads ``data/intern/session_*.json`` written after a headless ``intern -p`` run.
Missing files return an empty list or 404. The journal is not rewritten here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from billproof.config import DATA_DIR

router = APIRouter(tags=["intern"])


def _dir() -> Path:
    return DATA_DIR / "intern"


def _safe_id(session_id: str) -> str | None:
    if not session_id or session_id != Path(session_id).name:
        return None
    if ".." in session_id or "/" in session_id or "\\" in session_id:
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    if any(ch not in allowed for ch in session_id):
        return None
    return session_id


def _load(path: Path) -> dict[str, Any] | None:
    try:
        body = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _summary(path: Path, body: dict[str, Any]) -> dict[str, Any]:
    system1 = body.get("system1") if isinstance(body.get("system1"), dict) else {}
    cost = body.get("cost_usd") if isinstance(body.get("cost_usd"), dict) else {}
    return {
        "id": body.get("id") or path.stem.removeprefix("session_"),
        "task": body.get("task"),
        "system2": body.get("system2"),
        "system1_model": system1.get("model") or body.get("system1_model"),
        "started_at": body.get("started_at"),
        "elapsed_s": body.get("elapsed_s"),
        "turns": body.get("turns"),
        "cost_usd": cost,
        "system1_asks": system1.get("asks"),
        "system1_interventions": system1.get("n_interventions"),
        "reply_preview": (body.get("reply") or "")[:280],
    }


@router.get("/api/intern/sessions")
def list_sessions() -> JSONResponse:
    folder = _dir()
    sessions: list[dict[str, Any]] = []
    if folder.is_dir():
        for path in sorted(folder.glob("session_*.json")):
            body = _load(path)
            if body is None:
                continue
            sessions.append(_summary(path, body))
    return JSONResponse({"sessions": sessions, "n": len(sessions)})


@router.get("/api/intern/session/{session_id}")
def get_session(session_id: str) -> JSONResponse:
    safe = _safe_id(session_id)
    if safe is None:
        return JSONResponse({"error": "bad session id"}, status_code=400)
    path = _dir() / f"session_{safe}.json"
    if not path.is_file():
        return JSONResponse({"error": "session not found", "id": safe}, status_code=404)
    body = _load(path)
    if body is None:
        return JSONResponse({"error": "session unreadable", "id": safe}, status_code=404)
    return JSONResponse(body)
