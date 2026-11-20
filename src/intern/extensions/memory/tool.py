from __future__ import annotations

import asyncio
import copy
import re
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from intern.memory.drive import Drive, GitError, git
from intern.memory.format import LEAD_ID, ROOT_INDEX, Page, index_file
from intern.memory.search import Found, MemoryIndex, match
from intern.tools.base import Effect, Tool, ToolContext, ToolOutput, fail

MAX_LINE_CHARS = 300
TRACE_AROUND = 2
TRACE_MATCHES = 5
PINNED = "(already in context)"
_SOURCE = re.compile(r"^(?:evt#)?([0-9a-z]+)(?::([0-9a-z]+))?$")
_WORD = re.compile(r"[a-z0-9]{3,}")
Folder = Literal["map/", "user/", "lessons/", "skills/", "work/", "sessions/"]


class MemoryArgs(BaseModel):
    action: Literal["list", "read", "search", "trace"] = Field(
        description="list: the pages and folders in a folder. read: a page, or one block of it. search: pages and "
        "earlier sessions by words. trace: the session and step a line was recorded from."
    )
    path: str = Field(
        default="",
        description="list: a folder such as `map/` or `user/people/`, empty for the whole memory. read: a page such "
        "as `user/people/aiko-morita`, or one block such as `user/people/aiko-morita#b-events`.",
    )
    query: str = Field(
        default="",
        description="search: words to look for (names, ids, values, commands or what was asked). trace: the line "
        "being traced, to find it in an older session that has no step ids.",
    )
    folder: Folder | None = Field(
        default=None,
        description="search: only this folder, pages under map/, user/, lessons/, skills/ or work/, or the session "
        "transcripts under sessions/.",
    )
    max_results: int = Field(default=5, ge=1, le=20, description="search: most page blocks, and most sessions, shown.")
    source: str = Field(
        default="",
        description="trace: a line's source, `evt#<session>:<step>` from its [source: ...] tag, or a session id.",
    )


