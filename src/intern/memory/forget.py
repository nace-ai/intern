from __future__ import annotations

import re
from collections.abc import Iterator

from pydantic import BaseModel, Field

from intern.memory.format import (
    FOLDER_INDEX,
    LEAD_ID,
    RELATIONS_ID,
    UNREAD_FOLDERS,
    FormatError,
    build_indexes,
    is_page_file,
    links,
    parse_page,
    strip_source,
)
from intern.memory.steward.write import GRAVEYARD, SESSIONS_FOLDER

MARKER = "[removed at the user's request]"
# A page's title as a line: removing it removes the page, as its title is also its name in every table of contents.
TITLE = "title"
_WORD = re.compile(r"[a-z0-9]{3,}")
# Words a request to forget is made of, which say nothing about what to forget.
_ASKING_TEXT = (
    "the and for with that this from into what was were has have had not are you your please about any all "
    "forget forgot forgotten delete deleted remove removed erase drop stop keep keeping memory remember wrong "
    "incorrect true false anymore longer don dont isn isnt wasn wasnt never"
)
_ASKING = frozenset(_ASKING_TEXT.split())
_LABEL_TAIL = re.compile(r" \([^()]*\d{4}-\d{2}-\d{2}[^()]*\)$")
# A removed line's text shorter than this is too common to redact wherever it appears in a transcript.
MIN_COVERED_CHARS = 16


class Spot(BaseModel):
    # One line of a drive file: a page's lead or block line, or a transcript line.
    file: str
    block: str = ""
    line: str


class Removal(BaseModel):
    lines: list[Spot] = Field(default_factory=list)
    pages: list[str] = Field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.lines and not self.pages


def words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def body(line: str) -> str:
    return _LABEL_TAIL.sub("", strip_source(line)).removeprefix("- ").strip()


def page_files(files: dict[str, str]) -> list[str]:
    return [
        n
        for n in files
        if is_page_file(n) or (n.startswith(GRAVEYARD) and n.endswith(".md") and not n.endswith(f"/{FOLDER_INDEX}"))
    ]


def _spots(files: dict[str, str], transcripts: dict[str, str]) -> Iterator[Spot]:
    for name in page_files(files):
        try:
            page = parse_page(name[:-3], files[name])
        except FormatError:
            continue
        yield Spot(file=name, block=TITLE, line=page.title)
        yield from (Spot(file=name, block=LEAD_ID, line=line) for line in page.lead if line.strip())
        for block in page.blocks:
            if block.id != RELATIONS_ID:
                yield from (Spot(file=name, block=block.id, line=line) for line in block.lines if line.strip())
    for name, text in transcripts.items():
        yield from (Spot(file=name, line=line) for line in text.splitlines() if line.strip())


def candidates(files: dict[str, str], transcripts: dict[str, str], text: str, pages: int, lines: int) -> list[Spot]:
    # The lines sharing most of a request's words: the most page lines and the most transcript lines.
    want = words(text) - _ASKING
    if not want:
        return []
    need = 1 if len(want) <= 2 else 2
    scored = [(n, spot) for spot in _spots(files, transcripts) if (n := len(want & words(spot.line))) >= need]
    scored.sort(key=lambda s: -s[0])
    on_pages = [s for _, s in scored if not s.file.startswith(SESSIONS_FOLDER)][:pages]
    in_transcripts = [s for _, s in scored if s.file.startswith(SESSIONS_FOLDER)][:lines]
    return on_pages + in_transcripts


def matching(files: dict[str, str], transcripts: dict[str, str], text: str) -> list[Spot]:
    want = words(text)
    return [spot for spot in _spots(files, transcripts) if want and want <= words(spot.line)] if want else []


