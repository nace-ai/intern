"""Read-only API server for the Quorum web UI.

Serves arena results, NDI citations, page images and usage counters from
files in the data folder. Makes no Nace or DeepInfra calls.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import pymupdf
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from backend.app import config, history, nace
from backend.app.hub import Hub

PAGE_DPI = 110
NACE_ZERO: dict[str, int] = {
    "drex_calls": 0,
    "drex_questions": 0,
    "drex_input_tokens": 0,
    "ndi_jobs": 0,
    "ndi_pages": 0,
    "search_calls": 0,
    "workspace_files": 0,
}

hub = Hub()


def data_dir() -> Path:
    """Data folder: env QUORUM_DATA if set, else config.DATA. Tests may patch this."""
    env = os.environ.get("QUORUM_DATA", "").strip()
    return Path(env) if env else config.DATA


def arena_dir() -> Path:
    return data_dir() / "arena"


# ---------- file helpers (pure-ish) ----------

def decode_text(raw: bytes) -> str:
    """Decode UTF-8 (with or without BOM) or UTF-16 tolerantly."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    if raw[:200].count(b"\x00") > 20:
        enc = "utf-16-le" if raw[1:2] == b"\x00" else "utf-16-be"
        return raw.decode(enc, errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


def read_jsonl(path: Path) -> list[Any]:
    """All parseable JSON lines of a file; [] if missing. Bad lines are skipped."""
    if not path.exists():
        return []
    out: list[Any] = []
    for line in decode_text(path.read_bytes()).splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(decode_text(path.read_bytes()))
    except json.JSONDecodeError:
        return default


def attack_key(rec: dict) -> str:
    return f"{rec.get('round')}-{rec.get('id')}"


def load_attacks() -> list[dict]:
    recs = [r for r in read_jsonl(arena_dir() / "attacks.jsonl") if isinstance(r, dict)]
    for r in recs:
        r["key"] = attack_key(r)
    return recs


def find_attack(key: str) -> dict:
    for r in load_attacks():
        if r["key"] == key:
            return r
    raise HTTPException(status_code=404, detail="attack not found")


def load_parsed_fields(doc_hash: str | None) -> dict | None:
    """Mapped NDI fields (value, status, citations) from the cache, or None."""
    if not doc_hash:
        return None
    digest = str(doc_hash).split(":", 1)[-1]
    if not digest or "/" in digest or "\\" in digest or ".." in digest:
        return None
    cached = read_json(data_dir() / "ndi_cache" / f"{digest}.json", None)
    if not isinstance(cached, dict):
        return None
    parsed = cached.get("parsed")
    if not isinstance(parsed, dict):
        return None
    return parsed.get("fields") or {}


def safe_pdf_path(pdf: str | None) -> Path:
    """Resolve the record's PDF path; it must lie inside the data folder."""
    if not pdf:
        raise HTTPException(status_code=404, detail="no pdf")
    root = data_dir().resolve()
    p = Path(pdf)
    if not p.is_absolute():
        p = root / p
    p = p.resolve()
    if not p.is_relative_to(root) or not p.is_file():
        raise HTTPException(status_code=404, detail="pdf not found")
    return p


def render_page(path: Path) -> tuple[bytes, float, float]:
    with pymupdf.open(path) as doc:
        page = doc[0]
        png = page.get_pixmap(dpi=PAGE_DPI).tobytes("png")
        return png, page.rect.width, page.rect.height


def nace_usage(data: Path) -> dict:
    """Nace counters summed over all runs (processes) in data/usage.json."""
    return nace.usage_totals(data / "usage.json")


def _int(x: Any) -> int:
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


def intern_usage(data: Path) -> dict:
    out = {"runs": 0, "turns": 0, "drex_asks": 0, "input_tokens": 0, "output_tokens": 0}
    folder = data / "intern-logs"
    if not folder.is_dir():
        return out
    for f in sorted(folder.glob("*.jsonl")):
        for ev in read_jsonl(f):
            if not isinstance(ev, dict) or ev.get("type") != "run_end":
                continue
            usage = ev.get("usage") or {}
            out["runs"] += 1
            out["turns"] += _int(ev.get("turns"))
            out["drex_asks"] += _int((ev.get("system1") or {}).get("asks"))
            out["input_tokens"] += _int(usage.get("input_tokens"))
            out["output_tokens"] += _int(usage.get("output_tokens"))
    return out


def arena_usage(data: Path) -> dict:
    out = {
        "rounds": 0,
        "attacks": 0,
        "attacker_input_tokens": 0,
        "attacker_output_tokens": 0,
        "attacker_turns": 0,
    }
    for r in read_jsonl(data / "arena" / "rounds.jsonl"):
        if not isinstance(r, dict):
            continue
        att = r.get("attacker") or {}
        out["rounds"] += 1
        out["attacks"] += _int(r.get("n_attacks"))
        out["attacker_input_tokens"] += _int(att.get("input_tokens"))
        out["attacker_output_tokens"] += _int(att.get("output_tokens"))
        out["attacker_turns"] += _int(att.get("turns"))
    return out


# ---------- app ----------

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    arena = arena_dir()
    tasks = [
        asyncio.create_task(hub.tail_jsonl(arena / "attacks.jsonl", "attack")),
        asyncio.create_task(hub.tail_jsonl(arena / "rounds.jsonl", "round")),
    ]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="Quorum API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Page-Width", "X-Page-Height"],
)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/arena/rounds")
def rounds() -> list:
    return [r for r in read_jsonl(arena_dir() / "rounds.jsonl") if isinstance(r, dict)]


@app.get("/api/arena/attacks")
def attacks(round: int | None = Query(default=None)) -> list:
    recs = load_attacks()
    if round is None:
        return recs
    return [r for r in recs if _int(r.get("round")) == round]


@app.get("/api/arena/attacks/{key}")
def attack(key: str) -> dict:
    rec = find_attack(key)
    rec["parsed"] = load_parsed_fields(rec.get("doc_hash"))
    return rec


@app.get("/api/arena/attacks/{key}/page.png")
def attack_page(key: str) -> Response:
    rec = find_attack(key)
    path = safe_pdf_path(rec.get("pdf"))
    try:
        png, w, h = render_page(path)
    except Exception as exc:  # unreadable PDF
        raise HTTPException(status_code=404, detail="pdf unreadable") from exc
    return Response(
        content=png,
        media_type="image/png",
        headers={"X-Page-Width": f"{w:g}", "X-Page-Height": f"{h:g}"},
    )


@app.get("/api/arena/report")
def report() -> Any:
    return read_json(arena_dir() / "report.json", {})


@app.get("/api/usage")
def usage() -> dict:
    data = data_dir()
    return {"nace": nace_usage(data), "intern": intern_usage(data), "arena": arena_usage(data)}


@app.get("/api/history/{supplier_id}")
def supplier_history(supplier_id: str) -> dict:
    h = history.load_history(supplier_id, data_dir())
    if h is None:
        raise HTTPException(status_code=404, detail="no history for supplier")
    return {**history.history_summary(h), "wall_ms": h.get("wall_ms"), "mode": h.get("mode")}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await hub.connect(ws)
    try:
        await ws.send_json({"type": "hello"})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(ws)


_DIST = config.ROOT / "web" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="web")
