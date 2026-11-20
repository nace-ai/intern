"""The Provider protocol every System 2 model goes through."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from intern.messages import AssistantMessage, ImagePart, Message
from intern.models.profile import Compat, Sampling


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, JsonValue]


class RetryPolicy(BaseModel):
    """How a model call that fails with a retryable error (a rate limit, an overloaded server, a dropped connection)
    is made again.

    The provider makes the request again after each of `provider_waits_s` while nothing of the reply has arrived.
    The run then requests the reply again after each of `reply_waits_s`, which also covers a stream that broke
    partway, and after those every `wait_out_s` until `wait_out_window_s` has passed since the first failure: ending
    the run after a minute or two of 429s threw away runs that had hours of budget left."""

    model_config = ConfigDict(frozen=True)

    provider_waits_s: tuple[float, ...] = (2.0, 8.0, 20.0)
    reply_waits_s: tuple[float, ...] = (5.0, 20.0)
    wait_out_s: float = 30.0
    wait_out_window_s: float = 1800.0


NO_RETRIES = RetryPolicy(provider_waits_s=(), reply_waits_s=(), wait_out_window_s=0.0)
"""Every call made once, for a client whose own gateway retries (`model_retries = false`)."""


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
    retries: RetryPolicy = Field(default_factory=RetryPolicy)


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
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
        code: str | None = None,
        meter: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.code = code
        """The Nace console's `error.code` (`free_exhausted`, `rate_limited`, …), for a request to it."""
        self.meter = meter
        """The free credit's meter the console named: `agent` or `system1`."""


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
        line = buffer.rstrip("\r")
        if line.startswith("data:") and not _whole(line[len("data:") :].strip()):
            # The connection closed inside an event. Like a stream that ends between events, that is worth a retry;
            # handed on, the half line would fail as undecodable data, which is not.
            raise ProviderError("the stream ended in the middle of an event", retryable=True)
        yield line


def _whole(data: str) -> bool:
    """Whether an event's data, on a last line that no newline ended, all arrived."""
    if data in ("", "[DONE]"):
        return True
    try:
        json.loads(data)
    except json.JSONDecodeError:
        return False
    return True


def image_base64(image: ImagePart) -> str | None:
    """A stored image as base64, or None when the file is gone (a resumed session whose spill directory
    was cleared): providers then say so in text instead of failing the request."""
    try:
        return base64.b64encode(Path(image.path).read_bytes()).decode()
    except OSError:
        return None


def missing_image_note(image: ImagePart) -> str:
    return f"[{image.source}: the image file is no longer available]"
