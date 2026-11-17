"""The tool-summary extension: a run's tool calls counted for eval error analysis, live and from the journal."""

from __future__ import annotations

from intern.events import ExtensionEvent
from intern.extensions.tool_summary import ToolCallSummary, ToolSummary, summaries
from intern.journal import Journal, MessageEntry
from intern.messages import AssistantMessage, ToolCall
from intern.providers.fake import Turn


def summary_events(harness) -> list[ToolCallSummary]:
    return [
        ToolCallSummary.model_validate(e.data)
        for e in harness.recorder.of(ExtensionEvent)
        if e.extension == "tool-summary" and e.kind == "summary"
    ]


async def test_a_run_summary_counts_each_call_by_tool_and_where_it_failed(make_harness):
    harness = make_harness(
        [
            Turn(
                tool_calls=[("write", {"path": "a.txt", "content": "x"}), ("open_file", {"path": "a.txt"})],
                invalid_tool_calls=[("edit", '{"path": "a.txt", ')],
            ),
            Turn(tool_calls=[("read", {"offset": 3}), ("read", {"path": "missing.txt"})]),
            Turn(tool_calls=[("read", {"path": "a.txt"})]),
            Turn(text="done"),
        ],
        extensions=[ToolSummary()],
    )
    await harness.run()

    [summary] = summary_events(harness)
    assert (summary.turns, summary.calls, summary.ok) == (4, 6, 2)
    assert summary.failures_by_kind == {"invalid_json": 1, "unknown_tool": 1, "bad_arguments": 1, "failed": 1}
    assert {name: (s.calls, s.ok, s.failures) for name, s in summary.by_tool.items()} == {
        "write": (1, 1, {}),
        "open_file": (1, 0, {"unknown_tool": 1}),
        "edit": (1, 0, {"invalid_json": 1}),
        "read": (3, 1, {"bad_arguments": 1, "failed": 1}),
    }
    assert [(f.turn, f.tool, f.kind) for f in summary.failures] == [
        (1, "edit", "invalid_json"),
        (1, "open_file", "unknown_tool"),
        (2, "read", "bad_arguments"),
        (2, "read", "failed"),
    ]
    assert summary.failures[0].raw_arguments == '{"path": "a.txt", '

    # The journal alone gives the same summary, for runs that did not load the extension.
    assert summaries(harness.deps.journal.entries()) == [summary]


async def test_calls_an_earlier_run_left_without_a_result_count_in_the_next_run(make_harness):
    harness = make_harness([Turn(text="picked up")], extensions=[ToolSummary()])
    journal: Journal = harness.deps.journal
    cut_short = AssistantMessage(tool_calls=[ToolCall(id="c1", name="bash", arguments={"command": "make"})])
    journal.append(MessageEntry(message=cut_short, run_id="earlier"))
    await harness.run()

    [summary] = summary_events(harness)
    assert summary.failures_by_kind == {"interrupted": 1}
    assert (summary.failures[0].turn, summary.failures[0].tool) == (0, "bash")
    earlier, latest = summaries(journal.entries())
    assert earlier.calls == 0 and latest == summary
