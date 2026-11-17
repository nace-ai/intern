"""The `agent` tool: the agent hands self-contained tasks to subagents (`intern.subagents`) and reads their reports.

The tool itself changes nothing, so it declares no effects: each subagent's own calls pass the guard and every other
check as the parent's would. Its schema follows the session's settings: as many tasks as a run may start (at most
eight a call), and a `model` only when the session names models subagents may run on. It answers with a line saying
how many subagents completed, then each one's report, the output split evenly between them so a long report never
pushes another out.
"""

from __future__ import annotations

from typing import Any, ClassVar, cast

from pydantic import BaseModel, Field

from intern.providers.base import ToolSpec
from intern.subagents import (
    AGENT_TOOL,
    SubagentRefused,
    SubagentResult,
    SubagentSettings,
    SubagentsExhausted,
    SubagentSpec,
)
from intern.tools.base import Effect, Tool, ToolContext, ToolOutput, fail

MAX_TASKS = 8
SEPARATOR = "\n\n---\n\n"
CUT = "\n[... {omitted} characters of this report omitted{where} ...]\n"
CUT_RESERVE = len(CUT.format(omitted=10**12, where="")) + 600  # the marker, with room for the journal's path


class AgentTask(BaseModel):
    task: str = Field(
        min_length=1,
        description="The whole task: what to find or do, where, what is known already, and what to report back. The "
        "subagent sees none of this conversation.",
    )
    name: str = Field(default="subagent", min_length=1, max_length=64, description="A short label, e.g. `scout`.")
    tools: list[str] | None = Field(
        default=None,
        min_length=1,
        description='Your tools it may use, e.g. ["read", "grep", "find"]; omit for all you may pass on. Subagents '
        "run at the same time only when each lists tools that only read; one that can write or run commands runs "
        "alone.",
    )
    model: str | None = Field(default=None, description="One of the models listed; omit for the default.")


class AgentArgs(BaseModel):
    tasks: list[AgentTask] = Field(min_length=1, max_length=MAX_TASKS, description="One task per subagent.")


class AgentTool(Tool):
    name = AGENT_TOOL
    description = (
        "Hand self-contained tasks to subagents and get their reports back. A subagent starts with a clean context "
        "and your tools (or fewer), works on its own, and returns only its final report, so your context stays "
        "small. Use it for work that needs many reads but a short answer (where is X handled across the code, what "
        "do these documents say about Y), or for independent searches that can run at the same time. Do not use it "
        "for a single read or search you can make yourself. Write each task in full: the subagent cannot ask you "
        "anything. Its report is notes to check: verify what you rely on."
    )
    Args = AgentArgs
    effects: ClassVar[frozenset[Effect]] = frozenset()

    def __init__(self, settings: SubagentSettings | None = None) -> None:
        self.settings = settings or SubagentSettings()

    @property
    def max_tasks(self) -> int:
        return min(MAX_TASKS, self.settings.max_children)

    @property
    def models(self) -> list[str]:
        return [m for m in dict.fromkeys([self.settings.model, *self.settings.models]) if m]

    def spec(self) -> ToolSpec:
        spec = super().spec()
        schema = cast(dict[str, Any], spec.parameters)
        schema["properties"]["tasks"]["maxItems"] = self.max_tasks
        task = schema["$defs"]["AgentTask"]["properties"]
        if self.models:
            task["model"] = {
                "enum": self.models,
                "description": "The model the subagent runs on; omit for the default.",
            }
        else:
            task.pop("model", None)  # nothing to choose: a small model is not tempted to guess a name
        return spec

    async def run(self, args: AgentArgs, ctx: ToolContext) -> ToolOutput:
        if ctx.subagents is None:
            return fail(
                "subagents are not available in this run",
                "Do the task yourself with your other tools.",
                retry_safe=False,
            )
        if len(args.tasks) > self.max_tasks:
            return fail(
                f"{len(args.tasks)} tasks given; this session runs at most {self.max_tasks} in one call",
                f"Call agent with at most {self.max_tasks} tasks, or do the rest yourself.",
                argument="tasks",
            )
        specs = [
            SubagentSpec(task=t.task, name=t.name, tools=t.tools, model=t.model or None)  # "" is no choice
            for t in args.tasks
        ]
        try:
            results = await ctx.subagents.run_many(specs)
        except SubagentsExhausted as e:
            return fail(str(e), "Do the remaining work yourself with your other tools.", retry_safe=False)
        except SubagentRefused as e:
            return fail(str(e), "Fix that task (its tools, model or count) and call agent again, or do it yourself.")
        completed = sum(result.status == "completed" for result in results)
        head = f"{completed} of {len(results)} subagents completed."
        room = ctx.max_output_chars - len(head) - len(SEPARATOR) * len(results)
        share = max(room // len(results) - CUT_RESERVE, 200)
        content = SEPARATOR.join([head, *(_fit(result, share) for result in results)])
        return ToolOutput(status="ok" if completed else "error", content=content)


def _fit(result: SubagentResult, limit: int) -> str:
    """A report within `limit` characters (and the cut marker): its start (the status and the answer a report leads
    with) and its end (where it sums up), cut by character, since one report may be a single long line. The marker
    names the subagent's journal, where the whole reply is, when it is kept."""
    text = result.report()
    if len(text) <= limit:
        return text
    head, tail = text[: limit // 2], text[len(text) - limit // 2 :]
    where = f"; the whole reply is in {result.journal}" if result.journal else ""
    return head + CUT.format(omitted=len(text) - len(head) - len(tail), where=where) + tail
