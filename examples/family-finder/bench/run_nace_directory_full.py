"""Read the full resident section of a directory year with Nace Extract.

Usage: python bench/run_nace_directory_full.py 1928 [--jobs 6]
Renders each resident page at 400 dpi, uploads it, extracts with directory_schema, and writes
results/directory-full/<year>/<page_id>.json (skips pages already done). Pages with fewer than
12 address-like lines in the Internet Archive's OCR (ads, section breaks) are skipped.
"""
import argparse, json, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).parent))
from directory_schema import SCHEMA, INSTRUCTIONS
from run_nace import load_key, call, BASE, TERMINAL
from run_nace_crops import upload

ROOT = Path(__file__).resolve().parent.parent
PAT = re.compile(r"(^|\s)[hrb]\s*\d+")


def pages_for(year):
    d = fitz.open(ROOT / f"data/directory/wilmington-{year}.pdf")
    keep = json.loads((ROOT / "data/directory/full-run-pages.json").read_text())[year]
    return d, [i for i in keep if len([l for l in d[i].get_text().splitlines() if PAT.search(l)]) >= 12]


def run(key, d, year, i, out_dir, img_dir):
    pid = f"wilmington{year}-p{i:03d}"
    out = out_dir / f"{pid}.json"
    if out.exists():
        return pid, "cached"
    img = img_dir / f"{pid}.jpg"  # rendered up front in main()
    try:
        source, _ = upload(key, img)
    except Exception as e:  # keep going; a re-run picks the page up
        return pid, f"upload failed: {e}"
    body = {"source": source, "schema": SCHEMA, "instructions": INSTRUCTIONS, "parse_mode": "high",
            "citations": {"include_source_text": False}}
    t0 = time.monotonic()
    st, job = call("POST", f"{BASE}/extract?wait_seconds=100", key, body)
    while st in (200, 201, 202) and job.get("status") not in TERMINAL:
        time.sleep(3)
        st, job = call("GET", f"{BASE}/jobs/{job['job_id']}", key)
    if job.get("status") != "succeeded":
        return pid, f"{st} {job.get('status')} not saved"
    rec = {"page_id": pid, "year": year, "pdf_index": i, "system": "nace-high", "wall_ms": round((time.monotonic() - t0) * 1000),
           "data": (job.get("result") or {}).get("data"), "cost_usd": ((job.get("cost") or {}).get("total_usd")),
           "job_id": job.get("job_id")}
    out.write_text(json.dumps(rec, indent=1))
    return pid, f"{rec['wall_ms']/1000:.0f}s {len((rec['data'] or {}).get('entries') or [])} entries ${rec['cost_usd']}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("year")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--reverse", action="store_true", help="work from the back (a second worker on the same year)")
    ap.add_argument("--no-render", action="store_true", help="skip rendering (images already made)")
    a = ap.parse_args()
    key = load_key()
    d, idx = pages_for(a.year)
    out_dir = ROOT / "results/directory-full" / a.year
    img_dir = ROOT / "data/directory/full" / a.year
    out_dir.mkdir(parents=True, exist_ok=True)
    img_dir.mkdir(parents=True, exist_ok=True)
    print(f"{a.year}: {len(idx)} pages", flush=True)
    import threading
    lock = threading.Lock()  # PyMuPDF documents are not thread-safe for rendering

    def job(i):
        with lock:
            pass
        return run(key, d, a.year, i, out_dir, img_dir)

    # render serially up front so worker threads never touch the PDF concurrently
    if a.reverse:
        idx = idx[::-1]
    for i in ([] if a.no_render else idx):
        pid = f"wilmington{a.year}-p{i:03d}"
        img = img_dir / f"{pid}.jpg"
        if not img.exists():
            d[i].get_pixmap(dpi=400).save(img, jpg_quality=90)
            (img_dir / f"{pid}-ia-ocr.txt").write_text(d[i].get_text())
    with ThreadPoolExecutor(a.jobs) as ex:
        for pid, msg in ex.map(job, idx):
            print(pid, msg, flush=True)


if __name__ == "__main__":
    main()
