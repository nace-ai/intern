"""Read LawWiki's statutes from the local store that tools/ingest_lawwiki_local.py builds.

The store is one SQLite file (data/lawwiki/lawwiki.sqlite, git-ignored) holding every active section of
the five LawWiki jurisdictions with a full-text index. Reading it needs no network and no key, so the
public site's rate limit never applies. When the file is absent every function returns nothing and the
engine falls back to the small fixtures in data/statutes.

A code scope is a list of specs: "LAB" is a whole code, "FS:83" is the sections of the Florida
Statutes whose number starts with "83." (Florida publishes one code, split by chapter).
"""
from __future__ import annotations

import json
import re
import sqlite3
import zlib

from . import config

WORD = re.compile(r"[A-Za-z][A-Za-z\-']{3,}")
STOP = set("""about after against also asks been before being between both case claim could does down each from have here
into just like more most must only other over said same shall should such than that their them then there these they this
those under until upon very were what when where which while will with within without would your days paid says attaching
client asking question whether""".split())


def available() -> bool:
    return config.LAW_STORE.is_file()


def _open() -> sqlite3.Connection | None:
    if not available():
        return None
    db = sqlite3.connect(f"file:{config.LAW_STORE}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def _record(row: sqlite3.Row, text: bool = True) -> dict:
    jur, code, sec = row["jur"], row["code"], row["section"]
    out = {"key": row["ref"], "jurisdiction": jur, "code": code, "code_name": row["code_name"], "section": sec,
           "citation": row["citation"], "title": row["heading"] or row["hierarchy"].split(" > ")[-1], "hierarchy": row["hierarchy"],
           "status": "active", "history": row["history"], "source_url": row["source_url"],
           "lawwiki_url": f"https://www.lawwiki.ai/{jur.lower()}/{code.lower()}/{sec}", "source_version": row["source_version"],
           "content_hash": row["content_hash"], "chars": row["chars"], "cross_references": json.loads(row["xrefs"] or "[]")}
    if text:
        out["text"] = zlib.decompress(row["body"]).decode("utf-8")
    return out


SELECT = ("select s.*, c.name as code_name from statutes s left join codes c on c.jur = s.jur and c.code = s.code ")


def get(key: str) -> dict | None:
    """The section `JUR/CODE/SECTION` with its text, or None."""
    db = _open()
    if db is None:
        return None
    try:
        row = db.execute(SELECT + "where s.ref = ?", (key,)).fetchone()
        return _record(row) if row else None
    finally:
        db.close()


def terms(text: str, limit: int = 14) -> list[str]:
    """The distinct content words of `text`, longest first: what the full-text query is built from."""
    seen: dict[str, None] = {}
    for w in WORD.findall(text.lower()):
        w = w.strip("-'")
        if len(w) > 3 and w not in STOP:
            seen.setdefault(w)
    return sorted(seen, key=lambda w: -len(w))[:limit]


def _scope_sql(scope: list[str] | None) -> tuple[str, list[str]]:
    if not scope:
        return "", []
    parts, args = [], []
    for spec in scope:
        code, _, chapter = spec.partition(":")
        if chapter:
            parts.append("(s.code = ? and s.section like ?)")
            args += [code, chapter + ".%"]
        else:
            parts.append("s.code = ?")
            args.append(code)
    return " and (" + " or ".join(parts) + ")", args


def search(query: str, jur: str | None = None, scope: list[str] | None = None, limit: int = 8, text: bool = False) -> list[dict]:
    """Sections ranked by BM25 for the content words of `query`, inside one jurisdiction and code scope.
    Headings and citations weigh more than body text. [] when the store is absent or nothing matches."""
    words = terms(query)
    db = _open()
    if db is None or not words:
        return []
    scope_sql, args = _scope_sql(scope)
    sql = ("select s.*, c.name as code_name, bm25(statutes_fts, 2.0, 8.0, 3.0, 1.0) as rank from statutes_fts "
           "join statutes s on s.id = statutes_fts.rowid left join codes c on c.jur = s.jur and c.code = s.code "
           "where statutes_fts match ?" + (" and s.jur = ?" if jur else "") + scope_sql + " order by rank limit ?")
    try:
        rows = db.execute(sql, [" OR ".join(f'"{w}"' for w in words)] + ([jur] if jur else []) + args + [limit]).fetchall()
        return [{**_record(r, text), "rank": round(-r["rank"], 2)} for r in rows]
    except sqlite3.Error:
        return []
    finally:
        db.close()


def count(jur: str, scope: list[str] | None = None) -> int:
    """How many sections a jurisdiction and code scope cover (0 when the store is absent)."""
    db = _open()
    if db is None:
        return 0
    scope_sql, args = _scope_sql(scope)
    try:
        return db.execute("select count(*) from statutes s where s.jur = ?" + scope_sql, [jur] + args).fetchone()[0]
    finally:
        db.close()


def snapshots() -> dict:
    """Which LawWiki snapshot each jurisdiction was read from: {jur: {folder, source_version, records_sha256, sections}}."""
    db = _open()
    if db is None:
        return {}
    try:
        return {k.split(".", 1)[1]: json.loads(v) for k, v in db.execute("select key, value from meta where key like 'snapshot.%'")}
    finally:
        db.close()
