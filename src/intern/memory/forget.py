from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Iterator

from pydantic import BaseModel, Field

from intern.memory.format import (
    FOLDER_INDEX,
    LEAD_ID,
    RELATIONS_ID,
    SCHEMA_FILE,
    UNREAD_FOLDERS,
    FormatError,
    build_indexes,
    is_page_file,
    links,
    parse_page,
    strip_source,
)
from intern.memory.steward.write import GRAVEYARD, SESSIONS_FOLDER, WORK_FOLDER, built_lead

MARKER = "[removed at the user's request]"
# A page's title as a line: removing it removes the page, as its title is also its name in every table of contents.
TITLE = "title"
# The file of a line that is not in the drive yet: what the session being written said before it was asked to forget.
SESSION = "this session"
_WORD = re.compile(r"[a-z0-9]{3,}")
_TOKEN = re.compile(r"[A-Za-z0-9]+")
_PLURALS = (("ies", "y"), ("es", ""), ("s", ""))
_ENDINGS = ("ing", "ed", "ly", "e", "y")
MIN_STEM_CHARS = 3
_SENTENCE_END = re.compile(r"(?<!\bDr\.)(?<!\bMr\.)(?<!\bMs\.)(?<!\bMrs\.)(?<!\bSt\.)(?<!\bvs\.)(?<=[.!?])\s+")
# A word held by no more lines than this share of the drive's, or this many, names what a request is about: a
# line sharing it alone is offered. A commoner word needs a second one beside it.
RARE_SHARE = 0.05
RARE_LINES = 8
WORK_KINDS = "work kinds"
# Words a request to forget is made of, which say nothing about what to forget.
_ASKING_TEXT = (
    "the and for with that this from into what was were has have had not are you your please about any all "
    "forget forgot forgotten delete deleted remove removed erase drop stop keep keeping memory remember wrong "
    "incorrect true false anymore longer don dont isn isnt wasn wasnt never wipe scrub strike struck purge clear "
    "discard disregard would rather should must told said tell say note know everything anything something thing "
    "record kept anywhere mistake sorry"
)
_LABEL_TAIL = re.compile(r" \([^()]*\d{4}-\d{2}-\d{2}[^()]*\)$")
# A removed line's text shorter than this is too common to redact wherever it appears in a transcript.
MIN_COVERED_CHARS = 16
# What starts a line and is never one of its sentences: a transcript's task, turn or step with its entry id, or a
# page line's attribute.
_HEAD = re.compile(r"^(?:task: |[usa]\d+ \[[^\]]*\] (?:@\S+ )?(?:\$ )?|\s*-> |(?:\(preference\) )?[a-z][a-z ]{0,24}: )")
_CONTENT = re.compile(r"[A-Za-z0-9]")
# A list number or a lone mark between sentences is no sentence of its own: it goes only with its line.
_WORDED = re.compile(r"[A-Za-z]{2}")
# A line of more sentences than this is offered whole.
MAX_LINE_PARTS = 6


class Spot(BaseModel):
    # One line of a drive file: a page's lead or block line, or a transcript line.
    file: str
    block: str = ""
    line: str
    # A sentence of the line when only it goes; empty when the whole line goes.
    part: str = ""
    # What the part becomes on a page, when a rewrite keeps what was not asked to be forgotten; empty removes it.
    kept: str = ""

    @property
    def text(self) -> str:
        # What goes: the part, or a page line's words without its label and source, or a transcript line.
        if self.part:
            return self.part
        return body(self.line) if self.file.endswith(".md") else self.line


class Removal(BaseModel):
    lines: list[Spot] = Field(default_factory=list)
    pages: list[str] = Field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.lines and not self.pages


def words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def stem(word: str) -> str:
    # A word without its ending, so "redundancies" finds "redundancy". Not a dictionary form: only equal for both.
    if not word.endswith("ss"):
        for end, put in _PLURALS:
            if word.endswith(end) and len(word) - len(end) >= MIN_STEM_CHARS:
                word = word[: -len(end)] + put
                break
    for end in _ENDINGS:
        if word.endswith(end) and len(word) - len(end) >= MIN_STEM_CHARS:
            return word[: -len(end)]
    return word


def stems(text: str) -> set[str]:
    return {stem(w) for w in _WORD.findall(text.lower())}


_ASKING = frozenset(stem(w) for w in _ASKING_TEXT.split())


def sentences(text: str) -> list[str]:
    # A message cut where it can be removed in part: at its line ends and after each sentence.
    return [s.strip() for line in text.splitlines() for s in _SENTENCE_END.split(line) if s.strip()]


def body(line: str) -> str:
    return _LABEL_TAIL.sub("", strip_source(line)).removeprefix("- ").strip()


