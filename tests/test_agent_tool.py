"""The subagents capability: off by default; on, the agent has an `agent` tool that hands tasks to subagents within
the configured limits, and a session that cannot run them safely refuses to turn it on."""

from __future__ import annotations

from typing import Any, ClassVar, cast

import pytest
from pydantic import ValidationError

from intern import hooks as h
from intern.capability_settings import CapabilitySettings
from intern.capability_toggles import applied, toggles
from intern.events import SubagentEnd
from intern.messages import JsonObject, JsonValue, ToolCall, ToolResultMessage
from intern.models.registry import ConfigError
from intern.providers.base import ProviderRequest
from intern.providers.fake import Turn
from intern.subagents import NOTE, RESULT_NOTE, SubagentSettings
from intern.tools.agent import MAX_TASKS, AgentArgs, AgentTool
from intern.tools.base import ToolOutput

from .fake_openai import Reply
from .support import Harness, WithTools
from .test_session_setup import configured, outputs

ON = "[capabilities.subagents]\nenabled = true\nmax_turns = 5\n"


class Gate(h.Extension):
    """A third-party check of tool calls that does not say how it runs in a subagent."""

    name: ClassVar[str] = "company-gate"

    async def tool_call(self, call: ToolCall, ctx: h.RunContext) -> h.ToolCallDecision | None:
        return None


# -- turning it on ----------------------------------------------------------------------------------------------


async def test_subagents_are_off_by_default(tmp_path):
    async with configured(tmp_path, [], "") as (session, _, _, _):
        assert "agent" not in {tool.name for tool in session.deps.tools}
        assert session.deps.subagents is None


async def test_turned_on_the_agent_hands_a_task_to_a_subagent_and_reads_its_report(tmp_path):
    replies = [
        Reply(
            tool_calls=[
                ("agent", {"tasks": [{"task": "Say which files are here.", "name": "scout", "tools": ["find"]}]})
            ]
        ),
        Reply(text="Two files: a.py and b.py."),  # the subagent
        Reply(text="The scout found two files."),
    ]
    async with configured(tmp_path, replies, ON) as (session, model, _, _):
        assert "agent" in {tool.name for tool in session.deps.tools}
        assert session.deps.subagents == SubagentSettings(max_turns=5)
        assert session.deps.resolve_model is not None
        outcome = await session.run("what is in this folder?")
        assert outcome.reason == "completed" and outcome.final_text == "The scout found two files."
        [report] = outputs(session)
        assert "scout" in report and "completed" in report and RESULT_NOTE in report
        assert "Two files: a.py and b.py." in report
        child = model.requests[1]
        assert NOTE in str(child["messages"][0]["content"])
        assert [tool["function"]["name"] for tool in child.get("tools", [])] == ["find"]
        assert session.usage.output_tokens > 0


async def test_a_session_with_an_unready_check_refuses_to_turn_subagents_on(tmp_path):
    with pytest.raises(ConfigError, match="company-gate"):
        async with configured(tmp_path, [], ON, extensions=[f"{__name__}:Gate"]):
            pass


async def test_the_same_extension_is_fine_with_subagents_off(tmp_path):
    async with configured(tmp_path, [], "", extensions=[f"{__name__}:Gate"]) as (session, _, _, _):
        assert session.deps.subagents is None


def test_the_switch_is_listed_off_and_flips_on():
    caps = CapabilitySettings()
    row = next(toggle for toggle in toggles(caps, caps, ["base"]) if toggle.id == "cap.subagents")
    assert not row.on
    on = applied(caps, caps, {"cap.subagents": True}, ["base"])
    assert on.subagents.enabled and not caps.subagents.enabled
    assert not applied(on, caps, {"cap.subagents": False}, ["base"]).subagents.enabled


@pytest.mark.parametrize(
    "table",
    [
        {"enabled": True, "max_parallel": 0},
        {"enabled": True, "model": "no-provider"},
        {"enabled": True, "models": ["also bad"]},
        {"enabled": True, "timeout_s": -1},
        {"enabled": True, "turns": 3},
    ],
)
def test_bad_subagent_settings_are_refused(table):
    with pytest.raises(ValidationError):
        CapabilitySettings.model_validate({"subagents": table})


# -- the tool ---------------------------------------------------------------------------------------------------


def is_child(request: ProviderRequest) -> bool:
    return any(NOTE in getattr(m, "content", "") for m in request.messages if m.role == "system")


DEFAULT = SubagentSettings()


def make(make_harness, script, settings: SubagentSettings | None = DEFAULT) -> Harness:
    harness: Harness = make_harness(script, extensions=[WithTools()])
    harness.deps.tools = [*harness.deps.tools, AgentTool(settings)]
    harness.deps.subagents = settings
    return harness


