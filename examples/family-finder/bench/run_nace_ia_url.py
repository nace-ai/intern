"""Nace Extract on Internet Archive directory pages by URL (no rendering, no upload).

Usage: python bench/run_nace_ia_url.py <ia_identifier> [--jobs 6] [--start-frac 0.06] [--end-frac 0.95]
Writes results/directory-nace-url/<identifier>/n<page>.json; skips pages already done. Stops on 402 (no credit).
"""
import argparse, json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from directory_schema import SCHEMA, INSTRUCTIONS
from run_nace import load_key, call, BASE, TERMINAL

ROOT = Path(__file__).resolve().parent.parent
STOP = {"flag": False}


def run(key, ident, n, out_dir):
    out = out_dir / f"n{n:04d}.json"
    if out.exists() or STOP["flag"]:
        return n, "skip"
    url = f"https://archive.org/download/{ident}/page/n{n}_w1800.jpg"
    body = {"source": {"type": "url", "url": url, "file_name": f"{ident}-n{n}.jpg"}, "schema": SCHEMA,
            "instructions": INSTRUCTIONS + " If the page has no resident listing, return no entries.",
            "parse_mode": "high", "citations": {"include_source_text": False}}
    t0 = time.monotonic()
    st, job = call("POST", f"{BASE}/extract?wait_seconds=100", key, body)
    if st == 402:
        STOP["flag"] = True
        return n, "402 out of credit, stopping"
    while st in (200, 201, 202) and job.get("status") not in TERMINAL:
        time.sleep(3)
        st, job = call("GET", f"{BASE}/jobs/{job['job_id']}", key)
    if job.get("status") != "succeeded":
        return n, f"{st} {job.get('status')}"
    rec = {"identifier": ident, "page": n, "image_url": url, "system": "nace-high", "wall_ms": round((time.monotonic() - t0) * 1000),
           "cost_usd": (job.get("cost") or {}).get("total_usd"), "data": (job.get("result") or {}).get("data")}
    out.write_text(json.dumps(rec))
    return n, f"{len((rec['data'] or {}).get('entries') or [])} entries ${rec['cost_usd']}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("identifier")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--start-frac", type=float, default=0.06)
    ap.add_argument("--end-frac", type=float, default=0.95)
    a = ap.parse_args()
    key = load_key()
    total = int(json.load(urllib.request.urlopen(f"https://archive.org/metadata/{a.identifier}/metadata/imagecount", timeout=60))["result"])
    pages = list(range(int(total * a.start_frac), int(total * a.end_frac)))
    out_dir = ROOT / "results/directory-nace-url" / a.identifier
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"{a.identifier}: {len(pages)} pages", flush=True)
    with ThreadPoolExecutor(a.jobs) as ex:
        for n, msg in ex.map(lambda p: run(key, a.identifier, p, out_dir), pages):
            print(a.identifier, n, msg, flush=True)


if __name__ == "__main__":
    main()
