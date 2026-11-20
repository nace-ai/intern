"""The memory steward's writer against a local OpenAI-compatible server: a model that thinks past the writer's
timeout at its own effort still writes the session's memory, and a model that refuses a reasoning effort is never
sent one."""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler
from typing import Any

import pytest

from intern.memory.steward import writer
from intern.memory.steward.extract import Extract, Turn
from tests.fake_openai import FakeOpenAI, send_json

ANSWER = {"title": "Fix the date parser", "work": "bug-fix", "story": [], "items": []}


class Model(FakeOpenAI):
    """Answers the writer's non-streaming chat completions: at low effort at once; at its own effort it thinks past
    the writer's timeout, as GLM 5.3 does on a session's trace; and `refuses_effort` answers any effort with the 400
    an endpoint that takes none sends."""

    def __init__(self, refuses_effort: bool = False) -> None:
        super().__init__([])
        self.refuses_effort = refuses_effort

    def answer(self, handler: BaseHTTPRequestHandler, body: bytes | None) -> bool:
        if body is None:
            return False
        request: dict[str, Any] = json.loads(body)
        self.requests.append(request)
        effort = request.get("reasoning_effort")
        if self.refuses_effort and effort is not None:
            send_json(handler, {"error": {"message": "reasoning_effort is not supported with this model"}}, 400)
            return True
        if effort is None and not self.refuses_effort:
            time.sleep(writer.TIMEOUT_S * 3)
        reply = {"choices": [{"message": {"role": "assistant", "content": json.dumps(ANSWER)}}], "usage": {}}
        send_json(handler, reply)
        return True


def session() -> Extract:
    asked = Turn(role="user", text="The date parser drops the time zone; fix it.", pointer="j:1", order=1, at="")
    done = Turn(role="assistant", text="Fixed: it keeps the offset now.", pointer="j:2", order=2, at="")
    return Extract(
        session_id="s1", cwd="/work", date="2026-11-19", task="", stop="completed", observations=[], notes=[],
        turns=[asked, done],
    )  # fmt: skip


@pytest.mark.parametrize(
    ("ref", "env_prefix", "refuses_effort"),
    [
        ("fireworks/accounts/fireworks/models/glm-5p3", "FIREWORKS", False),
        ("nace/glm-5.3", "NACE", False),
        ("openai/gpt-4o", "OPENAI", True),
    ],
    ids=["glm-5.3-on-fireworks", "glm-5.3-on-the-console", "gpt-4o-takes-no-effort"],
)
async def test_the_steward_writes_a_sessions_memory_whatever_effort_its_writer_takes(
    monkeypatch, ref, env_prefix, refuses_effort
):
    monkeypatch.setattr(writer, "TIMEOUT_S", 0.5)
    with Model(refuses_effort) as model:
        env = {f"{env_prefix}_BASE_URL": model.url, f"{env_prefix}_API_KEY": "test-key"}
        story, _, _ = await writer.draft(writer.writer_from_ref(ref, env), session(), "succeeded", [], [], [])
    assert story is not None and story.title == ANSWER["title"]
