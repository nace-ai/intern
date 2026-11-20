"""System 1 watches what runs: while commands run, it is asked on a schedule what each is doing, one ask for
all of them and none while nothing runs, and what it finds stalled, failing or waited on reaches the agent: at the
next turn, or between runs as a run of its own. Nothing here stops or moves a command."""

from __future__ import annotations

import asyncio

import pytest

from intern.decisions import Answers, ChoiceAnswer, ChoiceQuestion, Question
from intern.events import Nudge, ProcessCheck, RunStart
from intern.messages import UserMessage
from intern.process_watch import is_plain_sleep
from intern.providers.fake import Turn
from intern.session import JobNotices, Session

from .support import Harness, Quiet
from .test_job_notices import _call, _scripted, _session

SCHEDULE = (0.3, 0.6, 1.2)


class Sees(Quiet):
    """A System 1 that finds every running command `verdict`, and is quiet about the rest; `down` fails the looks."""

    def __init__(self, verdict: str) -> None:
        super().__init__()
        self.verdict, self.down = verdict, False

    async def decide(self, state: str, questions: dict[str, Question]) -> Answers:
        looks = [n for n, q in questions.items() if isinstance(q, ChoiceQuestion) and "stalled" in q.criteria]
        if looks and self.down:
            raise RuntimeError("backend down")
        answers = await super().decide(state, questions)
        for name in looks:
            answers.answers[name] = ChoiceAnswer(choice=self.verdict, probabilities={self.verdict: 0.9, "unclear": 0.1})
        return answers


def _watched(harness: Harness, notices: JobNotices = "quiet") -> Session:
    session, _ = _session(harness, notices)
    assert session._watch is not None  # pyright: ignore[reportPrivateUsage]
    session._watch.schedule, session._watch.every = SCHEDULE, 30  # pyright: ignore[reportPrivateUsage]
    return session


def _told(harness: Harness) -> list[str]:
    return [n.text for n in harness.recorder.of(Nudge) if n.reason == "process_check"]


@pytest.mark.parametrize(
    ("command", "plain"),
    [
        ("sleep 1200", True),
        ("sleep 20m", True),
        ("  sleep 1.5h ", True),
        ("sleep 1m 30s", True),
        ("sleep 0", True),
        ("sleep\t.5d 1.", True),
        ("sleep 5 && make", False),
        ("sleep $N", False),
        ("sleep infinity", False),
        ("timeout 5 sleep 10", False),
        ("sleep 10; echo done", False),
        ("sleep 10 > /dev/null", False),
        ("sleep 10 | cat", False),
        ("sleep $(echo 10)", False),
        ("N=10 sleep 10", False),
        ("sleep -1", False),
        ("sleep", False),
        ("sleep 1\n30", False),
        ("sleep\n30", False),
        ("sleep 1e3", False),
    ],
)
def test_plain_sleep_classifier(command: str, plain: bool):
    assert is_plain_sleep(command) is plain


async def test_only_a_plain_sleep_job_makes_no_asks_across_scheduled_looks(make_harness):
    harness = make_harness([], system1=(system1 := Sees("stalled")))
    session = _watched(harness)
    jobs = session.deps.processes
    assert jobs is not None
    try:
        await jobs.start("sleep 600", cwd=harness.cwd)
        await asyncio.sleep(1.4)  # beyond all three scheduled looks
        [status] = session.jobs_status()
        assert status.command == "sleep 600" and status.verdict is None and status.next_look_s is None
        assert system1.asks == []
        assert harness.recorder.of(ProcessCheck) == [] and _told(harness) == []
    finally:
        await session.aclose()
        await jobs.aclose()


async def test_a_plain_sleep_beside_another_job_is_absent_from_asks_and_verdicts(make_harness):
    harness = make_harness([], system1=(system1 := Sees("progressing")))
    session = _watched(harness)
    jobs = session.deps.processes
    assert jobs is not None
    try:
        await jobs.start("sleep 600", cwd=harness.cwd)
        await asyncio.sleep(0.05)  # the watch is idle; another command must wake it
        other = await jobs.start("sleep 5 && echo built", cwd=harness.cwd)
        await asyncio.sleep(0.8)
        checks = harness.recorder.of(ProcessCheck)
        assert len(checks) >= 2
        assert all([v.id for v in check.verdicts] == [other.handle] for check in checks)
        assert system1.asks
        for state, questions in system1.asks:
            assert "sleep 600" not in state and other.command in state
            assert len(questions) == 1
            assert all("sleep 600" not in q.instructions for q in questions.values())
    finally:
        await session.aclose()
        await jobs.aclose()


