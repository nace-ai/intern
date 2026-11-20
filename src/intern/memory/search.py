from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, Field

from intern.memory.drive import git
from intern.memory.format import LEAD_ID, RELATIONS_ID, FormatError, is_page_file, parse_page, strip_source

# Under .git/, so the index is never part of the drive's tree and a steward's reset leaves it alone.
SEARCH_DB = "nace-search.sqlite"
#: Bumped when what a passage holds changes: an older table is rebuilt.
SCHEMA = 1
SESSIONS_FOLDER = "sessions/"
#: Characters of a transcript one passage holds, so BM25's length normalisation weighs a chunk, not a whole session.
CHUNK_CHARS = 2_000
PAGE_LINES = 4
SESSION_LINES = 8
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
# Memory is full of paths, hosts, ids and flags (`api.prod-2`, `ORD-1234`): such a token is also matched whole.
_TOKEN = re.compile(r"[\w.@/-]{2,}")
_PART = re.compile(r"[^\W_]+")
_FILLER_TEXT = (
    "the and for with that this from into over what when where which your you are was were has have had not all any "
    "can will how did does who why"
)
_FILLER = frozenset(_FILLER_TEXT.split())
_TABLES = (
    "CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, blob TEXT);"
    "CREATE TABLE IF NOT EXISTS passages(id INTEGER PRIMARY KEY, path TEXT, blob TEXT, key TEXT, title TEXT, "
    "text TEXT);"
    "CREATE INDEX IF NOT EXISTS passages_path ON passages(path);"
    "CREATE VIRTUAL TABLE IF NOT EXISTS passage_fts USING fts5(title, body, content='', contentless_delete=1, "
    "tokenize='porter unicode61 remove_diacritics 2');"
)
# A title weighs twice its text; only passages of the session's commit count, in or out of one folder.
_QUERY = (
    "SELECT p.path, p.key, p.title, p.text FROM passage_fts JOIN passages p ON p.id = passage_fts.rowid "
    "WHERE passage_fts MATCH ? AND (p.path || ' ' || p.blob) IN (SELECT value FROM json_each(?)) "
    "AND substr(p.path, 1, length(?)) = ? AND (? = '' OR substr(p.path, 1, length(?)) != ?) "
    "ORDER BY bm25(passage_fts, 2.0, 1.0) LIMIT ?"
)
# A session ranks by its best chunk. FTS5 refuses bm25() inside an aggregate, and SQLite would flatten a plain
# subquery into one, so the scores are materialized first.
_SESSIONS = (
    "WITH scored AS MATERIALIZED (SELECT p.path, p.title, bm25(passage_fts, 2.0, 1.0) AS score "
    "FROM passage_fts JOIN passages p ON p.id = passage_fts.rowid "
    "WHERE passage_fts MATCH ? AND (p.path || ' ' || p.blob) IN (SELECT value FROM json_each(?)) "
    "AND substr(p.path, 1, length(?)) = ?) "
    "SELECT path, title, min(score) AS best FROM scored GROUP BY path ORDER BY best LIMIT ?"
)


class PageHit(BaseModel):
    page: str
    block: str
    title: str
    lines: list[str]


class SessionHit(BaseModel):
    path: str
    head: str
    lines: list[str]
    more: int


class Found(BaseModel):
    pages: list[PageHit] = Field(default_factory=list)
    sessions: list[SessionHit] = Field(default_factory=list)


def _split(text: str) -> str:
    return _CAMEL.sub(" ", text)


