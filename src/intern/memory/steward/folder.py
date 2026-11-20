from __future__ import annotations

import ast
import contextlib
import csv
import difflib
import io
import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field

from intern.memory.format import (
    CODE_SUFFIXES,
    FOLDER_SUFFIXES,
    MAX_FOLDER_FILE_CHARS,
    MAX_FOLDER_FILES,
    RELATIONS_ID,
    FormatError,
    Page,
    inert,
    is_folder_file,
    is_folder_skill,
    parse_page,
    strip_source,
)
from intern.memory.steward.artifacts import Collected
from intern.memory.steward.write import (
    EARLIER,
    SKILL_BLOCKS,
    Gap,
    _identity,
    append,
    bullet,
    earlier,
    folder_skill_path,
    relate,
)
from intern.memory.steward.writer import TOLD, FolderDraft, FolderFile, HeldFolder, _flat, _values

FILES_BLOCK = ("b-folder", "Files")
LINE_BLOCKS = {"steps": SKILL_BLOCKS["how"], "pitfalls": SKILL_BLOCKS["fails"], "checks": SKILL_BLOCKS["rule"]}
# Front matter: how many sessions that succeeded the folder was built or confirmed from.
SUCCEEDED_KEY = "succeeded"
# A script kept from a session is that script with its paths and the user's values moved out, not new code.
MIN_KEPT_CODE = 0.3
# A table or template is the user's words: a value it holds that the user never said was made up.
MAX_UNSAID_SHARE = 0.1
_PLACEHOLDER = re.compile(r"\{\{?[^{}\n]{1,40}\}?\}")
_LEAD_COUNT = re.compile(r"Built from \d+ sessions? that succeeded\.")


@dataclass
class Folder:
    # A skill folder as the steward reads it: its page, and its files by name.
    page: Page
    files: dict[str, str] = field(default_factory=dict)

    @property
    def folder(self) -> str:
        return self.page.path.rsplit("/", 1)[0] + "/"

    @property
    def name(self) -> str:
        return self.page.meta.get("name", self.page.title)

    @property
    def when(self) -> str:
        return self.page.description.split(": ", 1)[-1].rstrip(".")

    def held(self) -> HeldFolder:
        lines = [
            strip_source(line)[2:]
            for block in self.page.blocks
            if block.id != RELATIONS_ID and not block.id.startswith(EARLIER[0])
            for line in block.bullets
        ]
        return HeldFolder(self.name, self.when, {f"h{i}": t for i, t in enumerate(lines, 1)}, dict(self.files))


def folders(files: dict[str, str], raw: dict[str, str]) -> dict[str, Folder]:
    found: dict[str, Folder] = {}
    for name, text in files.items():
        if not name.endswith(".md") or not is_folder_skill(name[:-3]):
            continue
        try:
            page = parse_page(name[:-3], text)
        except FormatError:
            continue
        if page.kind == "skill":
            found[page.path] = Folder(page)
    for name, text in raw.items():
        owner = f"{name.rsplit('/', 1)[0]}/SKILL"
        if is_folder_file(name) and owner in found:
            found[owner].files[name.rsplit("/", 1)[1]] = text
    return found


def static_problem(name: str, content: str) -> str:
    # Checked without running it: code that does not parse, a table that does not load.
    if not name.endswith(FOLDER_SUFFIXES):
        return f"{name} is not a kind of file a skill folder keeps"
    if len(content) > MAX_FOLDER_FILE_CHARS:
        return f"{name} is {len(content)} characters, more than {MAX_FOLDER_FILE_CHARS}"
    try:
        if name.endswith(".py"):
            ast.parse(content, filename=name)
        elif name.endswith(".json"):
            json.loads(content)
        elif name.endswith((".csv", ".tsv")):
            rows = list(csv.reader(io.StringIO(content), delimiter="\t" if name.endswith(".tsv") else ","))
            if len({len(r) for r in rows if r}) > 1:
                return f"{name}: its rows have different numbers of columns"
        elif name.endswith(".sh"):
            done = subprocess.run(["bash", "-n"], input=content.encode(), capture_output=True, check=False)
            if done.returncode != 0:
                return f"{name}: {done.stderr.decode(errors='replace').strip()[:200]}"
    except (SyntaxError, ValueError, csv.Error, OSError) as e:
        return f"{name}: {e}"[:300]
    return ""


def kept_code(content: str, source: str) -> bool:
    return difflib.SequenceMatcher(None, source, content, autojunk=False).ratio() >= MIN_KEPT_CODE


def _data(name: str, content: str) -> str:
    # A table's keys and header row are its layout; only what it holds must come from the user.
    if name.endswith(".json"):
        with contextlib.suppress(ValueError):
            return "\n".join(_leaves(json.loads(content)))
    if name.endswith((".csv", ".tsv")):
        return content.split("\n", 1)[1] if "\n" in content else ""
    return content