def parent(tasks: list[JsonValue]):
    def script(request: ProviderRequest, child) -> Turn:
        if is_child(request):
            return child(request)
        if not any(isinstance(m, ToolResultMessage) for m in request.messages):
            return Turn(tool_calls=[("agent", {"tasks": tasks})])
        return Turn(text="done")

    return script


def result_of(harness: Harness) -> ToolOutput | str:
    message = next(m for m in harness.provider.requests[-1].messages if isinstance(m, ToolResultMessage))
    return (
        message.result.content
        if message.result.status == "ok"
        else f"{message.result.status}: {message.result.content}"
    )


async def test_without_subagents_the_tool_says_to_do_the_task_alone(make_harness):
    script = parent([{"task": "x"}])
    harness = make(make_harness, lambda r: script(r, lambda _: Turn(text="never")), settings=None)
    await harness.run()
    result = result_of(harness)
    assert isinstance(result, str) and result.startswith("error:") and "not available" in result
    assert not harness.recorder.of(SubagentEnd)


async def test_each_report_gets_its_share_of_the_output(make_harness):
    long = "x" * 200_000

    def child(request: ProviderRequest) -> Turn:
        text = str(request.messages[-1].content)  # type: ignore[union-attr]
        return Turn(text=f"BEGIN-{text[-1]} {long} END-{text[-1]}")

    script = parent([{"task": "task A", "tools": ["read"]}, {"task": "task B", "tools": ["read"]}])
    harness = make(make_harness, lambda r: script(r, child))
    await harness.run()
    result = result_of(harness)
    assert isinstance(result, str)
    # Both reports keep their head and their end: one long report never pushes the other out.
    for name in ("A", "B"):
        assert f"BEGIN-{name}" in result and f"END-{name}" in result


async def test_a_refused_request_is_an_error_the_agent_can_fix(make_harness):
    script = parent([{"task": "x", "tools": ["no_such_tool"]}])
    harness = make(make_harness, lambda r: script(r, lambda _: Turn(text="never")))
    await harness.run()
    result = result_of(harness)
    assert isinstance(result, str) and result.startswith("error:") and "no_such_tool" in result
    assert "call agent again" in result


async def test_when_every_subagent_fails_the_result_is_an_error(make_harness):
    script = parent([{"task": "x", "tools": ["read"]}, {"task": "y", "tools": ["read"]}])
    harness = make(make_harness, lambda r: script(r, lambda _: Turn(error="down")))
    await harness.run()
    result = result_of(harness)
    assert isinstance(result, str) and result.startswith("error:") and result.count("provider_error") == 2


def test_the_tool_takes_one_to_eight_tasks_and_declares_no_effects():
    tool = AgentTool()
    assert isinstance(tool.parse({"tasks": []}), ToolOutput)
    many: list[JsonValue] = [{"task": "t"}] * (MAX_TASKS + 1)
    assert isinstance(tool.parse({"tasks": many}), ToolOutput)
    assert isinstance(tool.parse({"tasks": [{"task": ""}]}), ToolOutput)
    assert isinstance(tool.parse({"tasks": [{"task": "t", "tools": []}]}), ToolOutput)  # a subagent with no tools
    one: JsonObject = {"tasks": [{"task": "t"}]}
    assert isinstance(tool.parse(one), AgentArgs)
    # The call itself changes nothing; the guard judges each call the subagents make.
    assert tool.effects == frozenset() and tool.read_only


def schema_of(tool: AgentTool) -> dict[str, Any]:
    return cast(dict[str, Any], tool.spec().parameters)


def test_the_schema_offers_a_model_only_from_the_allowed_list():
    plain = schema_of(AgentTool(SubagentSettings()))
    assert "model" not in plain["$defs"]["AgentTask"]["properties"]  # nothing to choose, nothing to guess
    listed = schema_of(AgentTool(SubagentSettings(model="a/one", models=["a/two", "a/one"])))
    assert listed["$defs"]["AgentTask"]["properties"]["model"]["enum"] == ["a/one", "a/two"]


def test_the_schema_offers_as_many_tasks_as_a_run_may_start():
    assert schema_of(AgentTool(SubagentSettings(max_children=2)))["properties"]["tasks"]["maxItems"] == 2
    assert schema_of(AgentTool(SubagentSettings(max_children=50)))["properties"]["tasks"]["maxItems"] == MAX_TASKS


async def test_more_tasks_than_a_call_may_start_are_refused_before_any_starts(make_harness):
    script = parent([{"task": "a"}, {"task": "b"}, {"task": "c"}])
    harness = make(make_harness, lambda r: script(r, lambda _: Turn(text="never")), SubagentSettings(max_children=2))
    await harness.run()
    result = result_of(harness)
    assert isinstance(result, str) and result.startswith("error:") and "at most 2" in result
    assert not any(is_child(r) for r in harness.provider.requests)


