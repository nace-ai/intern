"""Nace Extract on cropped images (table only, and three overlapping strips) for a few pages.

Usage: python bench/run_nace_crops.py
Uploads each crop in data/crops/, extracts with the same schema, and writes
results/nace-crops/<crop name>.json plus results/nace-crops-merged/<page_id>.json
(table variant) and results/nace-strips-merged/<page_id>.json (strips merged by line number).
"""
import json, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from schema import SCHEMA, INSTRUCTIONS
from run_nace import load_key, call, BASE, TERMINAL

ROOT = Path(__file__).resolve().parent.parent
CROP_NOTE = (
    " This image is a crop of the table only (column headers may be cut off). Columns from left: line number, "
    "house number, serial number, farm questions, name (Last name first), relationship, race, sex, age, marital status, "
    "birthplace, then work questions and occupation. Take each person's line number from the printed number at the "
    "left or right edge of the row. A dash under a surname means the same surname as the line above."
)


def upload(key, path):
    st, grant = call("POST", f"{BASE}/upload-grants", key, {"path": f"census-crops/{path.name}", "ttl_seconds": 600})
    assert st in (200, 201), (st, grant)
    meta = json.dumps({"path": f"census-crops/{path.name}", "on_conflict": "new_version"})
    out = subprocess.run(["curl", "-s", grant["upload_url"], "-H", f"X-Upload-Token: {grant['token']}",
                          "-F", f"file=@{path}", "--form-string", f"metadata={meta}"], capture_output=True, text=True)
    job = json.loads(out.stdout)
    while job.get("status") not in TERMINAL and job.get("job_id"):
        time.sleep(2)
        _, job = call("GET", f"{BASE}/jobs/{job['job_id']}", key)
    f = (job.get("result") or {}).get("file") or {}
    return {"type": "workspace_file", "workspace_id": f.get("workspace_id") or grant["workspace_id"],
            "file_id": f.get("file_id") or f.get("id")}, job


def run_crop(key, path):
    out = ROOT / "results/nace-crops" / f"{path.stem}.json"
    if out.exists():
        return path.stem, "cached"
    source, up = upload(key, path)
    body = {"source": source, "schema": SCHEMA, "instructions": INSTRUCTIONS + CROP_NOTE,
            "parse_mode": "high", "citations": {"include_source_text": False}}
    t0 = time.monotonic()
    st, job = call("POST", f"{BASE}/extract?wait_seconds=100", key, body)
    while st in (200, 201, 202) and job.get("status") not in TERMINAL:
        time.sleep(3)
        st, job = call("GET", f"{BASE}/jobs/{job['job_id']}", key)
    rec = {"crop": path.stem, "wall_ms": round((time.monotonic() - t0) * 1000), "http": st,
           "job_status": job.get("status"), "data": (job.get("result") or {}).get("data"), "job": job, "upload": up}
    out.write_text(json.dumps(rec, indent=1))
    return path.stem, f"{st} {job.get('status')} {rec['wall_ms']/1000:.0f}s {len((rec['data'] or {}).get('people') or [])} people"


def merge():
    crops = {p.stem: json.loads(p.read_text()) for p in (ROOT / "results/nace-crops").glob("*.json")}
    pages = sorted({k.rsplit("-", 1)[0] for k in crops})
    for variant, parts in (("nace-crops-table", ["table"]), ("nace-crops-strips", ["strip1", "strip2", "strip3"])):
        d = ROOT / "results" / variant
        d.mkdir(exist_ok=True)
        for pid in pages:
            recs = [crops.get(f"{pid}-{p}") for p in parts]
            if not all(recs):
                continue
            by_line = {}
            for r in recs:
                for person in (r["data"] or {}).get("people") or []:
                    if isinstance(person.get("line"), int) and (person.get("full_name") or "").strip():
                        by_line.setdefault(person["line"], person)
            cost = sum(float(((r["job"].get("cost") or {}).get("total_usd")) or 0) for r in recs)
            total_ms = sum(((r["job"].get("timing") or {}).get("total_ms") or 0) for r in recs)
            (d / f"{pid}.json").write_text(json.dumps({
                "page_id": pid, "system": variant, "wall_ms": sum(r["wall_ms"] for r in recs),
                "data": {"people": [by_line[k] for k in sorted(by_line)]},
                "job": {"cost": {"total_usd": str(cost)}, "timing": {"total_ms": total_ms}}}, indent=1))


def main():
    key = load_key()
    (ROOT / "results/nace-crops").mkdir(parents=True, exist_ok=True)
    crops = sorted((ROOT / "data/crops").glob("*.jpg"))
    with ThreadPoolExecutor(4) as ex:
        for name, msg in ex.map(lambda p: run_crop(key, p), crops):
            print(name, msg, flush=True)
    merge()


if __name__ == "__main__":
    main()
