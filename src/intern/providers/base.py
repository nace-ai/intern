"""The Provider protocol every System 2 model goes through."""

from __future__ import annotations

import asyncio
import base64
import contextlib
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, Field, JsonValue

from intern.messages import AssistantMessage, ImagePart, Message
from intern.models.profile import Compat, Sampling


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, JsonValue]


class ProviderRequest(BaseModel):
    """One model call. Hooks may change sampling, reasoning effort and the output cap before it is sent."""

    model: str
    messages: list[Message]
    tools: list[ToolSpec] = Field(default_factory=list)
    max_output_tokens: int
    sampling: Sampling = Field(default_factory=Sampling)
    reasoning_effort: str | None = None
    reasoning_replay: bool = False
    compat: Compat = Field(default_factory=Compat)
    session_id: str | None = None


class TextChunk(BaseModel):
    kind: Literal["text"] = "text"
    text: str


class ReasoningChunk(BaseModel):
    kind: Literal["reasoning"] = "reasoning"
    text: str


class Completed(BaseModel):
    kind: Literal["completed"] = "completed"
    message: AssistantMessage


StreamItem = TextChunk | ReasoningChunk | Completed


# Network failures worth a retry. OSError covers what httpx lets through unwrapped, such as an
# ssl.SSLError ("bad record mac") or a connection reset while reading the stream.
TRANSIENT_ERRORS: tuple[type[Exception], ...] = (httpx.TransportError, OSError)


class ProviderError(Exception):
    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class Provider(Protocol):
    """Streams one reply. The last item is always `Completed`; failures raise `ProviderError`."""

    def stream(self, request: ProviderRequest) -> AsyncIterator[StreamItem]: ...


async def retrying(
    attempt: Callable[[], AsyncGenerator[StreamItem]], waits_s: Sequence[float], provider: str
) -> AsyncIterator[StreamItem]:
    """The items of one `attempt()`, made again after each of `waits_s` while it fails with a retryable error before
    yielding anything. Once an item is out the reply cannot be taken back, so a later failure raises at once; a
    network error is raised as a retryable `ProviderError`."""
    for wait in (*waits_s, None):
        emitted = False
        try:
            async with contextlib.aclosing(attempt()) as items:
                async for item in items:
                    emitted = True
                    yield item
            return
        except (ProviderError, *TRANSIENT_ERRORS) as error:
            retryable = not isinstance(error, ProviderError) or error.retryable
            if emitted or not retryable or wait is None:
                if isinstance(error, ProviderError):
                    raise
                raise ProviderError(f"{type(error).__name__} from {provider}: {error}", retryable=True) from error
        await asyncio.sleep(wait)


async def sse_lines(response: httpx.Response) -> AsyncIterator[str]:
    """Split a server-sent event stream on newlines only. httpx's `aiter_lines` also splits on U+2028
    and U+0085, which can appear raw inside a JSON data line."""
    buffer = ""
    async for text in response.aiter_text():
        buffer += text
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            yield line.rstrip("\r")
    if buffer:
        yield buffer.rstrip("\r")


def image_base64(image: ImagePart) -> str | None:
    """A stored image as base64, or None when the file is gone (a resumed session whose spill directory
    was cleared): providers then say so in text instead of failing the request."""
    try:
        return base64.b64encode(Path(image.path).read_bytes()).decode()
    except OSError:
        return None


def missing_image_note(image: ImagePart) -> str:
    return f"[{image.source}: the image file is no longer available]"
