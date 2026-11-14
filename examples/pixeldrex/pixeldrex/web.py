"""PixelDrex web app: type an idea, watch Drex paint it live, browse the gallery.

Run: .venv/bin/pixeldrex-web            (http://127.0.0.1:8000)
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from pixeldrex.cli import GALLERY, ROOT, run, run_life_saved

HERE = Path(__file__).resolve().parent
app = FastAPI(title="PixelDrex")
GALLERY.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=GALLERY), name="files")
_one_at_a_time = asyncio.Lock()  # keep Drex under its in-flight limit during a live demo


def _png(painting, bg=(255, 255, 255), scale: int = 10) -> str:
    buf = io.BytesIO()
    painting.flat(scale, bg=bg).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _serialize(kind: str, payload: dict) -> dict:
    out = {"kind": kind, "usage": payload.get("usage"), "elapsed": round(payload.get("elapsed", 0), 2)}
    if kind == "spec":
        out["spec"] = payload["spec"]
    elif kind == "background":
        out["img"] = _png(payload["result"])
    elif kind == "sprite":
        out["name"] = payload["name"]
        out["img"] = _png(payload["result"], bg=(205, 210, 220))
    elif kind == "motion":
        t = payload["result"]
        out["name"] = payload["name"]
        out["track"] = {"x": t.xs, "y": t.ys, "size": t.sizes}
    elif kind == "done":
        out["frames"] = payload.get("frames")
    return out


def _gif_url(stats: dict) -> str:
    return "/files/" + Path(stats["dir"]).name + "/anim.gif"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "web" / "index.html")


@app.get("/api/paint")
async def paint(
    idea: str = Query(..., min_length=2, max_length=200), frames: int = Query(16, ge=4, le=32), res: int = Query(64)
):
    if res not in (16, 32, 64, 128):
        return JSONResponse({"error": "res must be 16, 32, 64 or 128"}, status_code=422)
    queue: asyncio.Queue = asyncio.Queue()

    def on_event(kind: str, payload: dict) -> None:
        queue.put_nowait(_serialize(kind, payload))

    async def worker() -> None:
        try:
            async with _one_at_a_time:
                stats = await run(idea, frames=frames, res=res, quiet=True, on_event=on_event)
            queue.put_nowait({"kind": "result", "stats": stats, "gif": _gif_url(stats)})
        except Exception as e:  # surface the error to the page instead of hanging
            queue.put_nowait({"kind": "error", "message": f"{type(e).__name__}: {e}"[:400]})
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(worker())

    async def stream():
        try:
            while (item := await queue.get()) is not None:
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            if not task.done():
                await task

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


def _life_event(kind: str, payload: dict) -> dict:
    if kind == "world":
        return {"kind": "world", "world": payload["world"]}
    if kind == "sources":
        return {"kind": "sources", "sources": payload["sources"]}
    life, info = payload["life"], payload["info"]
    buf = io.BytesIO()
    life.image(scale=10).save(buf, format="PNG")
    return {
        "kind": "frame",
        "step": payload["step"],
        "elapsed": round(payload["elapsed"], 2),
        "img": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
        "census": life.census(),
        "new_questions": info["new_questions"],
        "unique_questions": life.asked + len(life.grid) ** 2,
        "cell_decisions": len(life.grid) ** 2 * (payload["step"] + 1),
    }


WORLDS = ROOT / "worlds"
FEATURED = ["forest-fire-cycle", "zombie-outbreak-city", "news-bird-flu", "news-wildfire", "coral-reef-dynamics"]


@app.get("/api/worlds")
def worlds() -> JSONResponse:
    """Saved worlds that can be replayed (known-good rules)."""
    out = []
    names = sorted(
        (p.stem for p in WORLDS.glob("*.json")),
        key=lambda n: (n not in FEATURED, FEATURED.index(n) if n in FEATURED else 0, n),
    )
    for name in names:
        try:
            w = json.loads((WORLDS / f"{name}.json").read_text())
            out.append({"name": name, "title": w.get("title", name), "states": len(w.get("states", []))})
        except (OSError, json.JSONDecodeError):
            continue
    return JSONResponse(out)


@app.get("/api/life")
async def life(
    idea: str = Query("", max_length=200),
    size: int = Query(32, ge=8, le=48),
    steps: int = Query(40, ge=5, le=120),
    news: bool = False,
    world: str | None = None,
):
    world_path = None
    if world:
        candidate = (WORLDS / f"{world}.json").resolve()
        if candidate.parent != WORLDS.resolve() or not candidate.exists():
            return JSONResponse({"error": f"unknown world {world!r}"}, status_code=404)
        world_path = str(candidate)
    elif len(idea.strip()) < 2:
        return JSONResponse({"error": "give an idea or a saved world"}, status_code=422)
    queue: asyncio.Queue = asyncio.Queue()

    def on_event(kind: str, payload: dict) -> None:
        queue.put_nowait(_life_event(kind, payload))

    async def worker() -> None:
        try:
            async with _one_at_a_time:
                stats = await run_life_saved(
                    None if (news or world_path) else idea,
                    size=size,
                    steps=steps,
                    on_event=on_event,
                    quiet=True,
                    news=idea if news else None,
                    world_path=world_path,
                )
            queue.put_nowait({"kind": "result", "stats": stats, "gif": _gif_url(stats)})
        except Exception as e:
            queue.put_nowait({"kind": "error", "message": f"{type(e).__name__}: {e}"[:400]})
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(worker())

    async def stream():
        try:
            while (item := await queue.get()) is not None:
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            if not task.done():
                await task

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.get("/api/gallery")
def gallery(limit: int = 60) -> JSONResponse:
    items = []
    for d in sorted(GALLERY.iterdir(), reverse=True):
        f = d / "stats.json"
        if d.is_dir() and f.exists() and (d / "anim.gif").exists():
            s = json.loads(f.read_text())
            items.append(
                {
                    "title": s["spec"]["title"],
                    "idea": s.get("idea"),
                    "gif": _gif_url(s),
                    "mode": s.get("mode", "scene"),
                    "decisions": s.get("cell_decisions", s["drex"]["questions"]),
                    "calls": s["drex"]["calls"],
                    "seconds": s["seconds"],
                    "cost": s["drex"]["cost_usd"],
                }
            )
        if len(items) >= limit:
            break
    return JSONResponse(items)


def main() -> None:
    import uvicorn

    host, port = os.getenv("HOST", "127.0.0.1"), int(os.getenv("PORT", "8000"))
    missing = [k for k in ("NACE_API_KEY", "DEEPINFRA_API_KEY") if not os.getenv(k)]
    if missing:
        print(f"WARNING: {', '.join(missing)} not set - add them to {ROOT / '.env'} or GIFs will fail.", flush=True)
    print(f"PixelDrex web app: open http://{host}:{port} in your browser  (Ctrl+C to stop)", flush=True)
    uvicorn.run("pixeldrex.web:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
