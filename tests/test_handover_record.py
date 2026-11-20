"""What System 2 is sent of a hand-over's turns at the next message: a record of them (`intern.handover_record`)."""

from __future__ import annotations

from dataclasses import replace

from intern import handover_record
from intern.capability_settings import HandoffConfig
from intern.context import TokenMeter, build_view
from intern.decisions import Answers, ChoiceAnswer, Question
from intern.events import Handoff
from intern.handoff import HandoffTarget
from intern.journal import CompactionEntry, EventEntry, MessageEntry, StateEntry
from intern.messages import AssistantMessage, ToolResultMessage, UserMessage
from intern.providers.fake import FakeProvider, Turn
from intern.session import Session

from .support import binding

WRITES_B = "mkdir .cache; echo x > .cache/y; printf 'x\\ny\\n' > b.txt; echo 12 passed"
SCRIPT = """cat > notes.txt <<'EOF'
first line of the file
second line of the file
EOF
python3 - <<'EOF'
for n in range(1, 51):
    print("row", n)
EOF"""


class Judge:
    """A System 1 that judges every turn ready for the hand-over."""

    name = "judge"

    async def decide(self, state: str, questions: dict[str, Question]) -> Answers:
        return Answers(answers={"ready": ChoiceAnswer(choice="ready", probabilities={"ready": 1.0})})


def handed_over(make_harness, command=WRITES_B):
    """A session whose first run System 2 starts and a cheaper model finishes in three turns: a shell command that
    writes a file, a write with nothing said, and the reply."""
    strong = [
        Turn(text="built", tool_calls=[("write", {"path": "a.txt", "content": "one\n"})]),
        Turn(text="second answer"),
        Turn(text="third answer"),
    ]
    harness = make_harness(strong, system1=Judge())
    cheap = FakeProvider(
        [
            Turn(text="fixing", tool_calls=[("bash", {"command": command})]),
            Turn(tool_calls=[("write", {"path": "c.txt", "content": "c\n"})]),
            Turn(text="all done"),
        ],
        "cheap/small",
    )
    config = HandoffConfig(min_turn=1, streak=1)
    harness.deps.handoff = HandoffTarget(binding=replace(binding(cheap), ref="cheap/small"), config=config)
    return harness, cheap, Session(harness.deps)


def sent_to_system2(harness):
    messages = harness.provider.requests[-1].messages
    said = [m.text for m in messages if isinstance(m, AssistantMessage)]
    harness_says = [m.content for m in messages if isinstance(m, UserMessage) and "[Record" in m.content]
    results = [m.result.content for m in messages if isinstance(m, ToolResultMessage)]
    return messages, said, harness_says, results


def journaled[E](harness, kind: type[E]) -> list[E]:
    events = [e.event for e in harness.deps.journal.active_path() if isinstance(e, EventEntry)]
    return [event for event in events if isinstance(event, kind)]


async def test_a_record_stands_for_the_cheaper_models_turns_and_never_changes(make_harness):
    harness, cheap, session = handed_over(make_harness)
    await session.run("do the task")
    kept = [e.message for e in harness.deps.journal.active_path() if isinstance(e, MessageEntry)]
    (event,) = journaled(harness, Handoff)
    assert event.record_rule == handover_record.RULE  # the journal says what System 2 is sent of these turns

    await session.run("next")

    messages, said, records, results = sent_to_system2(harness)
    assert said == ["built", "all done"]  # its own turn, then the reply the user read; the turns between are the record
    assert len(records) == 1 and len(results) == 1
    record = records[0]
    assert record.startswith("[Record of turns 2 to 3]")
    assert "give the reason when a turn states it" in record and "the reason was not recorded" in record
    assert "Turn 2. fixing\n  - ran: mkdir .cache; echo x > .cache/y;" in record
    assert "    exit code 0, 1 line of output:\n    12 passed\n" in record
    assert "on disk: created b.txt (2 lines)" in record  # found on disk: the shell wrote it, no file tool did
    assert "Turn 3.\n  - wrote c.txt (1 line)" in record
    assert record.endswith("Files these turns created or changed: b.txt, c.txt.")
    assert ".cache" not in record.split("Turn 3.")[1]  # hidden folders are not watched
    order = [type(m).__name__ for m in messages[-4:]]
    assert order == ["ToolResultMessage", "UserMessage", "AssistantMessage", "UserMessage"]
    # The journal keeps every turn, and the cheaper model was sent its own in full.
    after = [e.message for e in harness.deps.journal.active_path() if isinstance(e, MessageEntry)]
    assert after[: len(kept)] == kept
    assert [m.text for m in kept if isinstance(m, AssistantMessage)] == ["built", "fixing", "", "all done"]
    assert any(
        isinstance(m, ToolResultMessage) and "12 passed" in m.result.content for m in cheap.requests[-1].messages
    )
    assert not any("[Record" in m.content for m in cheap.requests[-1].messages if isinstance(m, UserMessage))

    await session.run("and again")

    assert sent_to_system2(harness)[2] == [record]  # the same bytes at every later message


