"""Code in languages other than Python, chunked by what it defines: tree-sitter parses the file and one query a
language names its classes and functions, so a search lands on a function or a class, not on a window of lines.

The grammars are optional (the `code-search` extra). A language whose grammar is not installed keeps the windows
of `WindowProcessor`; so does a file that defines nothing, and one too long to be anything but generated.
"""

from __future__ import annotations

import importlib
import re
from functools import cache
from importlib.util import find_spec
from typing import TYPE_CHECKING, ClassVar, NamedTuple

from intern.locate.processors import Chunk, ChunkKind, Derivative, window_chunks, windows

if TYPE_CHECKING:
    from pathlib import Path

    from tree_sitter import Language, Node, Query

#: Chunks and characters past which a file keeps its windows: generated and minified code, a dump of rows. Each chunk
#: is a card in the index, and of a long file a search judges only the 160 nearest the question's words.
MAX_CHUNKS = 1000
MAX_CHARS = 2_000_000
#: Lines of code outside every definition (a script's body, a block of constants) that make a chunk of their own.
GAP_LINES = 8
TITLE_CHARS = 200
SUMMARY_CHARS = 160
NAME_CHARS = 80


class _Grammar(NamedTuple):
    """One language: the package of its tree-sitter grammar and the query that finds its definitions. The query
    captures a definition as ``@class`` or ``@function``, its name as ``@name`` where the node has no ``name``
    field, the type a method is written outside of as ``@scope`` (Go's receiver, Rust's ``impl``), and comments
    as ``@comment``."""

    module: str
    suffixes: tuple[str, ...]
    query: str
    entry: str = "language"


_JS = """
[(function_declaration) (generator_function_declaration) (method_definition)] @function
(class_declaration) @class
(variable_declarator name: (identifier) value: [(arrow_function) (function_expression) (generator_function)]) @function
(variable_declarator name: (identifier) value: (class)) @class
(assignment_expression left: (member_expression) @name right: [(arrow_function) (function_expression)]) @function
(pair key: (property_identifier) @name value: [(arrow_function) (function_expression)]) @function
(comment) @comment
"""
_TS = (
    _JS
    + """
[(abstract_class_declaration) (interface_declaration) (enum_declaration) (type_alias_declaration)] @class
(public_field_definition name: (_) value: [(arrow_function) (function_expression)]) @function
"""
)
_C = """
(function_definition) @function
(preproc_function_def) @function
[(struct_specifier name: (_) body: (_)) (union_specifier name: (_) body: (_)) (enum_specifier name: (_) body: (_))]
  @class
(type_definition type: [(struct_specifier !name body: (_)) (union_specifier !name body: (_))
  (enum_specifier !name body: (_))] declarator: (type_identifier) @name) @class
(comment) @comment
"""
_GO_RECEIVER = (
    "[(type_identifier) @scope (generic_type type: (type_identifier) @scope) "
    "(pointer_type [(type_identifier) @scope (generic_type type: (type_identifier) @scope)])]"
)
_SQL_CLASSES = ("create_table", "create_view", "create_materialized_view", "create_type")
_SQL_FUNCTIONS = ("create_function", "create_procedure", "create_trigger")

