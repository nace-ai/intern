"""An extension for the eval's recall question: every tool call is refused, so the answer comes from what the model
was sent. Loaded by name (`--extension no_tools:NoTools`); the tool list, and with it the cached prompt, stays as
it was."""

from __future__ import annotations

from intern.hooks import Extension, Refuse, RunContext, ToolCallDecision
from intern.messages import ToolCall


class NoTools(Extension):
    name = "no-tools"

    def for_subagent(self) -> Extension:
        return self

    async def tool_call(self, call: ToolCall, ctx: RunContext) -> ToolCallDecision | None:
        return Refuse(
            reason="No tool is available for this question.",
            next_action="Answer from what you already know of this session, and say so when you do not know.",
        )
