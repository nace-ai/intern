"""The look: the mark, the masthead, and System 1's cards."""

from __future__ import annotations

import json
import re

from textual.content import Content

from intern.events import Compaction, MessageEvent, Nudge, ProcessVerdict, ToolCallStart, ToolGuard
from intern.locate.trace import Ranked
from intern.messages import UserMessage
from intern.tui.ledger import CompactionJudgement, GuardJudgement, KeepVerdict, Ledger, RuleRefusal, TurnJudgement
from intern.tui.system1_panel import feed, shares, usage
from intern.tui.theme import CARD, MASCOT_BODY, MASCOT_COLOURS, S_BOX, S_BOX_MUTED, S_OK, S_RULE, S_WARN
from intern.tui.widgets import (
    CARD_LINES,
    FILLED,
    LANDED,
    MASCOT,
    METER_WIDTH,
    NBSP,
    TRACK,
    StatusBar,
    compaction_card,
    cost_text,
    ended_lines,
    guard_card,
    level,
    look_card,
    mark,
    meter,
    process_result,
    rule_card,
    settled,
    supervisor_card,
    thinking_box,
    tool_header,
)

from .test_tui_logic import guard, keep, run_start, search, supervise, trace

LEVEL = re.compile(r"#([0-9a-f]{2})[0-9a-f]{4}")


def judgement(**overrides: object) -> TurnJudgement:
    fields: dict[str, object] = {
        "turn": 7,
        "mode": "advancing",
        "probabilities": {
            "advancing": 0.92,
            "re_investigating": 0.06,
            "off_plan": 0.01,
            "finished": 0.0,
            "retrying": 0.01,
        },
        "backend": "decisions-api:nace-ai/drex-max",
        "cached": False,
        "duration_ms": 565,
        "cost_usd": 0.0001,
    }
    fields.update(overrides)
    return TurnJudgement(**fields)  # type: ignore[arg-type]


def levels(style: str) -> list[int]:
    return [int(level, 16) for level in LEVEL.findall(style)]


def test_the_mark_draws_the_mascot_two_pixels_to_a_cell_in_its_own_colours_with_the_page_around_it():
    half_tones = "+k"
    # None the page by a typo.
    assert {pixel for row in MASCOT for pixel in row} <= {*MASCOT_COLOURS, *half_tones, "#", "."}
    rows = mark()
    assert len(rows) == 6  # under the masthead's first row
    named = {colour: name for name, colour in MASCOT_COLOURS.items()}
    pixel = {colour.lower(): name for colour, name in (named | dict.fromkeys(MASCOT_BODY, "#")).items()}
    pixel |= {"": ".", CARD.lower(): "."}
    drawn: list[str] = []
    for row in rows:
        styles = [""] * len(row.plain)
        for span in row.spans:
            styles[span.start : span.end] = [str(span.style)] * (span.end - span.start)
        upper = lower = ""
        for glyph, style in zip(row.plain, styles, strict=True):
            # The upper pixel is the cell's background, the lower a lower half block on it: never an upper half
            # block, which macOS Terminal starts short of the cell's top.
            assert glyph in " ▄"
            fg, on, bg = style.rpartition("on ")
            top, bottom = ((bg, fg.strip()) if on else ("", bg)) if glyph == "▄" else (bg, bg)
            upper, lower = upper + pixel.get(top.lower(), "~"), lower + pixel.get(bottom.lower(), "~")
        drawn += [upper, lower]
    # A half tone is a blend, of no colour of its own; the page is no colour at all.
    expected = [re.sub(f"[{re.escape(half_tones)}]", "~", row) for row in MASCOT]
    assert drawn == expected


