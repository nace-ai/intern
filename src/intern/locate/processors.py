"""Processors turn a file into a Derivative: its text in units (lines or pages), its structure, its tables.

Cards and the cascade see the Derivative and nothing else, so a processor can be swapped (another PDF parser, an OCR,
a perception service) without touching them. Code and text processors live here and need no optional dependency;
`documents.py` has the PDF, spreadsheet, table and image ones.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import ClassVar, Literal, Protocol

from pydantic import BaseModel

Unit = Literal["line", "page"]
ChunkKind = Literal["class", "function", "method", "section", "page", "pages", "key", "window", "table"]

#: Lines a window of plain code or text spans when a file has no structure to chunk by.
WINDOW_LINES = 120
#: A piece of a document's text holds no more than System 1 is shown of a place when it judges it
#: (`search.LEAF_LINES` lines, `search.LEAF_CHARS` characters): what a longer chunk holds past that is never read, so
#: a passage there is ranked by the chunk's first lines. In contracts as plain text, whose lines are paragraphs, a
#: window of WINDOW_LINES is about 13,000 characters: System 1 was shown a quarter of the text, and a search put
#: the quoted clause in its first 20,000 characters for 0.62 of the questions whose clause was past the part shown
#: and 0.92 of the others (CUAD, 60 questions about contracts of more than one window).
PIECE_LINES = 80
PIECE_CHARS = 4000
#: A Python file is chunked by symbols; a class with no methods is one chunk.
_PY_SUFFIXES = frozenset({".py", ".pyi"})
#: Characters of Python source parsed into a syntax tree at most. A longer file is data or generated code, and its
#: tree is many times its text: a 30 MB module of literals took 4.7 GB to parse and a 2 MB one 460 MB, in the agent's
#: own process before its first turn. Past this a file is indexed by windows of lines and its imports are not read.
MAX_AST_CHARS = 1_000_000
_MD_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt"})
_KEYED_SUFFIXES = frozenset({".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"})
_TEXT_SUFFIXES = frozenset(
    [
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        ".vue",
        ".svelte",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".scala",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".cs",
        ".rb",
        ".php",
        ".swift",
        ".m",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".bat",
        ".sql",
        ".r",
        ".jl",
        ".lua",
        ".pl",
        ".ex",
        ".exs",
        ".erl",
        ".hs",
        ".ml",
        ".clj",
        ".dart",
        ".proto",
        ".graphql",
        ".html",
        ".htm",
        ".css",
        ".scss",
        ".less",
        ".xml",
        ".svg",
        ".lock",
        ".mk",
        ".cmake",
        ".dockerfile",
        ".gradle",
        ".tf",
        ".hcl",
    ]
)


class Chunk(BaseModel):
    """One addressable piece of a file: a symbol, a section, a page, a table. ``start``/``end`` are 1-based units
    (lines or pages), inclusive; ``parent`` is the enclosing chunk's key."""

    key: str
    kind: ChunkKind
    title: str
    summary: str = ""
    start: int
    end: int
    parent: str | None = None
    card: str = ""


class TableInfo(BaseModel):
    """A table the `sql` tool can query: its DuckDB name, where it came from, and its profile."""

    name: str
    source: str
    rows: int
    columns: list[str]
    profile: str


class Derivative(BaseModel):
    unit: Unit
    units: list[str]
    chunks: list[Chunk] = []
    tables: list[TableInfo] = []
    #: One line about the file: its docstring, first paragraph, shape ("3 rows × 3 columns: name, age").
    summary: str = ""
    #: What a document or a package says it is, when it says: front matter's `summary`, a README's first paragraph,
    #: a package's `description`. Only the root's card reads it (a folder's purpose, for the agent's map); the walk's
    #: cards and vectors use ``summary``, so a description never changes what System 1 is shown.
    description: str = ""
    #: How the text was obtained when that matters to a reader ("text layer; 3 of 12 pages OCR'd").
    note: str = ""
    #: The text the file's vector and keywords are built from, when the units are not it (a table's rendering).
    head: str = ""
    #: Whether the units must be written to `<home>/derived` because the source is not readable text.
    derived: bool = False