def parts(spot: Spot) -> list[Spot]:
    # A line of several sentences is offered sentence by sentence, so what was not asked about stays with it. A
    # title names its page and stays whole.
    if spot.part or spot.block == TITLE or spot.file == SESSION:
        return [spot]
    text = spot.text
    head = m[0] if (m := _HEAD.match(text)) else ""
    found = [s for s in dict.fromkeys(sentences(text[len(head) :])) if _WORDED.search(s)]
    if not 1 < len(found) <= MAX_LINE_PARTS:
        return [spot]
    return [spot.model_copy(update={"part": s}) for s in found]


def cut(line: str, spots: list[Spot], marker: str = "") -> str | None:
    # The line without the parts that go, each rewritten as kept or replaced by the marker; None when the whole
    # line goes or nothing of it is left.
    if any(not s.part for s in spots):
        return None
    for s in spots:
        line = _without(line, s.part, s.kept or marker)
    rest = (line if marker else body(line)).replace(MARKER, "")
    rest = rest[len(m[0]) :] if (m := _HEAD.match(rest)) else rest
    return line if _CONTENT.search(rest) else None


def _without(line: str, part: str, put: str) -> str:
    if put:
        return line.replace(part, put)
    for found in (f" {part}", f"{part} "):
        if found in line:
            return line.replace(found, "")
    return line.replace(part, "")


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


