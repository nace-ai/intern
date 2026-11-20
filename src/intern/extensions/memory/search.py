from __future__ import annotations

import asyncio
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from intern.memory.drive import Drive
from intern.memory.format import page_file
from intern.memory.search import Found, MemoryIndex, match
from intern.tools.base import Effect, Tool, ToolContext, ToolOutput, fail

MAX_LINE_CHARS = 300


class MemorySearchArgs(BaseModel):
    query: str = Field(description="Words to look for: names, ids, values, commands or what was asked.")
    folder: Literal["map/", "user/", "lessons/", "skills/", "work/", "sessions/"] | None = Field(
        default=None,
        description="Search only this folder: pages under map/, user/, lessons/, skills/ or work/, or the session "
        "transcripts under sessions/.",
    )
    max_results: int = Field(default=5, ge=1, le=20, description="Most page blocks, and most sessions, shown.")


class MemorySearchTool(Tool):
    name = "memory_search"
    description = (
        "Search memory pages and the transcripts of earlier sessions in this world for words, when the memory given "
        "does not hold what the task needs. Returns the best-matching page blocks first, each with the file to read, "
        "then the best-matching sessions, each with its task, outcome and matching lines."
    )
    Args = MemorySearchArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"read"})
    cancel_on_abort = True

    def __init__(self, drive: Drive) -> None:
        self.drive = drive
        self.index = MemoryIndex(drive.root, drive.commit)

    async def run(self, args: MemorySearchArgs, ctx: ToolContext) -> ToolOutput:
        if not match(args.query):
            return fail("the query has no words to search for", "Pass names, ids or values.", argument="query")
        found = await asyncio.to_thread(self.index.search, args.query, args.folder, args.max_results)
        return ToolOutput(content=self.render(found, args.query))

    def render(self, found: Found, query: str) -> str:
        if not found.pages and not found.sessions:
            return f"No memory page or earlier session mentions {query.strip()!r}."
        parts: list[str] = []
        if found.pages:
            entries = []
            for hit in found.pages:
                path = self.drive.resolve(page_file(hit.page))
                lines = "".join(f"\n  {line.strip()[:MAX_LINE_CHARS]}" for line in hit.lines)
                entries.append(f"- {hit.page}#{hit.block} ({path}): {hit.title}{lines}")
            parts.append("Pages:\n" + "\n".join(entries))
        if found.sessions:
            entries = []
            for hit in found.sessions:
                head = "\n".join(line[:MAX_LINE_CHARS] for line in hit.head.splitlines())
                lines = "".join(f"\n  {line.strip()[:MAX_LINE_CHARS]}" for line in hit.lines)
                more = f"\n  [{hit.more} more matching lines]" if hit.more else ""
                entries.append(head + lines + more)
            parts.append("Sessions:\n" + "\n\n".join(entries))
        return "\n\n".join(parts)