def _quote(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _tokens(query: str) -> list[str]:
    return [t for t in (t.strip(".-/@") for t in _TOKEN.findall(query)) if len(t) >= 2]


def _parts(token: str) -> list[str]:
    return [p.lower() for p in _PART.findall(_split(token))]


def match(query: str) -> str:
    terms: list[str] = []
    for token in _tokens(query):
        parts = _parts(token)
        if len(parts) > 1:
            terms.append(" ".join(parts))
        terms += [p for p in parts if len(p) >= 2 and p not in _FILLER]
    return " OR ".join(_quote(t) for t in dict.fromkeys(terms))


def _needles(query: str) -> list[str]:
    found: list[str] = []
    for token in _tokens(query):
        parts = _parts(token)
        found += [token.lower()] if len(parts) > 1 else []
        found += [p for p in parts if len(p) >= 2 and p not in _FILLER]
    return list(dict.fromkeys(found))


def _best(lines: Sequence[str], needles: list[str]) -> list[str]:
    scored = [(sum(n in line.lower() for n in needles), i) for i, line in enumerate(lines) if line.strip()]
    return [lines[i] for score, i in sorted(scored, key=lambda s: -s[0]) if score > 0]


def _indexed(name: str) -> bool:
    return is_page_file(name) or (name.startswith(SESSIONS_FOLDER) and name.endswith(".txt"))


def _tree(root: Path, commit: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for entry in git(root, "ls-tree", "-r", "-z", commit).decode(errors="replace").split("\0"):
        meta, _, name = entry.partition("\t")
        fields = meta.split()
        if len(fields) == 3 and fields[1] == "blob" and _indexed(name):
            found[name] = fields[2]
    return found


def _blobs(root: Path, blobs: list[str]) -> dict[str, str]:
    unique = list(dict.fromkeys(blobs))
    if not unique:
        return {}
    out = git(root, "cat-file", "--batch", stdin="".join(f"{b}\n" for b in unique).encode())
    texts: dict[str, str] = {}
    pos = 0
    for blob in unique:
        header_end = out.index(b"\n", pos)
        size = int(out[pos:header_end].split()[2])
        texts[blob] = out[header_end + 1 : header_end + 1 + size].decode(errors="replace")
        pos = header_end + 1 + size + 1
    return texts


def _passages(name: str, text: str) -> list[tuple[str, str, str]]:
    # (key, title, text) for each block of a page, or each chunk of a transcript.
    if name.startswith(SESSIONS_FOLDER):
        head, _, body = text.partition("\n\n")
        chunks: list[tuple[str, str, str]] = []
        lines: list[str] = []
        start, size = 1, 0
        for number, line in enumerate(body.splitlines(), 1):
            if lines and size + len(line) > CHUNK_CHARS:
                chunks.append((f"l{start}", head, "\n".join(lines)))
                lines, start, size = [], number, 0
            lines.append(line)
            size += len(line) + 1
        chunks.append((f"l{start}", head, "\n".join(lines)))
        return chunks
    path = name.removesuffix(".md")
    try:
        page = parse_page(path, text)
    except FormatError:
        return []
    found = [(LEAD_ID, page.title, "\n".join(strip_source(line) for line in page.lead))]
    found += [
        (b.id, f"{page.title}, {b.heading}", "\n".join(strip_source(line) for line in b.lines))
        for b in page.blocks
        if b.id != RELATIONS_ID
    ]
    return found


def _open(path: Path | None) -> sqlite3.Connection:
    con = sqlite3.connect(path or ":memory:", timeout=120, check_same_thread=False, isolation_level=None)
    # What memory forgets must not stay readable in the file's free pages.
    con.execute("PRAGMA secure_delete = ON")
    if con.execute("PRAGMA user_version").fetchone()[0] != SCHEMA:
        con.executescript(
            "DROP TABLE IF EXISTS passage_fts; DROP TABLE IF EXISTS passages; DROP TABLE IF EXISTS files;"
        )
    con.executescript(_TABLES + f"PRAGMA user_version = {SCHEMA};")
    return con


def refresh(root: Path, commit: str) -> None:
    # After a merge: what the commit removed or rewrote leaves an index that exists, without waiting for a search.
    if (root / ".git" / SEARCH_DB).is_file():
        MemoryIndex(root, commit).refresh()


class MemoryIndex:
    def __init__(self, root: Path, commit: str) -> None:
        self.root, self.commit = root, commit
        self._tree: dict[str, str] | None = None
        self._con: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def search(self, query: str, folder: str | None, most: int) -> Found:
        expression = match(query)
        if not expression:
            return Found()
        needles = _needles(query)
        with self._lock:
            con = self._connect()
            tree = self._update(con)
            live = json.dumps(sorted(f"{p} {b}" for p, b in tree.items()))
            found = Found()
            if folder != SESSIONS_FOLDER:
                inside = folder or ""
                args = (expression, live, inside, inside, SESSIONS_FOLDER, SESSIONS_FOLDER, SESSIONS_FOLDER, most)
                for path, key, title, text in con.execute(_QUERY, args).fetchall():
                    lines = text.splitlines()
                    shown = _best(lines, needles)[:PAGE_LINES] or [line for line in lines if line.strip()][:2]
                    found.pages.append(PageHit(page=path.removesuffix(".md"), block=key, title=title, lines=shown))
            if folder in (None, SESSIONS_FOLDER):
                args = (expression, live, SESSIONS_FOLDER, SESSIONS_FOLDER, most)
                for path, head, _ in con.execute(_SESSIONS, args).fetchall():
                    chunks = con.execute("SELECT text FROM passages WHERE path = ? ORDER BY id", (path,)).fetchall()
                    hits = _best("\n".join(t for (t,) in chunks).splitlines(), needles)
                    shown = hits[:SESSION_LINES]
                    found.sessions.append(SessionHit(path=path, head=head, lines=shown, more=len(hits) - len(shown)))
            return found

    def refresh(self) -> None:
        with self._lock:
            self._update(self._connect())

    def _connect(self) -> sqlite3.Connection:
        if self._con is None:
            try:
                self._con = _open(self.root / ".git" / SEARCH_DB)
            except sqlite3.Error:
                # A drive this user cannot write still gets searched, from a table built for this session alone.
                self._con = _open(None)
        return self._con

    def _update(self, con: sqlite3.Connection) -> dict[str, str]:
        if self._tree is None:
            self._tree = _tree(self.root, self.commit)
        want = self._tree
        if dict(con.execute("SELECT path, blob FROM files").fetchall()) == want:
            return want
        con.execute("BEGIN IMMEDIATE")
        try:
            known = dict(con.execute("SELECT path, blob FROM files").fetchall())
            gone = [p for p, blob in known.items() if want.get(p) != blob]
            new = [p for p, blob in want.items() if known.get(p) != blob]
            texts = _blobs(self.root, [want[p] for p in new])
            for p in gone:
                con.execute("DELETE FROM passage_fts WHERE rowid IN (SELECT id FROM passages WHERE path = ?)", (p,))
                con.execute("DELETE FROM passages WHERE path = ?", (p,))
                con.execute("DELETE FROM files WHERE path = ?", (p,))
            for p in new:
                for key, title, text in _passages(p, texts[want[p]]):
                    cur = con.execute(
                        "INSERT INTO passages(path, blob, key, title, text) VALUES (?, ?, ?, ?, ?)",
                        (p, want[p], key, title, text),
                    )
                    con.execute(
                        "INSERT INTO passage_fts(rowid, title, body) VALUES (?, ?, ?)",
                        (cur.lastrowid, _split(f"{p} {title}"), _split(text)),
                    )
                con.execute("INSERT INTO files(path, blob) VALUES (?, ?)", (p, want[p]))
            if gone:
                # Merges the FTS5 segments, so a deleted passage's terms leave the index too.
                con.execute("INSERT INTO passage_fts(passage_fts) VALUES('optimize')")
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        return want