_GRAMMARS = (
    _Grammar("tree_sitter_javascript", (".js", ".jsx", ".mjs", ".cjs"), _JS),
    _Grammar("tree_sitter_typescript", (".ts", ".mts", ".cts"), _TS, "language_typescript"),
    _Grammar("tree_sitter_typescript", (".tsx",), _TS, "language_tsx"),
    _Grammar(
        "tree_sitter_go",
        (".go",),
        f"""
        (function_declaration) @function
        (method_declaration receiver: (parameter_list (parameter_declaration type: {_GO_RECEIVER}))) @function
        [(type_spec) (type_alias)] @class
        (comment) @comment
        """,
    ),
    _Grammar(
        "tree_sitter_rust",
        (".rs",),
        """
        [(function_item) (macro_definition)] @function
        [(struct_item) (enum_item) (union_item) (trait_item)] @class
        (impl_item type: [(type_identifier) @scope (generic_type type: (type_identifier) @scope)
          (scoped_type_identifier name: (type_identifier) @scope)]
          body: (declaration_list (function_item) @function))
        [(line_comment) (block_comment)] @comment
        """,
    ),
    _Grammar(
        "tree_sitter_java",
        (".java",),
        """
        [(class_declaration) (interface_declaration) (enum_declaration) (record_declaration)
          (annotation_type_declaration)] @class
        [(method_declaration body: (_)) (constructor_declaration)] @function
        [(line_comment) (block_comment)] @comment
        """,
    ),
    _Grammar("tree_sitter_c", (".c",), _C),
    # A `.h` is read as C++, which reads C too.
    _Grammar(
        "tree_sitter_cpp",
        (".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"),
        _C + "(class_specifier name: (_) body: (_)) @class",
    ),
    _Grammar(
        "tree_sitter_c_sharp",
        (".cs",),
        """
        [(class_declaration) (struct_declaration) (interface_declaration) (enum_declaration)
          (record_declaration)] @class
        [(method_declaration body: (_)) (constructor_declaration)] @function
        (comment) @comment
        """,
    ),
    _Grammar(
        "tree_sitter_ruby",
        (".rb",),
        "[(class) (module)] @class [(method) (singleton_method)] @function (comment) @comment",
    ),
    _Grammar(
        "tree_sitter_php",
        (".php",),
        """
        [(class_declaration) (interface_declaration) (trait_declaration) (enum_declaration)] @class
        [(function_definition) (method_declaration body: (_))] @function
        (comment) @comment
        """,
        "language_php",
    ),
    _Grammar(
        "tree_sitter_swift",
        (".swift",),
        """
        [(class_declaration) (protocol_declaration)] @class
        [(function_declaration) (init_declaration)] @function
        [(comment) (multiline_comment)] @comment
        """,
    ),
    _Grammar("tree_sitter_bash", (".sh", ".bash", ".zsh"), "(function_definition) @function (comment) @comment"),
    _Grammar(
        "tree_sitter_sql",
        (".sql",),
        "\n".join(
            [f"(statement ({node} (object_reference) @name)) @class" for node in _SQL_CLASSES]
            + [f"(statement ({node} (object_reference) @name)) @function" for node in _SQL_FUNCTIONS]
            + ["[(comment) (marginalia)] @comment"]
        ),
    ),
)
#: Nodes that only hold one definition: its lines and the comment above it start at the outermost of them.
_WRAPPERS = frozenset(
    {
        "export_statement",
        "lexical_declaration",
        "variable_declaration",
        "expression_statement",
        "template_declaration",
        "type_declaration",
    }
)
#: What a wrapper holds beside its definition.
_BESIDE = frozenset({"template_parameter_list", "decorator", "comment"})
#: Definitions named by their keyword.
_KEYWORD_NAMES = {"init_declaration": "init"}
#: What a comment's line starts and ends with, and the tags of a documentation comment (`<summary>`, `<p>`).
_COMMENT_MARKS = "/*#!-"
_TAG = re.compile(r"</?\w[^>]*>")


def _installed() -> frozenset[str]:
    if find_spec("tree_sitter") is None:
        return frozenset()
    return frozenset(s for g in _GRAMMARS if find_spec(g.module) is not None for s in g.suffixes)


@cache
def _grammar(suffix: str) -> tuple[Language, Query]:
    from tree_sitter import Language, Query

    grammar = next(g for g in _GRAMMARS if suffix in g.suffixes)
    language = Language(getattr(importlib.import_module(grammar.module), grammar.entry)())
    return language, Query(language, grammar.query)


class CodeProcessor:
    """Classes and functions of the languages with a grammar installed, methods under their class, and windows
    over the code between them."""

    name: ClassVar[str] = "code"
    suffixes: ClassVar[frozenset[str]] = _installed()

    def process(self, path: Path, workspace: Path) -> Derivative:
        from tree_sitter import Parser, QueryCursor

        text = path.read_text(errors="replace")
        lines = text.splitlines()
        plain = windows(lines)
        # The parser counts lines by "\n" alone; where `splitlines` counts another way (a form feed, a lone "\r"),
        # the lines of a definition would be off, so the file keeps its windows.
        if len(text) > MAX_CHARS or len(lines) != text.count("\n") + (not text.endswith("\n") and bool(text)):
            return plain
        try:
            language, query = _grammar(path.suffix.lower())
        except (ImportError, ValueError):  # a grammar this tree-sitter cannot load: the file is still indexed
            return plain
        root = Parser(language).parse(text.encode()).root_node
        chunks = _chunks(QueryCursor(query).matches(root), lines)
        if not any(c.kind != "window" for c in chunks) or len(chunks) > MAX_CHUNKS:
            return plain
        return Derivative(unit="line", units=lines, chunks=chunks, summary=plain.summary)