def test_the_masthead_lines_up_the_models_the_run_and_each_systems_cost_in_columns():
    status = StatusBar()
    status.system2, status.system1, status.thinking = "anthropic/claude-haiku-5-5", "drex:drex-max-v1.1", "xhigh"
    status.levels = ("low", "medium", "high", "xhigh", "max")
    status.context, status.window = 233_000, 1_000_000
    status.cost2, status.cost1 = 1.25, 0.0178
    status.refresh_line()
    content = status.content
    assert isinstance(content, Content)
    rows = content.plain.splitlines()
    assert len(rows) == 7 and "INTERN" in rows[1]
    sys2, sys1 = rows[3], rows[4]
    assert sys2.index("System 2") == sys1.index("System 1")
    assert sys2.index("anthropic/") == sys1.index("drex-max-v1.1")
    assert sys2.index(" thinking xhigh") == sys1.index("console")
    assert sys2.index("$1.2500") == sys1.index("$0.0178")  # each system's cost on its row, in a column
    assert sys2.rstrip().endswith("$1.2500   ○ 23% of 1.0M")  # the run after it: the context as a share


def test_the_thinking_box_is_filled_up_to_the_level_and_as_wide_at_every_one():
    levels = ("low", "medium", "high")
    boxes = ["".join(p if isinstance(p, str) else p[0] for p in thinking_box(each, levels)) for each in levels]
    assert boxes == [" thinking low    ▰▱▱ ", " thinking medium ▰▰▱ ", " thinking high   ▰▰▰ "]
    assert {part[1] for part in thinking_box("low", levels) if isinstance(part, tuple)} == {S_BOX, S_BOX_MUTED}
    assert "".join(p[0] for p in thinking_box("default", levels) if isinstance(p, tuple)).endswith("default ▱▱▱ ")


def test_a_systems_cost_says_when_its_provider_reports_no_price():
    assert (cost_text(0.0), cost_text(0.01234)) == ("", "$0.0123")
    assert (cost_text(0.0, unpriced=True), cost_text(0.5, unpriced=True)) == ("price unknown", "≥ $0.5000")


def test_a_meter_is_a_fixed_width_line_filled_to_the_level():
    assert "".join(text for text, _ in meter(0.62, "x")) == FILLED * 6 + TRACK * 4
    assert meter(0.0, "x") == [(TRACK * METER_WIDTH, S_RULE)]
    assert meter(1.0, "x") == [(FILLED * METER_WIDTH, "x")]


def card_lines(content: Content) -> list[str]:
    """A card's text with its non-breaking spaces (inside a labelled level) read as spaces."""
    return content.plain.replace(NBSP, " ").splitlines()


def test_a_level_binds_its_label_and_number_with_non_breaking_spaces():
    text = Content.assemble(*level("rewrites shared history", 0.96, "x")).plain
    assert " " not in text and text.replace(NBSP, " ") == "rewrites shared history 0.96"


def test_the_supervisor_card_is_the_panel_row_in_one_line():
    (line,) = card_lines(supervisor_card(judgement(strikes=1)))
    # the call, then the two next likeliest; finished (0.00) is not among them
    assert line == "System 1 · 0.6s   moving 0.92   circling 0.06   off-plan 0.01   → strike 1/2"
    (cached,) = card_lines(supervisor_card(judgement(cached=True)))
    assert cached.startswith("System 1 · 0.6s · cached   moving") and "→" not in cached  # nothing followed


def test_the_guard_card_says_what_the_call_would_do_and_why_it_was_refused():
    g = GuardJudgement(
        turn=3,
        subject='bash {"command": "git push --force"}',
        kind="rewrites_shared_history",
        probabilities={"routine": 0.02, "rewrites_shared_history": 0.96, "unclear": 0.02},
        asked=0.02,
        backend="decisions-api:nace-ai/drex-max",
        cached=False,
        duration_ms=380,
        cost_usd=0.00004,
    )
    (line,) = card_lines(guard_card(g))
    assert line == "✓ System 1   rewrites shared history 0.96   risk 0.96   asked 0.02   0.4s"
    g.refused, g.reason = True, "System 1 judged that it rewrites history that others share and the user did not ask"
    lines = card_lines(guard_card(g))
    assert lines[0].startswith("✗ System 1 refused   ") and lines[1] == g.reason
    rule = rule_card(RuleRefusal(turn=4, call_id="c4", name="bash", rule="force_push", reason="a force push"))
    assert rule.plain == "✗ refused   rule · force push\na force push"


