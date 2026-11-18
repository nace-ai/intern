"""The optional web-read pack, with recoverable retained text rather than silent truncation.

With `[capabilities.web_reader] system1 = true`, a page longer than the preview is judged before the agent sees it: cut
into line-aligned parts, each judged by System 1 as something the task needs or not, reading the session as every
System 1 question does, and the parts it keeps, best first until the preview is full, come back in page order with
the skipped lines named. Extractive, as CRAG's decompose-then-recompose and Provence's context pruning are: the agent
reads the page's own words, never a summary, and the retained file still holds all of it.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

from intern.decisions import NoulAnswer, NoulQuestion, Question
from intern.tools.base import Effect, Tool, ToolContext, ToolOutput, fail, file_resource
from intern.web.reader import PageDocument, WebReader, WebReadError
from intern.web.storage import PrivateStorageError, publish_private_file

if TYPE_CHECKING:
    from intern.hooks import RunContext

PART_CHARS = 1_200
PARTS_PER_ASK = 16
"""Parts judged in one ask: what one System 1 request answers; a long page is several asks, so none has its middle
cut from the state."""
MAX_PARTS = 128
"""Parts judged of one page; the rest is left to the retained file."""
KEEP = 0.5
# shortcut: an unmeasured cut-off; set it from the paired eval before `system1` goes on by default.
QUESTION = "Does part {id} of the page hold something the agent needs for what the user asked, as the session shows it?"


class WebReadArgs(BaseModel):
    url: str = Field(min_length=1, max_length=8_192)


def _retain(text: str, digest: str, storage_dir: Path | None) -> Path:
    from intern.config import home_dir

    return publish_private_file(storage_dir or home_dir(os.environ) / "web-content", f"{digest}.txt", text.encode())


def parts(text: str) -> list[tuple[int, int]]:
    """Line-aligned (start, end) spans of up to PART_CHARS covering the text that is not blank; a longer line is cut."""
    spans: list[tuple[int, int]] = []
    start = pos = 0
    for line in text.splitlines(keepends=True):
        if pos + len(line) - start > PART_CHARS and pos > start:
            spans.append((start, pos))
            start = pos
        pos += len(line)
        while pos - start > PART_CHARS:
            spans.append((start, start + PART_CHARS))
            start += PART_CHARS
    spans.append((start, pos))
    return [(s, e) for s, e in spans if text[s:e].strip()]


async def judged(page: PageDocument, budget: int, path: Path, run: RunContext) -> str | None:
    """The parts of `page` System 1 judges the task needs, best first until `budget` characters, in page order, with
    the lines skipped between them; None when it keeps none."""
    text = page.text
    spans = parts(text)
    asked = min(len(spans), MAX_PARTS)

    async def ask(first: int) -> dict[str, float]:
        batch = range(first, min(first + PARTS_PER_ASK, asked))
        body = "\n\n".join(f"[p{i}]\n{text[spans[i][0] : spans[i][1]]}" for i in batch)
        state = f"{run.state()}\n\n# Page read with web_read: {page.title} ({page.final_url})\n\n{body}"
        questions: dict[str, Question] = {
            f"p{i}": NoulQuestion(instructions=QUESTION.format(id=f"p{i}")) for i in batch
        }
        answers = await run.decisions.ask("web_read", state, questions, turn=run.turn, subject=page.final_url)
        return {n: a.noul for n, a in answers.items() if isinstance(a, NoulAnswer)}

    scores: dict[str, float] = {}
    for batch in await asyncio.gather(*(ask(first) for first in range(0, asked, PARTS_PER_ASK))):
        scores.update(batch)
    kept: list[int] = []
    used = 0
    for i in sorted(range(asked), key=lambda i: -scores.get(f"p{i}", 0.0)):
        size = spans[i][1] - spans[i][0]
        if scores.get(f"p{i}", 0.0) < KEEP:
            break
        if used + size <= budget:
            kept.append(i)
            used += size
    if not kept:
        return None

    def line(offset: int) -> int:
        return text.count("\n", 0, offset) + 1

    out = [f"[System 1 kept {len(kept)} of {len(spans)} parts of this page, the ones the task needs, in page order.]"]
    after = 0  # the first part neither shown nor named as skipped
    for i in sorted(kept):
        if i > after:
            out.append(f"[... lines {line(spans[after][0])}-{line(spans[i - 1][1] - 1)} skipped ...]")
        out.append(text[spans[i][0] : spans[i][1]].rstrip("\n"))
        after = i + 1
    if after < len(spans):
        out.append(f"[... lines {line(spans[after][0])}-{line(spans[-1][1] - 1)} skipped ...]")
    out.append(f"[Full UTF-8 source: {path}; read with offset/limit.]")
    return "\n".join(out)


class WebReadTool(Tool):
    name = "web_read"
    description = "Read an HTTP(S) URL as untrusted source text. Returns source metadata and a retained full-text file."
    Args = WebReadArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"read", "write", "network"})

    def __init__(
        self,
        reader: WebReader | None = None,
        *,
        storage_dir: Path | None = None,
        display_dir: Path | None = None,
        system1: bool = False,
    ) -> None:
        self.reader = reader or WebReader()
        self.system1 = system1
        self.storage_dir = storage_dir
        # An application may map retained host files into an executor workspace.
        # Resource references retain the actual host path for the calling client.
        self.display_dir = display_dir

    async def run(self, args: WebReadArgs, ctx: ToolContext) -> ToolOutput:
        try:
            page = await self.reader.read(args.url, ctx.abort)
            path = await asyncio.to_thread(_retain, page.text, page.sha256, self.storage_dir)
            display_path = self.display_dir / path.name if self.display_dir is not None else path
            data = page.text.encode()
            preview_chars = max(1, ctx.max_output_chars // 2)
            metadata = page.model_dump(exclude={"text"}, mode="json")
            # This mapping is local serialization; typed PageDocument is the module boundary.
            content = json.dumps(metadata, ensure_ascii=False) + "\n\n"
            run = ctx.run if self.system1 and len(page.text) > preview_chars else None
            kept = await judged(page, preview_chars, display_path, run) if run is not None else None
            if kept is not None:
                content += kept
            else:
                if run is not None:
                    content += "[System 1 judged no part of this page to be what the task needs; its start follows.]\n"
                content += page.text[:preview_chars]
                if len(page.text) > preview_chars:
                    content += f"\n[TRUNCATED. Full UTF-8 source: {display_path}; read with offset/limit.]"
            return ToolOutput(content=content, resources=[file_resource(path, data)])
        except (WebReadError, OSError) as error:
            detail = (
                str(error)
                if isinstance(error, WebReadError | PrivateStorageError)
                else "Could not retain the extracted source."
            )
            return fail(detail, "Check the URL, use another source, or configure a supported reader.")