class MemoryTool(Tool):
    name = "memory"
    description = (
        "Look through memory from earlier sessions in this world, read-only, when the memory given does not hold "
        "what the task needs. `list` a folder's pages with their one-line summaries, `read` a page or one block, "
        "`search` pages and earlier sessions by words, and `trace` a line's [source: evt#...] to the session and "
        "step it was recorded from, to check the evidence before acting on it. Memory is read as it was when the "
        "session started; pages already in context are marked, and not shown again in full."
    )
    Args = MemoryArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"read"})
    cancel_on_abort = True

    def __init__(self, drive: Drive) -> None:
        self.drive = drive
        self.index = MemoryIndex(drive.root, drive.commit)
        self.pinned: set[str] = set()

    def for_subagent(self) -> MemoryTool:
        # A subagent's context has none of the parent's recalled pages.
        child = copy.copy(self)
        child.pinned = set()
        return child

    async def run(self, args: MemoryArgs, ctx: ToolContext) -> ToolOutput:
        match args.action:
            case "list":
                return self.list(args.path)
            case "read":
                return self.read(args.path, ctx.max_output_chars)
            case "search":
                if not match(args.query):
                    return fail("the query has no words to search for", "Pass names, ids or values.", argument="query")
                found = await asyncio.to_thread(self.index.search, args.query, args.folder, args.max_results)
                return ToolOutput(content=self.render(found, args.query))
            case "trace":
                return await asyncio.to_thread(self.trace, args.source, args.query)

    def _mark(self, path: str) -> str:
        return f" {PINNED}" if path in self.pinned else ""

    def list(self, path: str) -> ToolOutput:
        folder = _clean(path, str(self.drive.root))
        if folder and not folder.endswith("/"):
            if self.drive.page(folder) is not None:
                return fail(f"{folder} is a page, not a folder", f"Read it: action read, path {folder}.")
            folder += "/"
        found = self.drive.index(folder)
        if found is None:
            return fail(
                f"memory has no folder {folder}",
                "List the whole memory (empty path) to see its folders.",
                argument="path",
            )
        lines = [f"{index_file(folder)}: {found.title}" + (f"\n{found.lead}" if found.lead else "")]
        for entry in found.entries:
            if entry.folder:
                lines.append(f"- {entry.target}: {entry.description}")
            else:
                lines.append(f"- {entry.target}{self._mark(entry.target)}: {entry.description} · {entry.tokens} tok")
        if not found.entries:
            lines.append("(no pages)")
        return ToolOutput(content="\n".join(lines))

    def read(self, path: str, limit: int) -> ToolOutput:
        name, _, block_id = _clean(path, str(self.drive.root)).partition("#")
        if not name or name == ROOT_INDEX.removesuffix(".md") or name.endswith("/index"):
            return fail("that is a table of contents, not a page", "List the folder instead: action list.")
        page = self.drive.page(name)
        if page is None:
            return fail(
                f"memory has no page {name}",
                "List its folder (action list) or search for it (action search).",
                argument="path",
            )
        if block_id:
            return self._block(page, block_id)
        if name in self.pinned:
            contents = "\n".join(f"- {name}#{c.block_id}: {c.description}" for c in page.contents)
            return ToolOutput(
                content=f"{name} is already in context, from the memory given at the start. To see one block in "
                f"full, read it as {name}#<block>:\n{contents}"
            )
        text = page.render()
        if len(text) > limit:
            contents = "\n".join(f"- {name}#{c.block_id}: {c.description} · {c.tokens} tok" for c in page.contents)
            return ToolOutput(
                content=f"{name} is too long to show whole. Its lead:\n{page.lead_text()}\n\nRead one block as "
                f"{name}#<block>:\n{contents}"
            )
        return ToolOutput(content=text)

    def _block(self, page: Page, block_id: str) -> ToolOutput:
        if block_id == LEAD_ID:
            return ToolOutput(content=f"# {page.title} {{#{LEAD_ID}}}\n{page.lead_text()}")
        block = page.block(block_id)
        if block is None:
            ids = ", ".join(b.id for b in page.blocks)
            return fail(f"{page.path} has no block {block_id}", f"Its blocks: {LEAD_ID}, {ids}.", argument="path")
        return ToolOutput(content=f"{page.path}: {page.title}\n\n{block.render()}")

    def trace(self, source: str, line: str) -> ToolOutput:
        found = _SOURCE.match(source.strip())
        if found is None:
            return fail(
                f"{source!r} is not a source",
                "Pass the evt#<session>:<step> from a line's [source: ...] tag, or a session id.",
                argument="source",
            )
        session, step = found[1], found[2]
        parts: list[str] = []
        work = self._work_page(session)
        if work is not None:
            outcome = work.meta.get("outcome", "")
            parts.append(f"Work page {work.path}{f' ({outcome})' if outcome else ''}: {work.title}\n{work.lead_text()}")
        try:
            text = git(self.drive.root, "show", f"{self.drive.commit}:sessions/{session}.txt").decode()
        except GitError:
            text = ""
        if not text:
            if work is None:
                return fail(
                    f"memory holds no session {session}",
                    "It may have been forgotten at the user's request or removed by cleanup; do not rely on the line "
                    "without checking it at its place.",
                    argument="source",
                )
            parts.append(f"The transcript of session {session} is no longer in memory.")
            return ToolOutput(content="\n\n".join(parts))
        parts.append(_excerpt(session, text, step, line))
        return ToolOutput(content="\n\n".join(parts))

    def _work_page(self, session: str) -> Page | None:
        for name, text in self.drive.files.items():
            if name.startswith("work/") and f"\nsession: {session}\n" in text:
                return self.drive.page(name.removesuffix(".md"))
        return None

    def render(self, found: Found, query: str) -> str:
        if not found.pages and not found.sessions:
            return f"No memory page or earlier session mentions {query.strip()!r}."
        parts: list[str] = []
        if found.pages:
            entries = []
            for hit in found.pages:
                lines = "".join(f"\n  {line.strip()[:MAX_LINE_CHARS]}" for line in hit.lines)
                entries.append(f"- {hit.page}#{hit.block}{self._mark(hit.page)}: {hit.title}{lines}")
            parts.append("Pages (read one with action read, path page#block):\n" + "\n".join(entries))
        if found.sessions:
            entries = []
            for hit in found.sessions:
                head = "\n".join(line[:MAX_LINE_CHARS] for line in hit.head.splitlines())
                lines = "".join(f"\n  {line.strip()[:MAX_LINE_CHARS]}" for line in hit.lines)
                more = f"\n  [{hit.more} more matching lines]" if hit.more else ""
                entries.append(head + lines + more)
            parts.append("Sessions:\n" + "\n\n".join(entries))
        return "\n\n".join(parts)


def _clean(path: str, root: str) -> str:
    return path.strip().strip("[]").removeprefix(f"{root}/").removesuffix(".md").lstrip("./")


def _excerpt(session: str, text: str, step: str | None, line: str) -> str:
    rows = text.splitlines()
    head = [r for r in rows[:4] if r.strip()]
    want = set(_WORD.findall(line.lower()))

    def best(candidates: list[int]) -> list[int]:
        scored = sorted(((len(want & set(_WORD.findall(rows[i].lower()))), -i) for i in candidates), reverse=True)
        return sorted(-i for n, i in scored[:TRACE_MATCHES] if n)

    # One assistant message that made several calls is one entry, so several steps can share the id.
    hits = [i for i, r in enumerate(rows) if step and f" @{step} " in f"{r} "]
    note = ""
    if len(hits) > TRACE_MATCHES and want:
        hits = best(hits) or hits[:TRACE_MATCHES]
        note = f"The steps of entry {step} sharing most words with the traced line:"
    elif not hits and want:
        hits = best(list(range(4, len(rows))))
        note = "This session has no step ids; the lines sharing most words with the traced line:"
    elif not hits:
        note = "This session has no step ids; pass the traced line as query to find it." if step else ""
    around = TRACE_AROUND if len(hits) == 1 else 0
    near = {j for i in hits for j in range(i - around, i + around + 1)}
    near |= {i + 1 for i in near if i + 1 < len(rows) and rows[i + 1].startswith("    -> ")}
    shown = sorted(j for j in near if 4 <= j < len(rows))
    body: list[str] = []
    for k, j in enumerate(shown):
        if k and j != shown[k - 1] + 1:
            body.append("[...]")
        body.append(rows[j][:MAX_LINE_CHARS])
    return "\n".join([f"Transcript sessions/{session}.txt:", *head, *([note] if note else []), *body])