class Processor(Protocol):
    """Turns one file into its derivative. A processor that sets ``parallel = True`` (slow, and a pure function of
    the file: no shared connection) has its files processed in a build's worker processes."""

    name: ClassVar[str]
    suffixes: ClassVar[frozenset[str]]

    def process(self, path: Path, workspace: Path) -> Derivative: ...


def _first_line(text: str | None) -> str:
    return (text or "").strip().split("\n")[0].strip()


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    try:
        if isinstance(node, ast.ClassDef):
            return f"({', '.join(ast.unparse(b) for b in node.bases)})" if node.bases else ""
        return f"({ast.unparse(node.args)})" + (f" -> {ast.unparse(node.returns)}" if node.returns else "")
    except Exception:
        return "(...)"


class PythonProcessor:
    """Top-level classes and functions, methods under their class; the module docstring is the file summary."""

    name: ClassVar[str] = "python"
    suffixes: ClassVar[frozenset[str]] = _PY_SUFFIXES

    def process(self, path: Path, workspace: Path) -> Derivative:
        source = path.read_text(errors="replace")
        lines = source.splitlines()
        if len(source) > MAX_AST_CHARS:
            return windows(lines)
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            return windows(lines)
        chunks: list[Chunk] = []

        def add(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, key: str, kind: ChunkKind, parent=None):
            start = min([d.lineno for d in node.decorator_list] + [node.lineno])
            word = "class" if kind == "class" else ("async def" if isinstance(node, ast.AsyncFunctionDef) else "def")
            chunks.append(
                Chunk(
                    key=key,
                    kind=kind,
                    title=f"{word} {key}{_signature(node)}",
                    summary=_first_line(ast.get_docstring(node)),
                    start=start,
                    end=node.end_lineno or start,
                    parent=parent,
                )
            )

        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                add(node, node.name, "function")
            elif isinstance(node, ast.ClassDef):
                add(node, node.name, "class")
                for sub in node.body:
                    if isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef):
                        add(sub, f"{node.name}.{sub.name}", "method", node.name)
        return Derivative(unit="line", units=lines, chunks=chunks, summary=_first_line(ast.get_docstring(tree)))


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*#*$")
_RST_UNDERLINE = re.compile(r"^([=\-~^\"'`*+#])\1{2,}\s*$")


#: A top-level scalar key of YAML front matter, how far down its closing fence may be, and a description's length.
_FRONT_KEY = re.compile(r"^([A-Za-z_][\w-]*):\s*(\S.*?)\s*$")
FRONT_MATTER_LINES = 60
DESCRIPTION_CHARS = 160


class MarkdownProcessor:
    """Sections by heading (Markdown ``#`` levels, reStructuredText underlines), nested by level."""

    name: ClassVar[str] = "markdown"
    suffixes: ClassVar[frozenset[str]] = _MD_SUFFIXES

    def process(self, path: Path, workspace: Path) -> Derivative:
        lines = path.read_text(errors="replace").splitlines()
        derivative = sections(lines) or windows(lines)
        return derivative.model_copy(update={"description": describe(lines)})


def describe(lines: list[str]) -> str:
    """What a document says it is: its front matter's ``summary`` (or ``description``), else its first paragraph
    after the front matter, after its ``title`` when it has one."""
    meta, skip = front_matter(lines)
    said = meta.get("summary") or meta.get("description")
    if not said:
        body = [""] * skip + lines[skip:]  # the same line numbers, the front matter out of the first paragraph
        first = (sections(body) or windows(body)).summary
        said = f"{meta['title']}: {first}" if "title" in meta and first else meta.get("title") or first
    return re.sub(r"\*\*|`", "", said).strip()[:DESCRIPTION_CHARS]


