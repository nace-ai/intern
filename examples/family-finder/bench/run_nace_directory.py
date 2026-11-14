"""Nace Extract on the printed directory pages (the same rendered JPGs Claude reads, uploaded).

Usage: python bench/run_nace_directory.py [--limit N] [--jobs 4] [--mode high]
Writes results/directory/nace-<mode>/<page_id>.json and skips pages already done.
"""
import argparse, json, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from directory_schema import SCHEMA, INSTRUCTIONS
from run_nace import load_key, call, BASE, TERMINAL
from run_nace_crops import upload

ROOT = Path(__file__).resolve().parent.parent


def run_page(key, page, mode, out_dir):
    out = out_dir / f"{page['page_id']}.json"
    if out.exists():
        return page["page_id"], "cached"
    source, _ = upload(key, ROOT / "data/directory/pages" / f"{page['page_id']}.jpg")
    body = {"source": source, "schema": SCHEMA, "instructions": INSTRUCTIONS, "parse_mode": mode,
            "citations": {"include_source_text": False}}
    t0 = time.monotonic()
    st, job = call("POST", f"{BASE}/extract?wait_seconds=100", key, body)
    while st in (200, 201, 202) and job.get("status") not in TERMINAL:
        time.sleep(3)
        st, job = call("GET", f"{BASE}/jobs/{job['job_id']}", key)
    rec = {"page_id": page["page_id"], "system": f"nace-{mode}", "wall_ms": round((time.monotonic() - t0) * 1000),
           "http": st, "job_status": job.get("status"), "data": (job.get("result") or {}).get("data"), "job": job}
    out.write_text(json.dumps(rec, indent=1))
    return page["page_id"], f"{st} {job.get('status')} {rec['wall_ms']/1000:.0f}s {len((rec['data'] or {}).get('entries') or [])} entries"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--mode", default="high")
    a = ap.parse_args()
    key = load_key()
    pages = json.loads((ROOT / "data/directory/manifest.json").read_text())[: a.limit]
    out_dir = ROOT / "results/directory" / f"nace-{a.mode}"
    out_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(a.jobs) as ex:
        for pid, msg in ex.map(lambda p: run_page(key, p, a.mode, out_dir), pages):
            print(pid, msg, flush=True)


if __name__ == "__main__":
    main()