async def test_the_record_keeps_what_was_run_and_printed_and_leaves_out_what_is_on_disk(make_harness):
    harness, _, session = handed_over(make_harness, SCRIPT)
    await session.run("do the task")
    await session.run("next")

    turn = sent_to_system2(harness)[2][0].split("Turn 2. fixing\n")[1].split("Turn 3.")[0]
    # What the here-document wrote into a file is on disk; the script fed to a program is what was run.
    assert "    cat > notes.txt <<'EOF'\n    [2 lines written to the file]\n    EOF\n    python3 - <<'EOF'" in turn
    assert "second line of the file" not in turn and '    print("row", n)' in turn
    # A long output keeps its start and its end, and says how much is gone between.
    assert "exit code 0, 50 lines of output:\n    row 1\n" in turn
    assert "    row 10\n    [20 lines left out]\n    row 31\n" in turn
    assert "    row 50\n  - on disk: created notes.txt (2 lines)" in turn


async def test_a_hand_over_journaled_before_the_record_or_under_another_rule_is_sent_in_full(make_harness, monkeypatch):
    harness, _, session = handed_over(make_harness)
    await session.run("do the task")
    journaled(harness, Handoff)[0].record_rule = None  # as a session journaled before there was a record has it
    await session.run("next")

    _, said, records, results = sent_to_system2(harness)
    assert said == ["built", "fixing", "", "all done"] and records == [] and len(results) == 3

    harness, _, session = handed_over(make_harness)
    await session.run("do the task")
    monkeypatch.setattr(handover_record, "RULE", handover_record.RULE + 1)  # a later build, with another rule
    await session.run("next")

    _, said, records, results = sent_to_system2(harness)
    assert said == ["built", "fixing", "", "all done"] and records == [] and len(results) == 3


async def test_a_compaction_through_the_turns_leaves_a_record_of_those_still_held(make_harness):
    harness, _, session = handed_over(make_harness)
    await session.run("do the task")
    journal = harness.deps.journal
    turns = [
        e for e in journal.active_path() if isinstance(e, MessageEntry) and isinstance(e.message, AssistantMessage)
    ]
    result_of_turn_2 = next(
        e for e in journal.active_path() if isinstance(e, MessageEntry) and e.parent_id == turns[1].id
    )

    # A State written while the cheaper model worked covers its first turn only: the record is not yet covered.
    journal.append(StateEntry(text="## Plan\n- fix", covers_through_id=result_of_turn_2.id, tokens_before=1))
    path = journal.active_path()
    view = build_view(path, harness.deps.instructions, TokenMeter(), 100_000)
    handover_record.apply(view, path, harness.deps.cwd)
    record, reply = view.history[view.covered :][:2]
    assert isinstance(record, UserMessage) and record.content.startswith("[Record of turns 2 to 3]")
    assert isinstance(reply, AssistantMessage) and reply.text == "all done"

    # A compaction that kept the turns from the cheaper model's second on.
    journal.append(
        CompactionEntry(summary="## Plan\n- fix", first_kept_id=turns[2].id, tokens_before=1, method="state")
    )
    await session.run("next")

    _, said, records, results = sent_to_system2(harness)
    assert said == ["all done"] and results == []  # no call is sent without its result, and no result without its call
    assert len(records) == 1 and "[Record of turn 3]" in records[0] and "Turn 2" not in records[0]
    assert "the reason was not recorded" in records[0]  # what is left of the record still says what it is
