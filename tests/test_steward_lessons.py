import json
from pathlib import Path

from intern.memory.steward.writer import Draft, Past, ReplayWriter, learn


def answer(folder: Path, lessons: list[dict]) -> ReplayWriter:
    (folder / "k.json").write_text(json.dumps({"completion": json.dumps({"lessons": lessons})}))
    return ReplayWriter(folder)


def past(n: int, outcome: str) -> Past:
    lines = [Draft("work/s", f"s{n}#t{j}", f"session {n} line {j}") for j in (1, 2)]
    return Past(f"s{n}", f"session {n}", outcome, lines)


SESSIONS = [past(1, "succeeded"), past(2, "partial"), past(3, "succeeded")]


def lesson(steps: list[str], kind: str = "how") -> dict:
    return {"kind": kind, "skill": "Purchase requests", "when": "filing a purchase request", "steps": steps,
            "text": "Read the request back with get_request and set the team before submitting."}  # fmt: skip


async def test_a_lesson_citing_whole_sessions_cites_every_line_of_their_stories(tmp_path):
    learned, dropped = await learn(answer(tmp_path, [lesson(["w1", "w3"])]), SESSIONS, [], "k")
    assert not dropped
    assert learned[0].sessions == ["s1", "s3"]
    assert learned[0].pointers == ["s1#t1", "s1#t2", "s3#t1", "s3#t2"]
    assert learned[0].succeeded == 2


async def test_a_line_and_its_session_are_cited_once(tmp_path):
    learned, _ = await learn(answer(tmp_path, [lesson(["w1.2", "w1", "w2.1"])]), SESSIONS, [], "k")
    assert learned[0].pointers == ["s1#t2", "s1#t1", "s2#t1"]


async def test_a_lesson_dropped_before_the_gate_says_why(tmp_path):
    lessons = [lesson(["w1", "w9"]), lesson(["w2.1", "w2.2"]), lesson(["w2.1", "w2"]), lesson(["w1", "w2"], "tip")]
    learned, dropped = await learn(answer(tmp_path, lessons), SESSIONS, [], "k")
    assert not learned
    assert [d.reason for d in dropped] == [
        "it cites 1 session(s), not 2; unknown steps: w9",
        "it cites 1 session(s), not 2",
        "it cites 1 session(s), not 2",
        "its kind 'tip' is not one of how, fails, rule",
    ]
    assert dropped[0].pointer == "s1#t2"
    assert dropped[0].text.startswith("Read the request back")


async def test_a_how_lesson_from_sessions_that_did_not_succeed_is_dropped(tmp_path):
    sessions = [past(1, "partial"), past(2, "failed")]
    learned, dropped = await learn(
        answer(tmp_path, [lesson(["w1", "w2"]), lesson(["w1", "w2"], "fails")]), sessions, [], "k"
    )
    assert [lesson.kind for lesson in learned] == ["fails"]
    assert dropped[0].reason == "a how lesson cites no succeeded session"
