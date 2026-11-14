#!/usr/bin/env python3
"""Ingest LawWiki's own statute snapshots into a local, searchable store.

    python3 tools/ingest_lawwiki_local.py              # all five jurisdictions (about 410,000 sections)
    python3 tools/ingest_lawwiki_local.py --jur CA,FL  # only some
    python3 tools/ingest_lawwiki_local.py --stats      # what the store holds

Source: the immutable snapshots the LawWiki sync pipeline writes (one records.jsonl.gz per jurisdiction),
by default in ~/law-wiki/ingest/snapshots; set LAWWIKI_SNAPSHOTS to read them from somewhere else. The
newest complete snapshot of each jurisdiction is used. Nothing is fetched and no key is needed, so the
public site's rate limit (20 requests a minute) never applies.

Output: data/lawwiki/lawwiki.sqlite (git-ignored). It holds every section's citation, headings and
content hash, its text (zlib-compressed) and a full-text index (SQLite FTS5). engine/lawstore.py reads it.
The file is built under a temporary name and renamed when complete, so a half-built store is never used.

Standard library only.
"""
import argparse
import glob
import gzip
import json
import os
import pathlib
import sqlite3
import sys
import time
import zlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "lawwiki" / "lawwiki.sqlite"
SNAPSHOTS = pathlib.Path(os.environ.get("LAWWIKI_SNAPSHOTS", pathlib.Path.home() / "law-wiki" / "ingest" / "snapshots"))
JURS = ("CA", "FL", "NY", "TX", "US")
SCHEMA = """
create table codes(jur text, code text, name text, citation_prefix text, sections integer default 0, primary key (jur, code));
create table statutes(id integer primary key, ref text unique, jur text, code text, section text, citation text,
  heading text, hierarchy text, history text, source_url text, xrefs text, content_hash text, source_version text,
  chars integer, body blob);
create index statutes_code on statutes(jur, code);
create virtual table statutes_fts using fts5(citation, heading, hierarchy, text, content='', tokenize='porter unicode61');
create table meta(key text primary key, value text);
"""


def newest_snapshot(jur: str) -> tuple[pathlib.Path, dict] | None:
    """The newest snapshot folder of `jur` that covers the whole jurisdiction."""
    for folder in sorted(glob.glob(str(SNAPSHOTS / jur / "*")), reverse=True):
        manifest = pathlib.Path(folder) / "manifest.json"
        if manifest.is_file() and (pathlib.Path(folder) / "records.jsonl.gz").is_file():
            m = json.loads(manifest.read_text())
            if not m.get("units") and not (m.get("totals") or {}).get("errors"):
                return pathlib.Path(folder), m
    return None


def clean(value) -> str:
    return "" if value is None else str(value).strip()


def ingest(jurs: list[str]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    building = STORE.with_suffix(".sqlite.building")
    if building.exists():
        building.unlink()
    db = sqlite3.connect(building)
    db.executescript("pragma journal_mode=off; pragma synchronous=off;" + SCHEMA)
    total, t0 = 0, time.time()
    for jur in jurs:
        found = newest_snapshot(jur)
        if not found:
            print(f"{jur}: no complete snapshot under {SNAPSHOTS / jur}", file=sys.stderr)
            continue
        folder, manifest = found
        version, rows, fts, n = manifest.get("source_version", ""), [], [], 0
        with gzip.open(folder / "records.jsonl.gz", "rt", encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                if rec.get("type") == "code":
                    db.execute("insert or replace into codes(jur, code, name, citation_prefix) values (?,?,?,?)",
                               (jur, rec["abbreviation"], clean(rec.get("name")), clean(rec.get("citation_prefix"))))
                    continue
                if rec.get("type") != "section" or clean(rec.get("status")) not in ("", "active"):
                    continue
                total += 1
                n += 1
                text = clean(rec.get("text"))
                hierarchy = " > ".join(x for x in (clean(rec.get(k)) for k in ("division_name", "part_name", "chapter_name", "article_name")) if x)
                rows.append((total, f"{jur}/{rec['code']}/{rec['section']}", jur, rec["code"], clean(rec.get("section")), clean(rec.get("citation")),
                             clean(rec.get("heading")), hierarchy, clean(rec.get("history")), clean(rec.get("source_url")),
                             json.dumps(rec.get("xrefs") or []), clean(rec.get("content_hash")), version, len(text),
                             zlib.compress(text.encode("utf-8"), 6)))
                fts.append((total, clean(rec.get("citation")), clean(rec.get("heading")), hierarchy, text))
                if len(rows) >= 5000:
                    db.executemany("insert or ignore into statutes values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                    db.executemany("insert into statutes_fts(rowid, citation, heading, hierarchy, text) values (?,?,?,?,?)", fts)
                    rows, fts = [], []
        db.executemany("insert or ignore into statutes values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.executemany("insert into statutes_fts(rowid, citation, heading, hierarchy, text) values (?,?,?,?,?)", fts)
        db.execute("insert or replace into meta values (?,?)", (f"snapshot.{jur}", json.dumps(
            {"folder": folder.name, "source_version": version, "records_sha256": manifest.get("records_sha256"), "sections": n})))
        db.commit()
        print(f"{jur}: {n} sections from {folder.name} ({time.time() - t0:.0f}s)", flush=True)
    db.execute("update codes set sections = (select count(*) from statutes s where s.jur = codes.jur and s.code = codes.code)")
    db.execute("insert or replace into meta values ('built_at', ?)", (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),))
    db.execute("insert into statutes_fts(statutes_fts) values ('optimize')")
    db.commit()
    db.close()
    os.replace(building, STORE)
    print(f"done: {total} sections in {STORE.relative_to(ROOT)} ({STORE.stat().st_size / 1e6:.0f} MB, {time.time() - t0:.0f}s)")


def stats() -> None:
    if not STORE.is_file():
        sys.exit(f"{STORE.relative_to(ROOT)} does not exist; run this tool without --stats first")
    db = sqlite3.connect(f"file:{STORE}?mode=ro", uri=True)
    for jur, codes, sections in db.execute("select jur, count(*), sum(sections) from codes group by jur order by jur"):
        print(f"{jur}: {codes} codes, {sections} sections")
    for key, value in db.execute("select key, value from meta order by key"):
        print(key, value[:160])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jur", default=",".join(JURS), help="comma-separated jurisdictions, default all five")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    if args.stats:
        stats()
        return
    ingest([j.strip().upper() for j in args.jur.split(",") if j.strip()])


if __name__ == "__main__":
    main()
