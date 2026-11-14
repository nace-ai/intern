"""Transcribe pages with Claude Code on the subscription (`claude -p`).

Usage: python bench/run_claude.py --model sonnet [--limit N] [--jobs 4]
Writes results/claude-<model>/<page_id>.json, one per page, and skips pages already done.
"""
import argparse, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import schema as census_schema, directory_schema

ROOT = Path(__file__).resolve().parent.parent


DATASETS = {
    "census": (census_schema, "data/reference/manifest.json", "data/pages", "results", "the people on it"),
    "directory": (directory_schema, "data/directory/manifest.json", "data/directory/pages", "results/directory", "the entries on it"),
}


def run_page(page, model, out_dir, ds="census"):
    mod, _, pages_dir, _, what = DATASETS[ds]
    SCHEMA, INSTRUCTIONS = mod.SCHEMA, mod.INSTRUCTIONS
    out = out_dir / f"{page['page_id']}.json"
    if out.exists():
        return page["page_id"], "cached"
    img = ROOT / pages_dir / f"{page['page_id']}.jpg"
    prompt = f"{INSTRUCTIONS}\n\nRead the image at {img} and return {what}."
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}  # bill the subscription
    cmd = [
        "claude", "-p", prompt,
        "--model", model,
        "--output-format", "json",
        "--json-schema", json.dumps(SCHEMA),
        "--allowedTools", "Read",
        "--no-session-persistence",
    ]
    t0 = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=ROOT, timeout=900)
    wall_ms = round((time.monotonic() - t0) * 1000)
    rec = {"page_id": page["page_id"], "system": f"claude-{model}", "wall_ms": wall_ms, "exit": proc.returncode}
    try:
        res = json.loads(proc.stdout)
        rec["cli"] = {k: res.get(k) for k in ("duration_ms", "duration_api_ms", "num_turns", "total_cost_usd", "usage", "modelUsage", "is_error", "subtype")}
        people = res.get("structured_output")
        if people is None and isinstance(res.get("result"), str):
            try:
                people = json.loads(res["result"])
            except json.JSONDecodeError:
                people = None
        rec["data"] = people
    except json.JSONDecodeError:
        rec["data"] = None
        rec["stdout"] = proc.stdout[-2000:]
    rec["stderr"] = proc.stderr[-2000:]
    out.write_text(json.dumps(rec, indent=1))
    d = rec.get("data") or {}
    return page["page_id"], f"{wall_ms/1000:.1f}s, {len(d.get('people') or d.get('entries') or [])} rows"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--dataset", default="census", choices=list(DATASETS))
    a = ap.parse_args()
    _, manifest, _, results, _ = DATASETS[a.dataset]
    pages = json.loads((ROOT / manifest).read_text())[: a.limit]
    out_dir = ROOT / results / f"claude-{a.model}"
    out_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(a.jobs) as ex:
        for pid, msg in ex.map(lambda p: run_page(p, a.model, out_dir, a.dataset), pages):
            print(pid, msg, flush=True)


if __name__ == "__main__":
    main()