def _sharing(
    spots: list[Spot], want: set[str], need: int, alone: Callable[[str, int], bool], skip: list[Spot], most: int
) -> list[Spot]:
    # The lines holding most of the wanted words, a rare word counting for more: the most page lines and half as
    # many transcript lines. A line is in on `need` of the words, or on one that is enough `alone` (the word and
    # how many lines hold it).
    shared = [(spot, want & stems(spot.line)) for spot in spots]
    held = Counter(w for _, found in shared for w in found)
    skipped = {(s.file, s.block, s.line) for s in skip}
    scored = [
        (sum(math.log(1 + len(spots) / held[w]) for w in found), spot)
        for spot, found in shared
        if (len(found) >= need or any(alone(w, held[w]) for w in found))
        and (spot.file, spot.block, spot.line) not in skipped
    ]
    scored.sort(key=lambda s: -s[0])
    on_pages = [s for _, s in scored if not s.file.startswith(SESSIONS_FOLDER)][:most]
    in_transcripts = [s for _, s in scored if s.file.startswith(SESSIONS_FOLDER)][: most // 2]
    return [p for s in on_pages + in_transcripts for p in parts(s)]


def _rare(spots: list[Spot]) -> int:
    return max(RARE_LINES, int(len(spots) * RARE_SHARE))


def candidates(files: dict[str, str], transcripts: dict[str, str], text: str, most: int) -> list[Spot]:
    # The lines a request may mean: those sharing its words, whatever their endings. One shared word is enough when
    # few lines hold it, as it then names what the request is about.
    want = stems(text) - _ASKING
    spots = list(_spots(files, transcripts))
    rare = _rare(spots)
    return _sharing(spots, want, 1 if len(want) <= 2 else 2, lambda _, held: held <= rare, [], most)


def earlier(turns: list[str], text: str, most: int) -> list[Spot]:
    # The sentences a session said, apart from a request in it and what it did about the request, that share a
    # word with the request, long enough to be taken out of what is written from the session.
    want = stems(text) - _ASKING
    found = [s for turn in turns for s in sentences(turn) if len(s) >= MIN_COVERED_CHARS]
    shared = sorted(((len(want & stems(s)), s) for s in dict.fromkeys(found)), key=lambda p: -p[0])
    return [Spot(file=SESSION, line=s) for n, s in shared if n][:most]


def _values(line: str) -> set[str]:
    # The words of a line that name one thing: a number, or a capitalised word that does not start its sentence.
    found: set[str] = set()
    for sentence in sentences(body(line)):
        tokens = _TOKEN.findall(sentence)
        found |= {t for i, t in enumerate(tokens) if _numbered(t) or (i and t[0].isupper())}
    return {stem(w) for t in found for w in _WORD.findall(t.lower())}


def _numbered(word: str) -> bool:
    return any(c.isdigit() for c in word)


def followers(
    files: dict[str, str], transcripts: dict[str, str], text: str, picked: list[Spot], skip: list[Spot], most: int
) -> list[Spot]:
    # The lines that hold the values of the picked page lines, where the request did not spell them out: an
    # address, a name or a number, wherever else memory wrote it. Two of a line's values tie another line to it, or
    # one number. A page's lead and title sum up more than one line, and a transcript line holds a whole message,
    # so neither says which words are the value.
    spots = list(_spots(files, transcripts))
    held = Counter(w for spot in spots for w in stems(spot.line))
    rare = _rare(spots)
    said = [s for s in picked if s.block not in (TITLE, LEAD_ID) and s.file in files]
    values = {w for s in said for w in _values(s.text) - stems(text) - _ASKING if held[w] <= rare}
    return _sharing(spots, values, 2, lambda word, _: _numbered(word), [*picked, *skip], most)


def matching(files: dict[str, str], transcripts: dict[str, str], text: str) -> list[Spot]:
    want = words(text)
    return [spot for spot in _spots(files, transcripts) if want and want <= words(spot.line)] if want else []


def widened(files: dict[str, str], transcripts: dict[str, str], removal: Removal) -> Removal:
    # A session whose task line goes whole, every sentence of it, is forgotten whole, so its work page goes as a
    # removed title does; one whose task loses only some sentences keeps the rest. Then what goes is taken out of
    # every transcript line that repeats it (a page line or sentence, as a recalled page or a reminder showed it),
    # and a line naming a gone page's title or path, or a forgotten session by its id, as a search listed it, goes.
    tasks: dict[tuple[str, str], list[Spot]] = {}
    for s in removal.lines:
        if s.file.startswith(SESSIONS_FOLDER) and s.line.startswith("task: "):
            tasks.setdefault((s.file, s.line), []).append(s)
    ids = {
        name.removeprefix(SESSIONS_FOLDER).removesuffix(".txt")
        for (name, line), spots in tasks.items()
        if cut(line, spots, MARKER) is None
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
    pages_said = [s for s in lines if not s.file.startswith(SESSIONS_FOLDER)]
    said = {b for s in pages_said if s.block != TITLE and len(b := s.text) >= MIN_COVERED_CHARS}
    named = [*paths, *(s.line for s in pages_said if s.block == TITLE)]
    named = {b for b in named if len(b) >= MIN_COVERED_CHARS} | {f"session: {i}" for i in ids}
    for name, text in transcripts.items():
        if name in pages:
            continue
        for line in text.splitlines():
            whole = any(b in line for b in named)
            found = [Spot(file=name, line=line)] if whole else [Spot(file=name, line=line, part=b) for b in said]
            lines += [s for s in found if (whole or s.part in line) and s not in lines]
    return Removal(lines=lines, pages=pages)


def apply(files: dict[str, str], transcripts: dict[str, str], removal: Removal) -> tuple[dict[str, str], list[str]]:
    # The files a removal rewrites and the files it deletes. A page the removal leaves with nothing in it goes too,
    # and a line linking a page that goes is dropped, as the page's name may be what was asked to be forgotten. So
    # may the name of a kind of work be: one left with no session is no longer listed.
    drop: dict[str, dict[tuple[str, str], list[Spot]]] = {}
    for spot in removal.lines:
        drop.setdefault(spot.file, {}).setdefault((spot.block, spot.line), []).append(spot)
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
        lines = {line: spots for (block, line), spots in drop.get(name, {}).items() if not block}
        if lines and name not in removed:
            changed[name] = "\n".join(
                (cut(line, lines[line], MARKER) or MARKER) if line in lines else line for line in text.split("\n")
            )
    left = {_work_kind(n) for n in files if n not in removed}
    unused = {_work_kind(n) for n in removed} - left - {""}
    if unused and SCHEMA_FILE in files:
        changed[SCHEMA_FILE] = _without_kinds(files[SCHEMA_FILE], unused)
    return changed, removed


def _work_kind(name: str) -> str:
    parts = name.split("/")
    return parts[1] if name.startswith(WORK_FOLDER) and len(parts) == 3 and is_page_file(name) else ""


def _without_kinds(schema: str, kinds: set[str]) -> str:
    lines = schema.splitlines()
    for i, line in enumerate(lines):
        key, sep, value = line.partition(":")
        if sep and key.strip() == WORK_KINDS:
            kept = [v.strip() for v in value.split(",") if v.strip() and v.strip() not in kinds]
            lines[i] = f"{WORK_KINDS}: {', '.join(kept)}"
    return "\n".join(lines) + "\n"


def _pages(
    files: dict[str, str], drop: dict[str, dict[tuple[str, str], list[Spot]]], gone: set[str]
) -> tuple[dict[str, str], set[str]]:
    changed: dict[str, str] = {}
    emptied: set[str] = set()
    for name in page_files(files):
        path, text = name[:-3], files[name]
        lines = drop.get(name, {})
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
        # A lead built from the page's lines is built again, so it sums up only what stays; alone it is no content.
        built = built_lead(page) if lines else None
        bare = built is not None or not any(line.strip() for line in page.lead)
        if lines and bare and all(b.id == RELATIONS_ID for b in page.blocks):
            emptied.add(path)
            continue
        if built is not None:
            page.lead = [built]
        page.refresh_contents()
        if page.render() != text:
            changed[name] = page.render()
    return changed, emptied


def _kept(body: list[str], block_id: str, drop: dict[tuple[str, str], list[Spot]], gone: set[str]) -> list[str]:
    kept: list[str] = []
    for line in body:
        if gone.intersection(links(line)):
            continue
        spots = drop.get((block_id, line))
        if (new := cut(line, spots) if spots else line) is not None:
            kept.append(new)
    return kept


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