def _leaves(value: object) -> list[str]:
    if isinstance(value, dict):
        return [leaf for v in value.values() for leaf in _leaves(v)]
    if isinstance(value, list):
        return [leaf for v in value for leaf in _leaves(v)]
    return [] if value is None or isinstance(value, bool) else [str(value)]


def said(content: str, source: str) -> bool:
    values = _values(_PLACEHOLDER.sub(" ", content))
    seen = _flat(source)
    unsaid = [v for v in values if _flat(v) not in seen]
    return len(unsaid) <= int(MAX_UNSAID_SHARE * len(values))


def provenance(file: FolderFile, collected: Collected, held: dict[str, str], succeeded: bool) -> str:
    # A script is one that worked in a session that succeeded, or a held one; a table or template is what the user
    # said, or a held one changed only by what the user said.
    told = "\n".join(s.text for s in collected.said)
    base = held.get(file.origin) if file.origin in held else held.get(file.name)
    if file.name.endswith(CODE_SUFFIXES):
        ran = next((a for a in collected.artifacts if a.name == file.origin and a.code), None)
        if ran is not None and succeeded and ran.ran and ran.ok and kept_code(file.content, ran.content):
            return ""
        if base is not None and kept_code(file.content, base) and (succeeded or said(file.content, f"{base}\n{told}")):
            return ""
        return f"{file.name} is not a script that worked in this session or one the folder holds"
    if said(_data(file.name, file.content), f"{base or ''}\n{told}"):
        return ""
    return f"{file.name} holds values the user did not give"


def apply(
    page: Callable[[str, str, str], Page],
    folder: Folder | None,
    draft: FolderDraft,
    kept: list[FolderFile],
    date: str,
    event: str,
    pointer: str,
    succeeded: bool,
) -> tuple[dict[str, str], list[Gap]]:
    # `pointer` cites the session on the lines of the files it keeps.
    path = folder.page.path if folder is not None else folder_skill_path(draft.skill)
    name = inert(draft.skill)
    target = page(path, "skill", f"{name}: {inert(draft.when).rstrip('.')}.")
    target.meta.setdefault("name", name)
    target.title = target.meta["name"]
    count = int(target.meta.get(SUCCEEDED_KEY, "0") or 0) + (1 if succeeded else 0)
    target.meta[SUCCEEDED_KEY] = str(count)
    note = f"Built from {count} session{'' if count == 1 else 's'} that succeeded."
    target.lead = [line for line in target.lead if not _LEAD_COUNT.fullmatch(line)] + [note]
    gaps: list[Gap] = []
    held = folder.held() if folder is not None else None
    for ident in draft.retired:
        text = held.lines.get(ident, "") if held is not None else ""
        for block in target.blocks:
            line = next((b for b in block.bullets if strip_source(b)[2:] == text), None)
            if line is None:
                continue
            block.lines.remove(line)
            if not block.bullets:
                target.blocks.remove(block)
            if append(target, *EARLIER, [earlier(line, date)]):
                gaps.append(Gap(f"no room on [[{path}]] for an earlier line", ""))
            break
    for line in draft.lines:
        base_id, heading = LINE_BLOCKS[line.kind]
        if append(target, base_id, heading, [bullet(inert(line.text), line.pointer, date)]):
            gaps.append(Gap(f"no room on [[{path}]] for: {inert(line.text)}", line.pointer))
    have = dict(folder.files) if folder is not None else {}
    written: dict[str, str] = {}
    for file in kept:
        if file.name not in have and len(have) >= MAX_FOLDER_FILES:
            gaps.append(Gap(f"{path} holds {MAX_FOLDER_FILES} files; not kept: {file.name}", pointer))
            continue
        have[file.name] = file.content
        written[f"{path.rsplit('/', 1)[0]}/{file.name}"] = file.content
        # A file's line names it and says how to use it; a changed file's line is said anew.
        for block in target.blocks:
            if block.id.startswith(FILES_BLOCK[0]):
                block.lines = [b for b in block.lines if _identity(b) != file.name]
        target.blocks = [b for b in target.blocks if b.lines or b.id == RELATIONS_ID]
        origin = "user said" if file.origin == TOLD else f"from {inert(file.origin)}"
        line = bullet(f"`{file.name}`: {inert(file.about)} ({origin}, {date})", pointer, date)
        if append(target, *FILES_BLOCK, [line]):
            gaps.append(Gap(f"no room on [[{path}]] for the line of {file.name}", pointer))
    relate(target, "seen_in", event)
    return written, gaps
