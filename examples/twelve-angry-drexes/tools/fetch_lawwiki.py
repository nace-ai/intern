#!/usr/bin/env python3
"""Build the statute snapshot the trials read: data/statutes/*.json and index.json.

    python3 tools/fetch_lawwiki.py               # from the public LawWiki API
    python3 tools/fetch_lawwiki.py --source db   # straight from the LawWiki database (owner only)
    python3 tools/fetch_lawwiki.py --verify      # compare every file with data/statutes/db_snapshot.json

The trials read this snapshot and never call LawWiki while they run, so nothing on the public site
(rate limits, bot protection) can block a demo. The section list was chosen with the database's own
search and metadata; `db_snapshot.json` records, for each section, the MD5 and length of the text as
the database holds it, and `--verify` proves each file here is that same text.

`--source db` reads the `statutes` table over the database's REST interface. It needs two environment
variables set in your own shell and never stored here: LAWWIKI_SUPABASE_URL (or NEXT_PUBLIC_SUPABASE_URL)
and LAWWIKI_SUPABASE_SERVICE_KEY (or SUPABASE_SERVICE_ROLE_KEY). It only sends GET requests.

Standard library only. Statute text is public law; each file keeps its citation, source and LawWiki link.
"""
import argparse
import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.lawwiki.ai/api/v1"
UA = "twelve-angry-drexes/0.1 (hackathon demo)"
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "statutes"
WANT = [  # (jurisdiction, code, section)
    ("CA", "CIV", "1950.5"), ("CA", "CIV", "1950.7"), ("CA", "CIV", "1942"), ("CA", "CIV", "1941.1"),
    ("CA", "CCP", "116.221"), ("CA", "CCP", "116.220"), ("CA", "CCP", "116.540"),
    ("CA", "LAB", "201"), ("CA", "LAB", "202"), ("CA", "LAB", "203"), ("CA", "LAB", "204"), ("CA", "LAB", "208"),
    ("CA", "LAB", "218.5"), ("CA", "LAB", "218.6"), ("CA", "LAB", "226"),
    ("FL", "FS", "83.49"), ("FL", "FS", "83.48"), ("FL", "FS", "83.56"), ("FL", "FS", "83.51"),
    ("FL", "FS", "83.491"), ("FL", "FS", "83.43"),
]
KEEP = ("jurisdiction", "code", "code_name", "section", "citation", "title", "status", "text", "history",
        "source_url", "lawwiki_url", "source_version", "last_synced_at", "content_hash")
DB_COLUMNS = ("ref_key,section_number,citation,title,generated_title,status,raw_text,history,source_url,"
              "source_version,last_seen_at,content_hash,codes(abbreviation,name,state_id)")


def name_of(jur, code, sec):
    return f"{jur}_{code}_{sec}.json".replace("/", "-")


def from_api(jur, code, sec):
    """One section from the public API. The API allows 20 requests a minute, so a 429 waits and tries again."""
    req = urllib.request.Request(f"{BASE}/statutes/{jur}/{code}/{sec}", headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())["data"]
            return {k: d.get(k) for k in KEEP}
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise
            time.sleep(25)


def db_config():
    url = os.environ.get("LAWWIKI_SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or ""
    key = os.environ.get("LAWWIKI_SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""
    host = urllib.parse.urlsplit(url).hostname or ""
    if not key or not url.startswith("https://") or not host.endswith(".supabase.co"):
        sys.exit("--source db needs LAWWIKI_SUPABASE_URL (an https *.supabase.co address) and LAWWIKI_SUPABASE_SERVICE_KEY "
                 "in the environment")
    return url.rstrip("/"), key


def from_db(jur, code, sec, url, key):
    query = urllib.parse.urlencode({"ref_key": f"eq.{jur}:{code}:{sec}", "status": "eq.active", "select": DB_COLUMNS, "limit": "1"})
    req = urllib.request.Request(f"{url}/rest/v1/statutes?{query}",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = json.loads(r.read())
    if not rows:
        raise KeyError("no active row")
    d = rows[0]
    return {"jurisdiction": jur, "code": code, "code_name": (d.get("codes") or {}).get("name"), "section": d["section_number"],
            "citation": d["citation"], "title": d.get("title") or d.get("generated_title"), "status": d["status"],
            "text": d["raw_text"], "history": d.get("history"), "source_url": d.get("source_url"),
            "lawwiki_url": f"https://www.lawwiki.ai/{jur.lower()}/{code.lower()}/{sec}", "source_version": d.get("source_version"),
            "last_synced_at": d.get("last_seen_at"), "content_hash": d.get("content_hash")}


def verify():
    """Each file's text must be the text the database holds: same MD5, same length, same content hash."""
    snap = json.loads((OUT / "db_snapshot.json").read_text())
    bad = 0
    for jur, code, sec in WANT:
        want = snap["sections"].get(f"{jur}:{code}:{sec}")
        try:
            row = json.loads((OUT / name_of(jur, code, sec)).read_text())
        except OSError:
            row = None
        if not want or not row:
            print(f"MISSING  {jur}:{code}:{sec}")
            bad += 1
            continue
        text = row["text"] or ""
        ok = (hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest() == want["md5"] and len(text) == want["chars"]
              and (row.get("content_hash") or "").startswith(want["content_hash_prefix"]))
        bad += not ok
        if not ok:
            print(f"DIFFERS  {jur}:{code}:{sec}")
    print(f"{len(WANT) - bad} of {len(WANT)} sections match the database rows recorded {snap['queried_at']}")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=("api", "db"), default="api")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    if args.verify:
        sys.exit(verify())
    OUT.mkdir(parents=True, exist_ok=True)
    db = db_config() if args.source == "db" else None
    index, missing = [], []
    for jur, code, sec in WANT:
        try:
            row = from_db(jur, code, sec, *db) if db else from_api(jur, code, sec)
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError) as e:
            missing.append(f"{jur}/{code}/{sec}: {e}")
            continue
        row["fetched_at"], row["fetched_from"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), args.source
        (OUT / name_of(jur, code, sec)).write_text(json.dumps(row, indent=1, ensure_ascii=False))
        index.append({"file": name_of(jur, code, sec), "citation": row["citation"], "title": row["title"],
                      "chars": len(row["text"] or ""), "lawwiki_url": row["lawwiki_url"]})
        if not db:
            time.sleep(3.2)  # stay under the public API's 20 requests a minute
    if index:
        (OUT / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False))
    for m in missing:
        print("MISS", m[:120])
    print(f"{len(index)} fetched from {args.source}, {len(missing)} missing")
    if (OUT / "db_snapshot.json").is_file():
        sys.exit(verify() or (1 if missing else 0))


if __name__ == "__main__":
    main()
