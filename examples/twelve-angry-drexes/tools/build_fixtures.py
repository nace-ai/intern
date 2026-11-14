#!/usr/bin/env python3
"""Write the statute fixtures the public repo ships: data/statutes/<JUR>_<CODE>_<SECTION>.json and index.json.

    python3 tools/build_fixtures.py            # add every missing fixture from the local store, rebuild index.json
    python3 tools/build_fixtures.py --verify   # compare fixture text with the database rows recorded in db_*.json

Which sections: every statute a case cites (mocks/tad-sim.js), every role anchor (engine/roles.py) and the
21 sections picked with the database's search. Text comes from the local store built from LawWiki's own
snapshots (tools/ingest_lawwiki_local.py); nothing is fetched. An existing fixture is never overwritten.

Titles: California's official source has no section headings, so LawWiki's database title is used where it
was recorded (data/statutes/db_snapshot.json, db_titles_20261114.json); otherwise the heading in the store.
`--verify` proves each fixture holds the text the database holds: same MD5, same length.
"""
import hashlib
import json
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import lawstore, roles  # noqa: E402

OUT = ROOT / "data" / "statutes"
WANT = """CA/CIV/1950.5 CA/CIV/1950.7 CA/CIV/1942 CA/CIV/1941.1 CA/CCP/116.221 CA/CCP/116.220 CA/CCP/116.540 CA/LAB/201 CA/LAB/202
CA/LAB/203 CA/LAB/204 CA/LAB/208 CA/LAB/218.5 CA/LAB/218.6 CA/LAB/226 FL/FS/83.49 FL/FS/83.48 FL/FS/83.56 FL/FS/83.51 FL/FS/83.491
FL/FS/83.43""".split()


def db_rows() -> dict:
    """{JUR/CODE/SECTION: {"md5", "chars", "title"}} as the LawWiki database held them when queried."""
    rows = {}
    for name in ("db_snapshot.json", "db_titles_20261114.json"):
        path = OUT / name
        if path.is_file():
            for key, v in json.loads(path.read_text())["sections"].items():
                rows[key.replace(":", "/")] = v
    return rows


def keys() -> list[str]:
    cited = re.findall(r'law\("([A-Z]{2}/[A-Za-z0-9\-]+/[0-9A-Za-z.\-]+)"', (ROOT / "mocks" / "tad-sim.js").read_text())
    anchors = [k for rid in roles.ROLES for jur in roles.ROLES[rid]["anchors"] for k in roles.anchors(rid, jur)]
    return list(dict.fromkeys(WANT + cited + anchors))


def path_of(key: str) -> pathlib.Path:
    return OUT / (key.replace("/", "_") + ".json")


def build() -> int:
    if not lawstore.available():
        sys.exit("the local store is missing; run python3 tools/ingest_lawwiki_local.py first")
    db, wrote, missing = db_rows(), 0, []
    for key in keys():
        if path_of(key).exists():
            continue
        rec = lawstore.get(key)
        if rec is None:
            missing.append(key)
            continue
        row = {k: rec[k] for k in ("jurisdiction", "code", "code_name", "section", "citation", "title", "status", "text", "history",
                                   "source_url", "lawwiki_url", "source_version", "content_hash")}
        row["title"] = (db.get(key) or {}).get("title") or rec["title"]
        row["fetched_at"], row["fetched_from"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "local LawWiki snapshot"
        with open(path_of(key), "x", encoding="utf-8") as fh:  # never overwrite an existing fixture
            json.dump(row, fh, indent=1, ensure_ascii=False)
        wrote += 1
    index = []
    for f in sorted(OUT.glob("*_*.json")):
        if f.name.startswith("db_"):
            continue
        d = json.loads(f.read_text())
        index.append({"file": f.name, "citation": d["citation"], "title": d["title"], "chars": len(d["text"] or ""), "lawwiki_url": d["lawwiki_url"]})
    (OUT / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False))
    print(f"{wrote} fixtures written, {len(index)} in index.json" + (f", not in the store: {', '.join(missing)}" if missing else ""))
    return 1 if missing else 0


def verify() -> int:
    db, bad, seen = db_rows(), [], 0
    for key, want in sorted(db.items()):
        if not path_of(key).is_file():
            bad.append(f"MISSING  {key}")
            continue
        seen += 1
        text = json.loads(path_of(key).read_text())["text"] or ""
        if hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest() != want["md5"] or len(text) != want["chars"]:
            bad.append(f"DIFFERS  {key}")
    print("\n".join(bad[:20]))
    print(f"{seen - sum(b.startswith('DIFFERS') for b in bad)} of {len(db)} fixtures hold the text of the database row (MD5 and length)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(verify() if "--verify" in sys.argv else build())
