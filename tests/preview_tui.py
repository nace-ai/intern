"""Open the terminal UI on a scripted System 2 and System 1, to see its look and the mark's faces without keys.

    uv run python -m tests.preview_tui

Type anything and press Enter: the model takes a moment over each turn (the mark reads), runs two slow commands (its
eyes turn, a turnstile) and replies. A prompt with "fail" in it ends in a provider error (its eyes are crossed out).
`/dark` and `/light` switch the look; Ctrl+Q quits. Not a test: nothing here is collected.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from intern.messages import UserMessage
from intern.providers.base import ProviderRequest, StreamItem
from intern.providers.fake import FakeProvider, Turn

from .test_tui import FakeDecision, HarnessApp, make_opener

THINKS_S = 2.5


class Slow(FakeProvider):
    """The scripted provider, taking `THINKS_S` over each reply as a model would."""

    async def stream(self, request: ProviderRequest) -> AsyncIterator[StreamItem]:
        await asyncio.sleep(THINKS_S)
        async for item in super().stream(request):
            yield item


def script() -> Slow:
    step = 0

    def reply(request: ProviderRequest) -> Turn:
        nonlocal step
        last = request.messages[-1]
        asked = last.content if isinstance(last, UserMessage) and isinstance(last.content, str) else None
        step = 0 if asked is not None else step + 1
        prompt = next(
            (
                m.content
                for m in reversed(request.messages)
                if isinstance(m, UserMessage) and isinstance(m.content, str)
            ),
            "",
        )
        if step == 0:
            return Turn(text="Let me look.", tool_calls=[("bash", {"command": "sleep 2; echo first"})])
        if "fail" in prompt.lower():
            return Turn(error="the provider returned 503 (scripted)")
        if step == 1:
            return Turn(tool_calls=[("bash", {"command": "sleep 2; echo second"})])
        return Turn(text="Both commands ran. Type `fail` to see a run that fails.")

    return Slow(reply)


if __name__ == "__main__":
    opener = make_opener(Path(tempfile.mkdtemp()), [], system1=FakeDecision(), providers={"fake/model": script()})
    HarnessApp(opener).run()