async def test_a_spent_run_says_to_do_the_rest_alone_not_to_retry(make_harness):
    def script(request: ProviderRequest) -> Turn:
        if is_child(request):
            return Turn(text="done")
        results = sum(isinstance(m, ToolResultMessage) for m in request.messages)
        return Turn(tool_calls=[("agent", {"tasks": [{"task": f"t{results}"}]})]) if results < 2 else Turn(text="end")

    harness = make(make_harness, script, SubagentSettings(max_children=1))
    await harness.run()
    second = [m.result for m in harness.provider.requests[-1].messages if isinstance(m, ToolResultMessage)][-1]
    assert second.status == "error" and second.error is not None
    assert second.error.retry_safe is False and "remaining work yourself" in second.error.next_action


async def test_an_empty_model_is_no_choice(make_harness):
    script = parent([{"task": "x", "tools": ["read"], "model": ""}])
    harness = make(make_harness, lambda r: script(r, lambda _: Turn(text="fine")))
    await harness.run()
    result = result_of(harness)
    assert isinstance(result, str) and result.startswith("1 of 1 subagents completed.")


async def test_eight_long_reports_fit_the_tool_result_and_each_keeps_its_head_and_end(make_harness):
    long = "y" * 300_000

    def child(request: ProviderRequest) -> Turn:
        text = str(request.messages[-1].content)  # type: ignore[union-attr]
        return Turn(text=f"BEGIN-{text[-1]} {long} END-{text[-1]}")

    names = "ABCDEFGH"
    script = parent([{"task": f"task {n}", "tools": ["read"]} for n in names])
    harness = make(make_harness, lambda r: script(r, child))
    seen: list[ToolOutput] = []
    tool = next(t for t in harness.deps.tools if isinstance(t, AgentTool))
    original = tool.run

    async def spy(args, ctx):
        output = await original(args, ctx)
        seen.append(output)
        assert len(output.content) <= ctx.max_output_chars  # cut once, here, never again by the loop
        return output

    tool.run = spy  # type: ignore[method-assign]
    await harness.run()
    content = seen[0].content
    assert content.startswith("8 of 8 subagents completed.")
    for name in names:
        assert f"BEGIN-{name}" in content and f"END-{name}" in content


async def test_a_bad_subagent_model_stops_the_session_from_opening_with_the_switch_on(tmp_path):
    toml = '[capabilities.subagents]\nenabled = true\nmodel = "openrouter/vendor/no-key-for-this"\n'
    with pytest.raises(ConfigError, match="Subagents cannot be turned on"):
        async with configured(tmp_path, [], toml):
            pass


async def test_with_the_guard_on_a_subagents_call_is_judged_and_refused(tmp_path):
    replies = [
        Reply(tool_calls=[("agent", {"tasks": [{"task": "List the root folder with sudo.", "tools": ["bash"]}]})]),
        Reply(tool_calls=[("bash", {"command": "sudo ls /root"})]),  # the subagent
        Reply(text="It was refused."),  # the subagent's report
        Reply(text="The subagent could not do it."),
    ]
    toml = ON + "[capabilities.guard]\nenabled = true\nsystem1 = false\n"
    async with configured(tmp_path, replies, toml) as (session, model, _, _):
        await session.run("what is in /root?")
        child_after_call = model.requests[2]
        results = [m for m in child_after_call["messages"] if m.get("role") == "tool"]
        assert results and "the guard refused this call" in str(results[-1]["content"])
        assert "It was refused." in outputs(session)[0]


async def test_a_subagent_model_is_bound_inside_the_sessions_budget(tmp_path):
    from intern.providers.failover import BudgetProvider

    from .fake_openai import MODEL

    toml = (
        "[capabilities.session_budget]\nmax_calls = 50\n"
        f'[capabilities.subagents]\nenabled = true\nmodel = "vllm/{MODEL}"\n'
    )
    async with configured(tmp_path, [], toml) as (session, _, _, _):
        assert session.deps.resolve_model is not None
        child = await session.deps.resolve_model(f"vllm/{MODEL}")
        parent = session.deps.system2.provider
        assert isinstance(child.provider, BudgetProvider) and isinstance(parent, BudgetProvider)
        assert child.provider.budget is parent.budget  # one budget counts the parent's calls and the subagent's


async def test_a_subagent_model_is_bound_without_the_parents_fallbacks():
    from intern.providers.failover import BudgetPolicy, BudgetProvider, RunBudget
    from intern.providers.fake import FakeProvider
    from intern.session_setup import _child_resolver

    from .support import binding

    raw = FakeProvider([])

    async def resolve(ref: str):
        return binding(raw)

    budget = RunBudget(BudgetPolicy(max_calls=5))
    bind = _child_resolver(resolve, budget)
    assert bind is not None
    child = await bind("any/model")
    # The model's own provider inside the budget: the session's System 2 fallbacks are the parent's backups.
    assert isinstance(child.provider, BudgetProvider) and child.provider.provider is raw
    assert _child_resolver(None, budget) is None