def test_a_compaction_card_lands_each_read_as_judged_then_settles_with_the_kept_reads_first():
    c = CompactionJudgement(turn=9)
    c.verdicts = [KeepVerdict(f"src/m{n}.py (lines 1-30)", "done_with", {"working_on": 0.05}, False) for n in range(10)]
    c.verdicts.append(KeepVerdict("src/kept.py (lines 1-30)", "working_on", {"working_on": 0.9}, False))  # judged last
    # Live: a line shows once it has started landing, the newest still being judged.
    ages = {v.path: LANDED for v in c.verdicts[:3]} | {c.verdicts[3].path: 0}
    live = compaction_card(c, ages=ages).plain
    assert "m2.py" in live and "m3.py" in live and "m4.py" not in live and "judging" in live
    assert not settled(c, ages)
    # The cut made and every line landed: the kept read first though judged last, the rest behind a click.
    c.method, c.turns_compacted, c.tokens_before, c.tokens_after = "state", 6, 14_000, 6_000
    ages = {v.path: LANDED for v in c.verdicts}
    assert settled(c, ages)
    lines = compaction_card(c, ages=ages).plain.splitlines()
    reads = [line for line in lines if ".py" in line]
    assert "kept.py" in reads[0] and len(reads) == CARD_LINES and "3 more" in lines[-1]
    assert sum(".py" in line for line in compaction_card(c, expanded=True).plain.splitlines()) == len(c.verdicts)


def test_the_system_1_panel_lists_every_judgement_under_its_prompt_each_kind_in_its_own_words():
    ledger = Ledger()
    ledger.observe(run_start("r1"))
    ledger.observe(MessageEvent(entry_id="e1", message=UserMessage(content="Fix the parser")))
    for turn in (4, 5, 9):
        ledger.observe(supervise(turn, "retrying"))
        if turn == 5:
            ledger.observe(Nudge(turn=5, reason="stuck_gate", text="A review of your last 5 turns found no progress"))
    ledger.observe(guard(4, 'bash {"command": "pytest -q"}', "routine", 0.9))
    ledger.observe(ToolCallStart(turn=4, call_id="c4", name="bash", arguments={"command": "sudo make install"}))
    ledger.observe(ToolGuard(turn=4, call_id="c4", name="bash", layer="rule", rule="privilege", reason="it runs sudo"))
    ledger.observe(guard(5, 'bash {"command": "git push --force"}', "rewrites_shared_history", 0.02))
    refused = "System 1 judged that it rewrites history and that the user did not ask for it"
    ledger.observe(ToolGuard(turn=5, call_id="c5", name="bash", layer="system1", rule="r", reason=refused))
    search(ledger, "s1", "decision_code", "where are retries capped?")
    found = [Ranked(path="src/retry.py", score=0.93, where="def backoff")]
    ledger.observe(trace("code-search", "a1", "where are retries capped?", asks=12, found=found))
    search(ledger, "s2", "decision_files", "what did the theft cost?")
    ledger.observe(keep(6, "src/a.py", "working_on"))
    ledger.observe(keep(6, "src/b.py", "done_with"))
    cut = Compaction(turn=6, method="state", tokens_before=14_000, tokens_after=6_000, turns_compacted=6, summary="")
    ledger.observe(cut.model_copy(update={"kept": 1}))
    text = feed(ledger.runs, 60).plain
    rows = [line for line in text.splitlines()[1:] if line[:1] != " " or line.startswith("  " + FILLED)]
    # A row a judgement, in the order they came. A watched turn starts with its bar and the gutter before it is
    # empty; a guarded call's sign is how it went, the one thing in the gutter that is coloured.
    assert text.splitlines()[0] == "› Fix the parser"
    assert "".join(row[0] for row in rows) == "   ✓✗✗⌕⌕⟲"
    # The watch, in a line: what System 1 weighed as one bar, each answer its share of the cells, so a call made at
    # 0.80 is seen to have been one of three; then its call, what it weighed most against it as far as the line holds
    # them, and what the loop did. Each in a word short enough for the line, not the name System 1 is asked with.
    assert rows[0] == "  " + FILLED * 12 + " retrying 0.80  moving 0.05  strike 1/2"
    assert "strike 2/2" in rows[1] and "stuck nudge sent" in text and "strike 1/2" in rows[2]
    weighed = shares({"retrying": 0.8, "advancing": 0.05, "re_investigating": 0.05})
    # Of twelve cells: the 0.90 it gave answers is eleven of them, and the cell left is a hairline.
    assert [(len(cells), style) for cells, style in weighed] == [(10, S_WARN), (1, S_OK), (1, S_RULE)]
    assert shares({"advancing": 0.5}, 4) == [(FILLED * 2, S_OK), (TRACK * 2, S_RULE)]
    # The guard: the call, allowed or refused, and who refused it and why. One allowed as routine is a line, its
    # risk at the row's end; what it would do and whether it was asked for are a refused or risky call's.
    assert rows[3].startswith("✓ $ pytest -q ") and rows[3].endswith(" risk 0.00") and "routine" not in text
    assert len(rows[3]) == 60 and "✗ $ sudo make install" in rows[4]
    assert "✗ $ git push --force" in rows[5]
    assert "a fixed rule, not System 1 · privilege" in text and "it runs sudo" in text
    assert "rewrites shared history 0.96" in text and "System 1 judged that it rewrites history" in text
    # A search: the question, what it took and the place it ranked first; one still running says so.
    assert "decision_code  12 asks · 36 judged" in rows[6] and "where are retries capped?" in text
    assert "→ src/retry.py  0.93" in text and "def backoff" in text
    assert "decision_files  searching…" in rows[7]
    # A compaction: the reads judged, the ones kept, and what the cut did to the context.
    assert "2 reads judged · 1 kept · 1 cut" in rows[8] and "14.0k → 6.0k −57%" in text and "✓ src/a.py" in text
    # Above the feed, what the asks came to, by kind: the feed's legend.
    said = usage(ledger.usage, 60).plain
    assert said.startswith("7 asks · ") and "━ watch 3" in said and "✓ guard 2" in said and "⟲ compaction 2" in said