def front_matter(lines: list[str]) -> tuple[dict[str, str], int]:
    """YAML front matter's top-level scalar keys and the lines it takes; ({}, 0) when the text has none."""
    if not lines or lines[0].strip() != "---":
        return {}, 0
    for end in range(1, min(len(lines), FRONT_MATTER_LINES)):
        if lines[end].strip() in ("---", "..."):
            keys = {m[1]: m[2].strip("\"'") for line in lines[1:end] if (m := _FRONT_KEY.match(line))}
            return keys, end + 1
    return {}, 0


def sections(lines: list[str]) -> Derivative | None:
    """The lines as a Derivative of nested sections, or None when they have no heading."""
    heads: list[tuple[int, int, str]] = []  # (line, level, title)
    rst_levels: dict[str, int] = {}
    fenced = False
    code: set[int] = set()
    for i, line in enumerate(lines, 1):
        if line.startswith(("```", "~~~")):
            fenced = not fenced
        if fenced or line.startswith(("```", "~~~")):
            code.add(i)
            continue
        if m := _MD_HEADING.match(line):
            heads.append((i, len(m[1]), m[2]))
        elif (
            i > 1
            and _RST_UNDERLINE.match(line)
            and (prev := lines[i - 2].strip())
            and not _RST_UNDERLINE.match(prev)
            and len(line.rstrip()) >= len(prev)
        ):
            level = rst_levels.setdefault(line[0], len(rst_levels) + 1)
            heads.append((i - 1, level, prev))
    if not heads:
        return None
    chunks: list[Chunk] = []
    stack: list[tuple[int, str]] = []  # (level, key) of open sections
    seen: dict[str, int] = {}
    for n, (line, level, title) in enumerate(heads):
        end = heads[n + 1][0] - 1 if n + 1 < len(heads) else len(lines)
        while stack and stack[-1][0] >= level:
            stack.pop()
        key = re.sub(r"\s+", " ", title)[:80]
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key = f"{key} ({seen[key]})"
        chunks.append(
            Chunk(key=key, kind="section", title=title, start=line, end=end, parent=stack[-1][1] if stack else None)
        )
        stack.append((level, key))
    # A section's end is its last line including its subsections; the preamble before the first heading is none.
    for chunk in chunks:
        children = [c for c in chunks if c.parent == chunk.key]
        if children:
            chunk.end = max(chunk.end, *(c.end for c in children))
    skip = code | {line for line, _, _ in heads}
    first_text = next(
        (
            line.strip()
            for i, line in enumerate(lines, 1)
            if line.strip() and i not in skip and not _RST_UNDERLINE.match(line)
        ),
        "",
    )
    return Derivative(unit="line", units=lines, chunks=chunks, summary=first_text[:160])


#: A ``description`` with a quoted string value, as pyproject.toml and package.json give one, in the first lines.
_DESCRIPTION = re.compile(r'^\s*"?description"?\s*[:=]\s*"([^"]+)"')
DESCRIPTION_LINES = 60
_TOP_KEY = re.compile(r'^(?:\[([^\]]+)\]|([A-Za-z_][\w.-]*)\s*:|\s{0,4}"([^"]+)"\s*:)')


class KeyedProcessor:
    """Top-level keys of JSON, YAML, TOML and INI files as chunks, from the lines rather than a parser."""

    # shortcut: line-based key detection misses keys in one-line JSON and nested TOML arrays; parse when it matters.
    name: ClassVar[str] = "keyed"
    suffixes: ClassVar[frozenset[str]] = _KEYED_SUFFIXES

    def process(self, path: Path, workspace: Path) -> Derivative:
        lines = path.read_text(errors="replace").splitlines()
        # A package's own description (pyproject.toml, package.json) says what its folder is for.
        description = next((m[1] for line in lines[:DESCRIPTION_LINES] if (m := _DESCRIPTION.match(line))), "")
        keys = [(i, m[1] or m[2] or m[3]) for i, line in enumerate(lines, 1) if (m := _TOP_KEY.match(line))]
        if not keys or len(keys) > len(lines) / 2:
            return windows(lines).model_copy(update={"description": description[:DESCRIPTION_CHARS]})
        chunks = [
            Chunk(
                key=key, kind="key", title=key, start=line, end=keys[n + 1][0] - 1 if n + 1 < len(keys) else len(lines)
            )
            for n, (line, key) in enumerate(keys)
        ]
        return Derivative(unit="line", units=lines, chunks=_unique(chunks), description=description[:DESCRIPTION_CHARS])


