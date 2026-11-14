"""`pixeldrex "an idea"` -> the director writes a scene, Drex paints and animates it, you get a GIF.

Examples:
  pixeldrex "a cat chasing a mouse"
  pixeldrex "a ufo abducting a cow" --frames 20
  pixeldrex "fireworks over a city" --spec-only          # just show the director's scene spec
  pixeldrex --spec out/gallery/<run>/spec.json            # re-paint a saved spec, no director call
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import Image

from pixeldrex.director import SceneSpec, direct
from pixeldrex.drex import Drex
from pixeldrex.llm import DeepInfra
from pixeldrex.pixels import save_gif, scene_gif

ROOT = Path(__file__).resolve().parent.parent
GALLERY = ROOT / "out" / "gallery"


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "scene"


class Progress:
    def __init__(self, quiet: bool = False):
        self.t0 = time.perf_counter()
        self.quiet = quiet

    def log(self, msg: str) -> None:
        if not self.quiet:
            print(f"  [{time.perf_counter() - self.t0:5.1f}s] {msg}", flush=True)

    def on_event(self, kind: str, payload: dict) -> None:
        if kind == "background":
            self.log("background painted (256 pixel decisions)")
        elif kind == "sprite":
            self.log(f"sprite '{payload['name']}' painted part by part (one request)")
        elif kind == "motion":
            tr = payload["result"]
            self.log(
                f"motion for '{payload['name']}' decided: "
                f"({tr.xs[0]:.0f},{tr.ys[0]:.0f}) -> ({tr.xs[-1]:.0f},{tr.ys[-1]:.0f}), "
                f"size {tr.sizes[0]:.0f} -> {tr.sizes[-1]:.0f}"
            )
        elif kind == "done":
            self.log(f"composited {payload['frames']} frames")


async def run(
    idea: str | None,
    *,
    spec_path: str | None = None,
    frames: int = 16,
    res: int = 64,
    ms: int = 90,
    outline: bool = True,
    spec_only: bool = False,
    quiet: bool = False,
    on_event=None,
) -> dict:
    """Full pipeline. Returns a dict with the spec, output paths and stats (also used by the web app)."""
    prog = Progress(quiet)
    t_start = time.perf_counter()
    director_stats = None

    if spec_path:
        spec = SceneSpec.from_dict(json.loads(Path(spec_path).read_text()))
        idea = idea or spec.scene
        prog.log(f"loaded spec '{spec.title}'")
    else:
        async with DeepInfra() as di:
            prog.log(f"director is planning: {idea!r}")
            spec = await direct(di, idea)
            director_stats = di.usage.summary()
        prog.log(
            f"director: '{spec.title}' with {len(spec.sprites)} sprite(s): " + ", ".join(s.name for s in spec.sprites)
        )
    if on_event:
        on_event("spec", {"spec": spec.to_dict()})
    if spec_only:
        return {"spec": spec.to_dict(), "director": director_stats}

    drex = Drex()

    def events(kind: str, payload: dict) -> None:
        prog.on_event(kind, payload)
        if on_event:
            on_event(kind, {**payload, "usage": drex.usage.summary(), "elapsed": time.perf_counter() - t_start})

    async with drex:
        gif_frames, meta = await scene_gif(
            drex,
            scene=spec.scene,
            background=spec.background,
            sprites=spec.sprites,
            frames=frames,
            outline=outline,
            on_event=events,
            sprite_px=res,
            canvas=2 * res,
        )
    total_s = time.perf_counter() - t_start

    run_dir = GALLERY / f"{datetime.now():%Y%m%d-%H%M%S}-{slugify(spec.title)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    save_gif(gif_frames, run_dir / "anim.gif", ms=ms)
    w, h = gif_frames[0].size
    picks = gif_frames[:: max(1, len(gif_frames) // 6)][:6]
    sheet = Image.new("RGB", (w // 2 * len(picks), h // 2), "white")
    for i, f in enumerate(picks):
        sheet.paste(f.resize((w // 2, h // 2), Image.NEAREST), (i * w // 2, 0))
    sheet.save(run_dir / "sheet.png")
    for j, sp in enumerate(meta["sprites"]):
        sp.flat(10, bg=(205, 210, 220)).save(run_dir / f"sprite{j}.png")
    meta["background"].flat(10).save(run_dir / "background.png")

    drex_stats = drex.usage.summary()
    stats = {
        "idea": idea,
        "spec": spec.to_dict(),
        "frames": len(gif_frames),
        "seconds": round(total_s, 1),
        "drex": drex_stats,
        "director": director_stats,
        "tracks": [
            {"x": [round(v, 2) for v in t.xs], "y": [round(v, 2) for v in t.ys], "size": [round(v, 2) for v in t.sizes]}
            for t in meta["tracks"]
        ],
        "dir": str(run_dir.relative_to(ROOT)),
        "gif": str((run_dir / "anim.gif").relative_to(ROOT)),
    }
    (run_dir / "spec.json").write_text(json.dumps(spec.to_dict(), indent=2))
    (run_dir / "stats.json").write_text(json.dumps(stats, indent=2))
    return stats


async def run_life_saved(
    idea: str,
    *,
    size: int = 32,
    steps: int = 40,
    temperature: float = 0.8,
    on_event=None,
    quiet: bool = False,
    world_path: str | None = None,
    doc: str | None = None,
    news: str | None = None,
) -> dict:
    """Drex Life: design a world from an idea, run it, save GIF + stats to the gallery."""
    from pixeldrex.life import World, run_life

    prog = Progress(quiet)
    t_start = time.perf_counter()
    world_obj = None

    doc_meta = news_meta = None
    if world_path:
        world_obj = World.from_dict(json.loads(Path(world_path).read_text()))
        idea = idea or world_obj.title
    elif doc:
        from pixeldrex.ndi import world_from_document

        world_obj, doc_meta = await world_from_document(doc, idea, on_line=prog.log)
        idea = idea or f"world from {Path(doc).name}"
    elif news:
        from pixeldrex.news import world_from_news

        world_obj, news_meta = await world_from_news(news, on_line=prog.log)
        idea = idea or f"this week's news: {news}"
        if on_event:
            on_event("sources", {"sources": news_meta["sources"]})

    def on_world(w) -> None:
        prog.log(f"world '{w.title}': " + ", ".join(f"{s.name}={s.color}" for s in w.states))
        if on_event:
            on_event("world", {"world": w.to_dict()})

    def on_frame(i: int, life, info: dict) -> None:
        if i == 0:
            prog.log(f"Drex seeded the {size}x{size} world ({size * size} cell decisions)")
        elif i % 10 == 0:
            prog.log(f"step {i}: {life.cell_updates:,} cell decisions from {life.asked:,} unique questions")
        if on_event:
            on_event("frame", {"step": i, "life": life, "info": info, "elapsed": time.perf_counter() - t_start})

    if world_obj is None:
        prog.log(f"director is designing a world: {idea!r}")
    life, frames, usage = await run_life(
        idea, n=size, steps=steps, temperature=temperature, on_frame=on_frame, on_world=on_world, world=world_obj
    )
    total_s = time.perf_counter() - t_start
    run_dir = GALLERY / f"{datetime.now():%Y%m%d-%H%M%S}-life-{slugify(life.world.title)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    save_gif(frames, run_dir / "anim.gif", ms=80)  # frames include blended in-betweens
    cell_decisions = size * size * (steps + 1)  # seeding + every step
    stats = {
        "mode": "life",
        "idea": idea,
        "spec": {"title": life.world.title},
        "world": life.world.to_dict(),
        "size": size,
        "steps": steps,
        "seconds": round(total_s, 1),
        "cell_decisions": cell_decisions,
        "unique_questions": life.asked + size * size,
        "drex": usage["drex"],
        "director": usage["director"],
        "dir": str(run_dir.relative_to(ROOT)),
        "gif": str((run_dir / "anim.gif").relative_to(ROOT)),
    }
    (run_dir / "stats.json").write_text(json.dumps(stats, indent=2))
    (run_dir / "world.json").write_text(json.dumps(life.world.to_dict(), indent=2))
    if doc_meta:
        (run_dir / "document.json").write_text(json.dumps(doc_meta, indent=2))
        stats["document"] = doc_meta["document"]
        (run_dir / "stats.json").write_text(json.dumps(stats, indent=2))
    if news_meta:
        (run_dir / "news.json").write_text(json.dumps(news_meta, indent=2))
        stats["news"] = news_meta
        (run_dir / "stats.json").write_text(json.dumps(stats, indent=2))

    # Build compact report
    census_first = life.census_history[0]
    census_last = life.census_history[-1] if life.census_history else {}
    extinct = [state for state, count in census_last.items() if count == 0 and census_first.get(state, 0) > 0]
    changed_last_5 = sum(life.changed_history[-5:])
    report = {
        "title": life.world.title,
        "steps": life.steps,
        "census_first": census_first,
        "census_last": census_last,
        "extinct": extinct,
        "changed_cells_last_5_steps": changed_last_5,
        "gif": str((run_dir / "anim.gif").relative_to(ROOT)),
    }
    stats["report"] = report

    return stats


def life_main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(
        prog="pixeldrex life", description="Drex Life: every pixel is an agent; the laws of physics are plain English."
    )
    ap.add_argument("idea", nargs="?", help="a world, e.g. 'a zombie outbreak in a city'")
    ap.add_argument(
        "--world", metavar="PATH", dest="world_path", help="load a world JSON and run without calling the director"
    )
    ap.add_argument(
        "--doc",
        metavar="PDF_OR_URL",
        help="build the world from a document: NDI extracts its actors and interactions with page "
        "citations, and every rule cites its page",
    )
    ap.add_argument(
        "--news",
        metavar="TOPIC",
        help="build the world from this week's news: Keenable searches and reads the articles, and every "
        "rule cites its sources",
    )
    ap.add_argument("--save-world", metavar="PATH", help="also save the world JSON here (e.g. worlds/reef.json)")
    ap.add_argument("--size", type=int, default=32)
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("--temp", type=float, default=0.8, help="sampling temperature over Drex's probabilities")
    ap.add_argument("--report", action="store_true", help="print a compact JSON report to stdout after the run")
    args = ap.parse_args(argv)
    if not args.idea and not args.world_path and not args.doc and not args.news:
        ap.error('give an idea, e.g. pixeldrex life "a zombie outbreak", or --world FILE, or --doc PDF')
    idea = args.idea or "(from world)"
    if not args.report:
        print(f"PixelDrex Life: {idea}", flush=True)
    res = asyncio.run(
        run_life_saved(
            args.idea,
            size=args.size,
            steps=args.steps,
            temperature=args.temp,
            world_path=args.world_path,
            doc=args.doc,
            news=args.news,
            quiet=args.report,
        )
    )
    if args.save_world:
        Path(args.save_world).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save_world).write_text(json.dumps(res["world"], indent=2))
    if args.report:
        print(json.dumps(res.get("report", {})))
        return
    print(f"\n  GIF: {res['gif']}")
    print(
        f"  {res['cell_decisions']:,} cell decisions from {res['unique_questions']:,} unique Drex questions in "
        f"{res['drex']['calls']} calls · {res['seconds']}s · Drex ≈ ${res['drex']['cost_usd']:.4f}"
    )


def god_main(argv: list[str]) -> None:
    from pixeldrex.god import god

    ap = argparse.ArgumentParser(
        prog="pixeldrex god", description="Intern becomes the god of a Drex Life world and tunes its laws of physics."
    )
    ap.add_argument("idea", nargs="?", default="")
    ap.add_argument("--world", help="start from a saved world JSON instead of designing one")
    ap.add_argument("--size", type=int, default=24)
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--max-runs", type=int, default=4)
    args = ap.parse_args(argv)
    if not args.idea and not args.world:
        ap.error("give an idea or --world FILE")
    print(f"PixelDrex god mode: {args.idea or args.world}", flush=True)
    res = asyncio.run(god(args.idea, size=args.size, steps=args.steps, max_runs=args.max_runs, world_file=args.world))
    print("\n--- original rules ---\n" + (res["original_rules"] or ""))
    print("\n--- final rules ---\n" + (res["final_rules"] or ""))
    print("\n--- Intern says ---\n" + (res["final_reply"] or "(no reply)"))
    print(
        f"\n  {len(res['reports'])} world runs · {res['turns']} Intern turns · {res['seconds']}s · "
        f"{res['system1_asks']} System 1 (Drex) asks · log {res['log']}"
    )


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "life":
        return life_main(argv[1:])
    if argv and argv[0] == "god":
        return god_main(argv[1:])
    ap = argparse.ArgumentParser(prog="pixeldrex", description="Drex paints and animates your idea as a pixel-art GIF.")
    ap.add_argument("idea", nargs="?", help="what to animate, e.g. 'a cat chasing a mouse'")
    ap.add_argument("--spec", help="paint a saved scene spec JSON instead of calling the director")
    ap.add_argument("--frames", type=int, default=16)
    ap.add_argument(
        "--res",
        type=int,
        default=64,
        choices=[16, 32, 64, 128],
        help="sprite resolution: 16x16 parts, then one Drex edge-smoothing pass per doubling (canvas = 2x)",
    )
    ap.add_argument("--ms", type=int, default=90, help="milliseconds per frame")
    ap.add_argument("--no-outline", action="store_true")
    ap.add_argument("--spec-only", action="store_true", help="print the director's scene spec and stop")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    args = ap.parse_args(argv)
    if not args.idea and not args.spec:
        ap.error('give an idea, e.g. pixeldrex "a cat chasing a mouse", or --spec FILE')

    print(f"PixelDrex: {args.idea or args.spec}", flush=True)
    res = asyncio.run(
        run(
            args.idea,
            spec_path=args.spec,
            frames=args.frames,
            res=args.res,
            ms=args.ms,
            outline=not args.no_outline,
            spec_only=args.spec_only,
            quiet=args.json,
        )
    )
    if args.json or args.spec_only:
        print(json.dumps(res, indent=2))
        return
    d = res["drex"]
    print(f"\n  GIF: {res['gif']}")
    print(
        f"  {d['questions']:,} Drex decisions in {d['calls']} calls · {res['seconds']}s total · "
        f"Drex ≈ ${d['cost_usd']:.4f}"
        + (f" · director ${res['director']['cost_usd']:.5f}" if res.get("director") else "")
    )


if __name__ == "__main__":
    main(sys.argv[1:])