@pytest.mark.parametrize("notices", ["quiet", "wake"])
async def test_a_skipped_sleep_end_still_reaches_the_agent(make_harness, notices: JobNotices):
    harness = make_harness([], system1=Sees("stalled"))
    session = _watched(harness, notices)
    jobs = session.deps.processes
    assert jobs is not None
    try:
        job = await jobs.start("sleep 0.8", cwd=harness.cwd)
        for _ in range(60):
            if not jobs.job_running(job.handle) and (notices == "quiet" or harness.recorder.of(RunStart)):
                break
            await asyncio.sleep(0.05)
        if notices == "quiet":
            assert harness.recorder.of(RunStart) == []
            await session.run("anything new?")
        else:
            await session.wait()
        ended = [n.text for n in harness.recorder.of(Nudge) if n.reason == "process_ended"]
        assert len(ended) == 1 and job.handle in ended[0] and "exit code 0" in ended[0]
        assert harness.recorder.of(ProcessCheck) == []
    finally:
        await session.aclose()
        await jobs.aclose()


async def test_looks_come_on_the_schedule_one_ask_for_all_and_none_once_nothing_runs(make_harness):
    harness = make_harness([], system1=(system1 := Sees("progressing")))
    session = _watched(harness)
    jobs = session.deps.processes
    assert jobs is not None
    try:
        await jobs.start("sleep 1.6 && echo built", cwd=harness.cwd)
        await jobs.start("sleep 1.6; echo done", cwd=harness.cwd)
        await asyncio.sleep(2.6)
    finally:
        await session.aclose()
        await jobs.aclose()
    checks = harness.recorder.of(ProcessCheck)
    # At 0.3, 0.6 and 1.2 s of age, each look over both jobs; after they ended at 1.6 s, none.
    assert [len(c.verdicts) for c in checks] == [2, 2, 2]
    assert all(v.verdict == "progressing" and not v.told for c in checks for v in c.verdicts)
    assert 1 <= len([q for _, q in system1.asks if "p0" in q]) <= 3, "a look the same as the last is the cache's"
    assert _told(harness) == [], "progressing tells the agent nothing"


async def test_every_running_command_gets_its_look_when_more_run_than_one_ask_holds(make_harness):
    harness = make_harness([], system1=Sees("progressing"))
    session = _watched(harness)
    jobs = session.deps.processes
    assert jobs is not None
    calls = [f"call-{n}" for n in range(11)]  # more than System 1 reads in one ask
    try:
        for call in calls:
            jobs.begin_call(call, f"make {call}")
        await asyncio.sleep(0.5)  # the first look, at 0.3 s
    finally:
        for call in calls:
            jobs.end_call(call, "exited", 0)
        await session.aclose()
        await jobs.aclose()
    looked_at = {v.id for check in harness.recorder.of(ProcessCheck) for v in check.verdicts}
    assert looked_at == set(calls), "the newest commands are judged too, not only counted as looked at"


async def test_a_command_found_stalled_is_told_at_the_next_turn_the_latest_look_only(make_harness):
    harness = make_harness(
        _scripted(
            _call("process", action="start", command="echo compiling; sleep 30"), _call("bash", command="sleep 1.5")
        ),
        system1=Sees("stalled"),
    )
    session = _watched(harness)
    try:
        assert (await session.run("build it")).reason == "completed"
    finally:
        await session.aclose()
        assert session.deps.processes is not None
        await session.deps.processes.aclose()
    assert len(harness.recorder.of(ProcessCheck)) >= 2, "several looks during the 1.5 s call"
    [told] = _told(harness)
    assert "background job" in told and "looks stalled" in told and "Its last line: compiling" in told
    # It reached the agent before its next turn.
    assert any(isinstance(m, UserMessage) and told in m.content for m in harness.provider.requests[2].messages)


async def test_between_runs_a_stalled_command_starts_a_run_that_tells_the_agent(make_harness):
    harness = make_harness(
        _scripted(_call("process", action="start", command="sleep 30; echo done"), lambda r: Turn(text="started")),
        system1=Sees("stalled"),
    )
    session = _watched(harness, "wake")
    jobs = session.deps.processes
    assert jobs is not None
    try:
        await session.run("start it")
        for _ in range(60):
            if len(harness.recorder.of(RunStart)) == 2 and not session.running:
                break
            await asyncio.sleep(0.05)
        assert len(harness.recorder.of(RunStart)) == 2 and _told(harness)
        assert harness.recorder.of(ProcessCheck)[0].turn is None, "looked at between runs"
    finally:
        await session.aclose()
        await jobs.aclose()


async def test_a_look_system1_cannot_answer_during_a_run_stops_it(make_harness):
    harness = make_harness(
        _scripted(_call("process", action="start", command="sleep 30; echo done"), _call("bash", command="sleep 1")),
        system1=(system1 := Sees("progressing")),
    )
    system1.down = True
    harness.deps.decisions._retry_window_s = 0.1  # pyright: ignore[reportPrivateUsage]
    session = _watched(harness)
    try:
        outcome = await session.run("start it")
    finally:
        await session.aclose()
        assert session.deps.processes is not None
        await session.deps.processes.aclose()
    assert outcome.reason == "system1_unavailable" and outcome.turns == 2