def _chunks(matches: list[tuple[int, dict[str, list[Node]]]], lines: list[str]) -> list[Chunk]:
    found: dict[tuple[int, int], dict[str, Node]] = {}
    comments: set[int] = set()  # rows a comment fills from their first character
    for _, captures in matches:
        for node in captures.get("comment", []):
            first, last = node.start_point.row, _last_row(node)
            if lines[first][: node.start_point.column].strip():
                first += 1  # a comment after code on its line
            comments.update(range(first, last + 1))
        for kind in ("class", "function"):
            for node in captures.get(kind, []):
                seen = found.setdefault((node.start_byte, node.end_byte), {})
                seen[kind] = node
                for extra in ("name", "scope"):  # a definition matched twice keeps its first name
                    if extra in captures and extra not in seen:
                        seen[extra] = captures[extra][0]

    chunks: list[Chunk] = []
    keys: dict[str, int] = {}
    classes: dict[str, str] = {}  # a class's name -> its key
    scoped: list[tuple[Chunk, str]] = []
    open_: list[tuple[int, Chunk]] = []  # the chunks a definition is inside of, by the byte they end at
    for (start, end), seen in sorted(found.items(), key=lambda kv: (kv[0][0], -kv[0][1])):
        node = seen.get("class") or seen["function"]
        name = _text(seen.get("name") or _named(node), NAME_CHARS) or _KEYWORD_NAMES.get(node.type, "")
        while open_ and open_[-1][0] <= start:
            open_.pop()
        inside = open_[-1][1] if open_ else None
        if not name or (inside is not None and inside.kind != "class"):
            continue  # anonymous, or local to a function
        scope = _text(seen["scope"], NAME_CHARS) if "scope" in seen and inside is None else ""
        owner = inside.key if inside is not None else scope
        key = f"{owner}.{name}" if owner else name
        keys[key] = keys.get(key, 0) + 1
        if keys[key] > 1:  # overloads, a class reopened
            key = f"{key} ({keys[key]})"
        kind: ChunkKind = "class" if "class" in seen else "method" if owner else "function"
        outer, first, summary = _above(node, comments)
        chunk = Chunk(
            key=key,
            kind=kind,
            title=_title(node, outer, lines, comments),
            summary=summary,
            start=first + 1,
            end=_last_row(node) + 1,
            parent=inside.key if inside is not None else None,
        )
        chunks.append(chunk)
        open_.append((end, chunk))
        if kind == "class":
            classes.setdefault(name, key)
        elif scope:
            scoped.append((chunk, scope))
    for chunk, scope in scoped:  # a method written outside its type is under it when the file has the type
        chunk.parent = classes.get(scope)
    return sorted(chunks + _between(chunks, lines, comments), key=lambda c: (c.start, -c.end))


def _named(node: Node) -> Node | None:
    """The node that names a definition: its ``name`` field, or what C and C++ declare inside nested declarators
    (``*Pool::take(int)`` is ``Pool::take``)."""
    name = node.child_by_field_name("name")
    if name is not None:
        return name
    inner = node
    while True:
        deeper = inner.child_by_field_name("declarator") or next(
            (c for c in inner.named_children if c.type.endswith("_declarator")), None
        )
        if deeper is None:
            return inner if inner is not node else None
        inner = deeper


def _text(node: Node | None, limit: int) -> str:
    return " ".join((node.text or b"").decode(errors="replace").split())[:limit] if node is not None else ""


def _last_row(node: Node) -> int:
    """The last row ``node`` has text on: a node that ends with its line's newline ends at column 0 of the next."""
    return node.end_point.row - (node.end_point.column == 0 and node.end_point.row > node.start_point.row)


def _above(node: Node, comments: set[int]) -> tuple[Node, int, str]:
    """The outermost node that holds only this definition, the row the definition starts on (its attributes are
    part of it) and the first line of the comment above it."""
    outer = node
    while (
        (parent := outer.parent) is not None
        and parent.type in _WRAPPERS
        and sum(c.type not in _BESIDE for c in parent.named_children) == 1
    ):
        outer = parent
    first = outer.start_point.row
    above = outer.prev_named_sibling
    while above is not None and above.type == "attribute_item":  # Rust's #[derive(...)], after the doc comment
        first, above = above.start_point.row, above.prev_named_sibling
    doc: list[str] = []
    row = first
    while above is not None and _last_row(above) == row - 1 and above.start_point.row in comments:
        doc[:0] = (above.text or b"").decode(errors="replace").splitlines()
        row, above = above.start_point.row, above.prev_named_sibling
    said = (_TAG.sub("", line).strip().strip(_COMMENT_MARKS).strip() for line in doc)
    return outer, first, next((line for line in said if line), "")[:SUMMARY_CHARS]


def _title(node: Node, outer: Node, lines: list[str], comments: set[int]) -> str:
    """The definition up to its body (`func (p *Pool) Take(n int) error`), or its first line when it has none."""
    holder = node.child_by_field_name("value") or node.child_by_field_name("right") or node
    body = holder.child_by_field_name("body")
    first = outer.start_point.row
    head = lines[first]
    if body is not None and body.start_byte > outer.start_byte and outer.text is not None:
        rows = outer.text[: body.start_byte - outer.start_byte].decode(errors="replace").split("\n")
        head = " ".join(row for n, row in enumerate(rows, first) if n not in comments)
    return " ".join(head.split())[:TITLE_CHARS].rstrip("{(=\\ ")


def _between(chunks: list[Chunk], lines: list[str], comments: set[int]) -> list[Chunk]:
    """Windows over the code no definition holds, where a run of it has GAP_LINES lines that are not comments."""
    held = [False] * (len(lines) + 2)
    for chunk in chunks:
        held[chunk.start : chunk.end + 1] = [True] * (chunk.end - chunk.start + 1)
    out: list[Chunk] = []
    line = 1
    while line <= len(lines):
        end = line
        while not held[end] and end < len(lines) and not held[end + 1]:
            end += 1
        code = [n for n in range(line, end + 1) if not held[n] and lines[n - 1].strip() and n - 1 not in comments]
        if len(code) >= GAP_LINES:
            out += window_chunks(lines, code[0], code[-1])
        line = end + 1
    return out