def _unique(chunks: list[Chunk]) -> list[Chunk]:
    seen: dict[str, int] = {}
    for chunk in chunks:
        seen[chunk.key] = seen.get(chunk.key, 0) + 1
        if seen[chunk.key] > 1:
            chunk.key = f"{chunk.key} ({seen[chunk.key]})"
    return chunks


class WindowProcessor:
    """Fixed windows of lines, for code the harness has no parser for and text with no headings."""

    name: ClassVar[str] = "text"
    suffixes: ClassVar[frozenset[str]] = _TEXT_SUFFIXES

    def process(self, path: Path, workspace: Path) -> Derivative:
        return windows(path.read_text(errors="replace").splitlines())


def windows(lines: list[str]) -> Derivative:
    chunks = window_chunks(lines, 1, len(lines))
    summary = next((line.strip() for line in lines if line.strip()), "")[:160]
    return Derivative(unit="line", units=lines, chunks=chunks if len(chunks) > 1 else [], summary=summary)


def window_chunks(lines: list[str], first: int, last: int) -> list[Chunk]:
    """Lines ``first`` to ``last`` (1-based, inclusive) as windows of WINDOW_LINES."""
    starts = range(first, max(last, first) + 1, WINDOW_LINES)
    return [_window(lines, start, min(start + WINDOW_LINES - 1, last)) for start in starts]


def _window(lines: list[str], start: int, end: int, parent: Chunk | None = None) -> Chunk:
    head = next((line.strip() for line in lines[start - 1 : end] if line.strip()), "")
    where = f"lines {start}-{end}"
    return Chunk(
        key=f"L{start}-{end}",
        kind="window",
        title=f"{parent.title}, {where}" if parent else where,
        summary=head[:120],
        start=start,
        end=end,
        parent=parent.key if parent else None,
    )


def fits(lines: list[str], first: int, last: int) -> bool:
    """Whether lines ``first`` to ``last`` are one piece: text System 1 is shown whole."""
    return last - first < PIECE_LINES and sum(len(line) + 1 for line in lines[first - 1 : last]) <= PIECE_CHARS


def piece_chunks(lines: list[str], first: int, last: int, parent: Chunk | None = None) -> list[Chunk]:
    """Lines ``first`` to ``last`` as pieces, cut between lines: each as many lines as fit (a line longer than a
    piece is one by itself, and the only text of a piece System 1 is not shown whole)."""
    chunks, start = [], first
    for n in range(first + 1, last + 1):
        if not fits(lines, start, n):
            chunks.append(_window(lines, start, n - 1, parent))
            start = n
    return [*chunks, _window(lines, start, last, parent)]


def in_pieces(derivative: Derivative) -> Derivative:
    """A text's Derivative with nothing in it longer than a piece: a text with no heading is cut into pieces from
    its first line (none when it is one piece), and a section with no subsection that is longer than a piece gets
    its pieces as its parts."""
    lines, chunks = derivative.units, derivative.chunks
    if all(c.kind == "window" for c in chunks):
        pieces = piece_chunks(lines, 1, len(lines)) if lines else []
        return derivative.model_copy(update={"chunks": pieces if len(pieces) > 1 else []})
    parents = {c.parent for c in chunks}
    long = [c for c in chunks if c.key not in parents and not fits(lines, c.start, c.end)]
    pieces = [piece for c in long for piece in piece_chunks(lines, c.start, c.end, c)]
    return derivative.model_copy(update={"chunks": [*chunks, *pieces]})
