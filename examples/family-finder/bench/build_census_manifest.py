"""Rebuild data/reference/manifest.json (the 20 census benchmark pages) from NARA's public 1950 census search API.

The manifest holds real people's names (NARA's machine index and volunteer corrections), so it is git-ignored
and rebuilt locally with this script.

Usage: python bench/build_census_manifest.py [--pages 20] [--min-rows 20]

For every enumeration district in data/reference/kent-eds.json it searches Kent County, Delaware with each vowel
as the name query (the API needs a name), unions the results by page image, keeps pages with at least --min-rows
distinct volunteer-corrected rows, and picks --pages of them, one district at a time so the set spreads across
the county.
"""
import argparse, json, time, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://1950census.archives.gov/api/search"
S3 = "https://nara-1950-census.s3.amazonaws.com/"
OUT = ROOT / "data/reference/manifest.json"


def search(ed, name):
    q = urllib.parse.urlencode({"county": "Kent", "ed": ed, "state": "DE", "name": name, "size": 100})
    req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": "census-bench"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r).get("results") or []


def merge(rows_a, rows_b):
    seen, out = set(), []
    for r in (rows_a or []) + (rows_b or []):
        k = (r.get("row"), r.get("name"))
        if k not in seen:
            seen.add(k)
            out.append({"row": r.get("row"), "name": r.get("name")})
    return sorted(out, key=lambda r: (r["row"] is None, r["row"] or 0, r["name"] or ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=20)
    ap.add_argument("--min-rows", type=int, default=20)
    a = ap.parse_args()
    eds = [d["ed"] for d in json.loads((ROOT / "data/reference/kent-eds.json").read_text())["results"]]

    by_ed = {}
    for ed in eds:
        pages = {}
        for vowel in "aeiou":
            for r in search(ed, vowel):
                img = r["image"]
                p = pages.setdefault(img, {"scheduleId": r.get("scheduleId"), "state": r.get("state"),
                                           "county": r.get("county"), "ed": r.get("ed"), "names": [], "transcribe": []})
                p["names"] = merge(p["names"], r.get("names"))
                p["transcribe"] = merge(p["transcribe"], r.get("transcribe"))
            time.sleep(0.2)
        good = {img: p for img, p in pages.items()
                if len({t["row"] for t in p["transcribe"] if t["row"] is not None}) >= a.min_rows}
        by_ed[ed] = sorted(good.items())
        print(f"ED {ed}: {len(pages)} pages found, {len(good)} with >= {a.min_rows} corrected rows", flush=True)

    picked = []
    while len(picked) < a.pages and any(by_ed.values()):
        for ed in eds:
            if by_ed.get(ed) and len(picked) < a.pages:
                picked.append(by_ed[ed].pop(0))

    manifest = [{
        "page_id": Path(img).stem,
        "state": p["state"],
        "county": p["county"],
        "ed": p["ed"],
        "schedule_id": p["scheduleId"],
        "url": S3 + img,
        "nara_machine_names": p["names"],
        "volunteer_names": p["transcribe"],
    } for img, p in picked]
    OUT.write_text(json.dumps(manifest, indent=1))
    print(f"wrote {len(manifest)} pages to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