def test_a_background_commands_lines_say_system_1s_judgement_and_how_it_stands_not_the_command_again():
    command = "cd /srv/app && python3 - <<'EOF'\nimport build\nbuild.everything()\nEOF"
    waits = {"waiting_as_expected": 0.93, "progressing": 0.05, "stalled": 0.02}
    looked = ProcessVerdict(
        id="j1", command=command, elapsed_s=120, verdict="waiting_as_expected", probabilities=waits, told=False
    )
    alone = look_card(looked, named=False).plain.replace(NBSP, " ")
    assert alone == "» System 1 · at 2m 00s   waiting as expected 0.93   progressing 0.05"  # the judgement alone
    stalled = looked.model_copy(update={"verdict": "stalled", "probabilities": {"stalled": 0.7}, "told": True})
    # With several running, which one: what it runs, in a few words, not the `cd` before it nor its later lines.
    named = look_card(stalled, named=True).plain.replace(NBSP, " ")
    assert named == "» System 1 · python3 - <<'EOF' … at 2m 00s   stalled 0.70   → told the agent"

    # A poll reads as how the job stands and what it printed, not the JSON the agent gets.
    snapshot = {
        "handle": "1cf1caa90bbb40ad",
        "command": command,
        "state": "running",
        "exit_code": None,
        "elapsed_s": 32,
    }
    polled = json.dumps({**snapshot, "output": "12 passed\n", "cursor": 0, "next_cursor": 10, "omitted_bytes": 0})
    assert tool_header("process", {"action": "poll", "handle": "1cf1caa90bbb40ad"}) == "process poll 1cf1caa9"
    assert process_result(polled) == ("running · 32s", "12 passed\n")
    refused = "Error: no job has that handle\nNext: Poll/list an owned handle."
    assert process_result(refused) == ("", refused)  # an error is the tool's own words

    # Its end, from the notice the agent gets: the command is over several lines there, and the output is quoted.
    notice = (
        f"Background job 1cf1caa9 (`{command}`) ended: exited, exit code 0, after 3m 02s.\nThe end of its output:\nok"
    )
    assert ended_lines(notice) == ["`python3 - <<'EOF' …` ended: exited, exit code 0, after 3m 02s"]
