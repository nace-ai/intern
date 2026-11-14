"""Transcribe pages with Nace Perception Extract, reading each page from NARA's public URL.

Usage: python bench/run_nace.py [--limit N] [--jobs 4] [--mode high]
Needs NACE_API_KEY in .env.local. Writes results/nace-<mode>/<page_id>.json and skips pages already done.
"""
import argparse, json, os, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from schema import SCHEMA, INSTRUCTIONS

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://console.nace.ai/v1/documents"
TERMINAL = {"succeeded", "failed", "cancelled"}


def load_key():
    env = ROOT / ".env.local"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("NACE_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    return os.environ.get("NACE_API_KEY")


def call(method, url, key, body=None):
    for attempt in range(6):
        req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 503, 529) and attempt < 5:
                time.sleep(int(e.headers.get("retry-after-ms") or 2000) / 1000)
                continue
            return e.code, json.loads(e.read() or b"{}")


def run_page(page, key, mode, out_dir):
    out = out_dir / f"{page['page_id']}.json"
    if out.exists():
        return page["page_id"], "cached"
    body = {
        "source": {"type": "url", "url": page["url"], "file_name": f"{page['page_id']}.jpg"},
        "schema": SCHEMA,
        "instructions": INSTRUCTIONS,
        "parse_mode": mode,
        "citations": {"include_source_text": False},
    }
    t0 = time.monotonic()
    status, job = call("POST", f"{BASE}/extract?wait_seconds=100", key, body)
    while status in (200, 201, 202) and job.get("status") not in TERMINAL:
        time.sleep(3)
        status, job = call("GET", f"{BASE}/jobs/{job['job_id']}", key)
    wall_ms = round((time.monotonic() - t0) * 1000)
    rec = {"page_id": page["page_id"], "system": f"nace-{mode}", "wall_ms": wall_ms, "http": status,
           "job_status": job.get("status"), "data": (job.get("result") or {}).get("data"), "job": job}
    if job.get("status") == "succeeded" and not job.get("usage_final"):
        # cost settles after the job; read once more so the record holds the final charge
        time.sleep(5)
        _, later = call("GET", f"{BASE}/jobs/{job['job_id']}", key)
        rec["job_settled"] = {k: later.get(k) for k in ("cost", "credits", "usage_final", "timing")}
    out.write_text(json.dumps(rec, indent=1))
    n = len((rec["data"] or {}).get("people") or [])
    return page["page_id"], f"{status} {job.get('status')} {wall_ms/1000:.1f}s, {n} people"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--mode", default="high")
    a = ap.parse_args()
    key = load_key()
    if not key:
        sys.exit("No NACE_API_KEY in .env.local")
    pages = json.loads((ROOT / "data/reference/manifest.json").read_text())[: a.limit]
    out_dir = ROOT / "results" / f"nace-{a.mode}"
    out_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(a.jobs) as ex:
        for pid, msg in ex.map(lambda p: run_page(p, key, a.mode, out_dir), pages):
            print(pid, msg, flush=True)


if __name__ == "__main__":
    main()