def widened(files: dict[str, str], transcripts: dict[str, str], removal: Removal) -> Removal:
    # A session whose task line goes is forgotten whole, so its work page goes as a removed title does. Then every
    # transcript line that repeats what goes (a page line, or a gone page's title or path, as a recalled page, a
    # reminder or a search showed it) goes too.
    ids = {
        s.file.removeprefix(SESSIONS_FOLDER).removesuffix(".txt")
        for s in removal.lines
        if s.file.startswith(SESSIONS_FOLDER) and s.line.startswith("task: ")
    }
    lines = list(removal.lines)
    pages = removal.pages + [n for i in sorted(ids) if (n := f"{SESSIONS_FOLDER}{i}.txt") not in removal.pages]
    for name in page_files(files):
        if not any(f"\nsession: {i}\n" in files[name] for i in ids):
            continue
        try:
            spot = Spot(file=name, block=TITLE, line=parse_page(name[:-3], files[name]).title)
        except FormatError:
            continue
        if spot not in lines:
            lines.append(spot)
    paths = [p.removesuffix(".md") for p in removal.pages] + [s.file[:-3] for s in lines if s.block == TITLE]
    said = [body(s.line) for s in lines if not s.file.startswith(SESSIONS_FOLDER)] + paths
    said = list({b for b in said if len(b) >= MIN_COVERED_CHARS})
    for name, text in transcripts.items():
        if name in pages:
            continue
        for line in text.splitlines():
            if any(b in line for b in said) and (spot := Spot(file=name, line=line)) not in lines:
                lines.append(spot)
    return Removal(lines=lines, pages=pages)


def apply(files: dict[str, str], transcripts: dict[str, str], removal: Removal) -> tuple[dict[str, str], list[str]]:
    # The files a removal rewrites and the files it deletes. A page the removal leaves with nothing in it goes too,
    # and a line linking a page that goes is dropped, as the page's name may be what was asked to be forgotten.
    drop: dict[str, set[tuple[str, str]]] = {}
    for spot in removal.lines:
        drop.setdefault(spot.file, set()).add((spot.block, spot.line))
    gone = {p.removesuffix(".md") for p in removal.pages}
    gone |= {s.file.removesuffix(".md") for s in removal.lines if s.block == TITLE}
    while True:
        changed, emptied = _pages(files, drop, gone)
        if not emptied:
            break
        gone |= emptied
    removed = sorted(n for n in page_files(files) if n[:-3] in gone)
    removed += sorted(n for n in removal.pages if n in transcripts)
    changed = {n: t for n, t in changed.items() if n not in removed}
    for name, text in transcripts.items():
        lines = {line for block, line in drop.get(name, set()) if not block}
        if lines and name not in removed:
            changed[name] = "\n".join(MARKER if line in lines else line for line in text.split("\n"))
    return changed, removed


def _pages(
    files: dict[str, str], drop: dict[str, set[tuple[str, str]]], gone: set[str]
) -> tuple[dict[str, str], set[str]]:
    changed: dict[str, str] = {}
    emptied: set[str] = set()
    for name in page_files(files):
        path, text = name[:-3], files[name]
        lines = drop.get(name, set())
        if path in gone or (not lines and not gone.intersection(links(text))):
            continue
        try:
            page = parse_page(path, text)
        except FormatError:
            continue
        page.lead = _kept(page.lead, LEAD_ID, lines, gone)
        for block in page.blocks:
            block.lines = _kept(block.lines, block.id, lines, gone)
        page.blocks = [b for b in page.blocks if any(line.strip() for line in b.lines) or b.id == RELATIONS_ID]
        if lines and not any(line.strip() for line in page.lead) and all(b.id == RELATIONS_ID for b in page.blocks):
            emptied.add(path)
            continue
        page.refresh_contents()
        if page.render() != text:
            changed[name] = page.render()
    return changed, emptied


def _kept(body: list[str], block_id: str, drop: set[tuple[str, str]], gone: set[str]) -> list[str]:
    return [line for line in body if (block_id, line) not in drop and not gone.intersection(links(line))]


def settle(files: dict[str, str], changed: dict[str, str], removed: list[str]) -> tuple[dict[str, str], list[str]]:
    # The tables of contents after a removal: rebuilt, and a folder left with no pages loses its own.
    merged = {n: t for n, t in (files | changed).items() if n not in removed}
    built = build_indexes(merged)
    removed = removed + [
        n for n in merged if n.endswith(f"/{FOLDER_INDEX}") and not n.startswith(UNREAD_FOLDERS) and n not in built
    ]
    changed = {n: t for n, t in changed.items() if n not in removed}
    changed |= {n: t for n, t in built.items() if files.get(n) != t and n not in removed}
    return changed, removed
