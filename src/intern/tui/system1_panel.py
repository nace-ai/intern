"""The System 1 panel: every judgement System 1 made in the session, as a feed, under what its asks came to.

System 1 is asked for different things, and each reads its own way (`ledger.py` gathers them from the events):

- `✦` the thinking level it picked for a run, at `auto`, first under the run's prompt: a column for each level the
  model takes, as tall as its probability, the one picked in the accent; then the level, how sure, and the next
  likeliest;
- a turn it watched, in a line: what it weighed as one bar, each answer its share of it; its call and the
  probability; then what the loop did about it (a stuck strike, a nudge) and, once the request looks met, the
  wrap-up verdict;
- `✓` or `✗` a tool call the guard asked about, allowed or refused. One it found routine is a line, the call and
  P(any risk); any other also says what it would do, P(any risk) and P(the user asked for it), and the reason when
  refused. A fixed rule's refusal is listed too, as the rule it is: no model was asked;
- `⌕` a search, `decision_code` or `decision_files`: the question, the asks the walk took and the cards it judged,
  and the places it ranked first;
- `⟲` a compaction: the reads being cut that System 1 judged, how many it kept and which, and what the cut did to
  the context;
- `»` a command that went on running and each look at it; `·` whatever else was asked for a turn.

Above the feed, the session's asks: how many System 1 answered, how long one took, how many the cache answered, and
the count by kind, each behind the sign its rows start with. The signs are muted, and only a guard's and the thinking
pick's are coloured: what stands out in the feed is how a judgement went, and the level the run goes with. A run
starts with its prompt; the feed follows its end as the session grows.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from rich.cells import cell_len
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.content import Content
from textual.widgets import Static

from intern.extensions.guard import ASKED_THRESHOLD, RISK_THRESHOLD
from intern.tui.ledger import (
    KINDS,
    CompactionJudgement,
    EffortJudgement,
    GuardJudgement,
    HandoverJudgement,
    Kind,
    Ledger,
    OtherJudgement,
    Row,
    RuleRefusal,
    RunLedger,
    SearchJudgement,
    TurnJudgement,
    Usage,
)
from intern.tui.theme import (
    S_BAD,
    S_BAD_BOLD,
    S_BRAND_BOLD,
    S_HEAD,
    S_INK,
    S_MUTED,
    S_MUTED_ITALIC,
    S_OK,
    S_RULE,
    S_TAB,
    S_WARN,
    S_WARN_BOLD,
)
from intern.tui.widgets import (
    AUTO_SIGN,
    CD,
    FILLED,
    HANDOVER_SIGN,
    JOB,
    MODE_STYLE,
    TRACK,
    Following,
    Part,
    brief,
    clip,
    effort_runners_up,
    fit_path,
    flow,
    ladder,
    level,
    mode_label,
    model_name,
    readiness,
    runners_up,
    short_path,
    shrink,
    stacked,
    tokens_text,
    tool_header,
    turn_notes,
)

SIGN: dict[Kind, str] = {
    "thinking": AUTO_SIGN,
    "hand-over": HANDOVER_SIGN,
    "watch": FILLED,
    "guard": "✓",
    "search": "⌕",
    "compaction": "⟲",
    "jobs": JOB,
    "memory": "◇",
    "other": "·",
}
"""What a kind's rows start with, as the count by kind shows it. A watched turn starts with its bar and a guarded
call with its tick or cross, so neither has a sign of its own in the gutter."""
GUTTER = 2
"""A row's sign: `⌕ `; empty before a watched turn, whose bar says what it is."""
BAR = 12
"""A watched turn's bar, in cells: what System 1 weighed, each answer its share of them."""
FEED_ROWS = 400
"""The newest lines drawn; a long session's older ones are in its journal."""
MIN_WIDTH = 30
QUESTION_LINES = 2
KEPT_SHOWN = 3
EMPTY = (
    "System 1's judgements show here as it makes them: the turns it watches, the calls it guards, the searches it "
    "walks and the reads it sorts at a compaction."
)


def seconds(ms: int) -> str:
    return f"{ms / 1000:.1f}s"


# -- what the asks came to ----------------------------------------------------------------------------------


def usage(used: Usage, width: int) -> Content:
    """The session's asks in a line (answered, the time one took, cached, failed), then the count by kind, each
    with the sign its rows start with."""
    if not used.total and not used.cached and not used.failed:
        return Content("")
    asks = used.total
    head: list[Part] = [(f"{asks:,}", S_HEAD), (f" ask{'s' if asks != 1 else ''}", S_MUTED)]
    if asks:
        head.append((f" · {seconds(used.duration_ms // asks)} each", S_MUTED))
    if used.cached:
        head.append((f" · {used.cached:,} cached", S_MUTED))
    if used.failed:
        head += [(" · ", S_MUTED), (f"{used.failed:,} failed and retried", S_WARN)]
    kinds: list[list[Part]] = [
        [(SIGN[kind], S_MUTED), (f" {kind} ", S_MUTED), (f"{used.asks[kind]:,}", S_INK)]
        for kind in KINDS
        if used.asks[kind]
    ]
    return stacked([head, *flow(kinds, width)])


# -- a row, by what was judged ------------------------------------------------------------------------------


def wrapped(text: str, room: int, style: str, lines: int = 0) -> list[list[Part]]:
    """`text` in lines of `room` cells, `lines` of them at most (0: all), the last ending `…` when it was cut."""
    out = textwrap.wrap(" ".join(text.split()), max(8, room)) or [""]
    if lines and len(out) > lines:
        out = [*out[: lines - 1], clip(out[lines - 1] + " …", max(8, room))]
    return [[(line, style)] for line in out]


def with_notes(head: list[Part], notes: list[Part], room: int) -> list[list[Part]]:
    """A row's first line with its notes after it, those that do not fit on lines of their own."""
    lines, used = [head], Content.assemble(*head).cell_length
    for note in notes:
        size = cell_len(note[0] if isinstance(note, tuple) else note)
        if lines[-1] and used + 2 + size <= room:
            lines[-1] += ["  ", note]
            used += 2 + size
        else:
            lines.append([note])
            used = size
    return lines


def shares(probabilities: dict[str, float], width: int = BAR) -> list[Part]:
    """What System 1 weighed as one bar of `width` cells: each answer its share of them, likeliest first, in its own
    colour (`MODE_STYLE`); a hairline for what it gave no answer. A share under half a cell does not show."""
    ranked = sorted(probabilities.items(), key=lambda item: -item[1])
    cells = [int(p * width) for _, p in ranked]
    spare = max(0, min(width, round(sum(probabilities.values()) * width)) - sum(cells))
    # The cells left over go to the answers nearest to one more.
    for n in sorted(range(len(ranked)), key=lambda n: cells[n] - ranked[n][1] * width)[:spare]:
        cells[n] += 1
    bar: list[Part] = [
        (FILLED * size, MODE_STYLE.get(mode, S_MUTED)) for (mode, _), size in zip(ranked, cells, strict=True) if size
    ]
    return [*bar, (TRACK * (width - sum(cells)), S_RULE)] if sum(cells) < width else bar


def watch_lines(j: TurnJudgement, room: int) -> list[list[Part]]:
    """A turn System 1 watched, in a line: what it weighed as a bar, its call and how sure, what it weighed most
    against it as far as the line holds them (the bar has them all), then what the loop did."""
    head: list[Part] = [
        *shares(j.probabilities),
        " ",
        (mode_label(j.mode), MODE_STYLE.get(j.mode, S_INK)),
        (f" {j.confidence:.2f}", S_INK),
    ]
    notes = turn_notes(j)
    noted = sum(2 + cell_len(note[0] if isinstance(note, tuple) else note) for note in notes)
    spare = room - Content.assemble(*head).cell_length - noted
    for mode in runners_up(j.probabilities, j.mode):
        other = f"  {mode_label(mode)} {j.probabilities[mode]:.2f}"
        if len(other) <= spare:
            head.append((other, S_MUTED))
            spare -= len(other)
    return with_notes(head, notes, room)


def effort_lines(e: EffortJudgement, room: int) -> list[list[Part]]:
    """The run's thinking level in a line: the ladder of what System 1 weighed, the level in the accent and how sure,
    then the next likeliest as far as the line holds them."""
    head: list[Part] = [("thinking ", S_MUTED), *ladder(e), " ", *level(e.level, e.probability, S_BRAND_BOLD)]
    if e.cached:
        head.append(("  cached", S_MUTED))
    for part in effort_runners_up(e):
        if Content.assemble(*head, part).cell_length > room:
            break
        head.append(part)
    return [head]


def subject_title(subject: str) -> str:
    """The guard's subject (`bash {"command": "git push"}`, its JSON cut at 120 characters) as the call reads in
    the transcript, without the `cd … &&` an agent puts before a command: `$ git push`."""
    name, _, raw = subject.partition(" ")
    try:
        arguments = json.loads(raw)
    except ValueError:
        arguments = None
    if isinstance(arguments, dict):
        title = tool_header(name, arguments)
        return "$ " + CD.sub("", title[2:]) if title.startswith("$ ") else title
    _, _, value = raw.partition('": "')  # cut short: its first argument's value, as far as it goes
    text = (value or raw).replace('\\"', '"').replace("\\n", " ")
    return f"$ {CD.sub('', text)}" if name == "bash" else f"{name} {text}"


def verdict_sign(refused: bool) -> Part:
    """A guarded call's sign, the one in the gutter that is coloured: allowed or refused."""
    return ("✗", S_BAD_BOLD) if refused else ("✓", S_OK)


def call_line(title: str, room: int, right: str = "") -> list[Part]:
    """The call's first line, with `right` muted at the row's end."""
    line = title.strip().splitlines()[0] if title.strip() else ""
    if not right:
        return [(clip(line, room), S_INK)]
    line = clip(line, max(8, room - cell_len(right) - 2))
    return [(line, S_INK), " " * max(2, room - cell_len(line) - cell_len(right)), (right, S_MUTED)]


def guard_lines(g: GuardJudgement, room: int) -> list[list[Part]]:
    """A call System 1 judged. One it allowed as routine is a line, the call and P(any risk) at its end: most are,
    and a feed of them reads as a list of commands with a column of risks. Any other has under it what the call
    would do, P(any risk) and P(asked), past its threshold in red, and the reason when it was refused."""
    risky = g.risk >= RISK_THRESHOLD
    unasked = risky and g.asked < ASKED_THRESHOLD
    if g.kind == "routine" and not risky and not g.refused:
        return [call_line(subject_title(g.subject), room, f"risk {g.risk:.2f}")]
    kind_style = S_MUTED if g.kind == "routine" else S_WARN if g.kind == "unclear" else S_BAD
    facts: list[list[Part]] = [
        [(f"{g.kind.replace('_', ' ')} {g.probabilities.get(g.kind, 0.0):.2f}", kind_style)],
        [("risk ", S_MUTED), (f"{g.risk:.2f}", S_BAD_BOLD if risky else S_MUTED)],
        [("asked ", S_MUTED), (f"{g.asked:.2f}", S_BAD_BOLD if unasked else S_MUTED)],
    ]
    lines = [call_line(subject_title(g.subject), room), *flow(facts, room)]
    return lines + wrapped(g.reason, room, S_BAD) if g.refused else lines


def rule_lines(r: RuleRefusal, room: int) -> list[list[Part]]:
    """A call a fixed rule refused: no model was asked, and the row says so."""
    title = tool_header(r.name, r.arguments) if r.arguments else r.name
    said = f"a fixed rule, not System 1 · {r.rule.replace('_', ' ')}"
    return [call_line(title, room), [(clip(said, room), S_MUTED)], *wrapped(r.reason, room, S_BAD)]


def search_lines(s: SearchJudgement, room: int, cwd: Path | None) -> list[list[Part]]:
    """A search: the tool and what the walk took, the question, then the places it ranked first."""
    facts: list[str] = []
    if s.asks:
        facts += [f"{s.asks:,} asks", f"{s.judged:,} judged"]
    if s.elapsed_ms:
        facts.append(seconds(s.elapsed_ms))
    if s.status is None:
        facts.append("searching…")
    head: list[Part] = [(s.tool, S_HEAD), (clip("  " + " · ".join(facts), room - len(s.tool)), S_MUTED)]
    scope = f" in {s.scope}" if s.scope else ""
    lines = [head, *wrapped(s.question + scope, room, S_INK, QUESTION_LINES)]
    for n, found in enumerate(s.found):
        score = f"  {found.score:.2f}"
        path = fit_path(short_path(found.path, cwd), max(8, room - 2 - len(score)))
        lines.append([("→ " if n == 0 else "  ", S_OK), (path, S_INK if n == 0 else S_MUTED), (score, S_MUTED)])
        if n == 0 and found.where:
            lines.append([(clip("  " + found.where, room), S_MUTED)])
    if s.status in ("error", "refused"):
        lines.append([("✗ the search did not run", S_BAD)])
    elif s.status == "ok" and s.search is not None and not s.found:
        lines.append([("nothing looked related", S_MUTED)])
    return lines


def compaction_lines(c: CompactionJudgement, room: int, cwd: Path | None) -> list[list[Part]]:
    """A compaction: the reads System 1 judged and kept, then what the cut did to the context."""
    kept = c.kept_reads()
    if not c.done:
        doing = "System 2 is writing its summary…" if c.writing else "System 1 is judging the reads being cut…"
        so_far = f" · {len(c.verdicts)} so far" if c.verdicts and not c.writing else ""
        return [[(clip(doing + so_far, room), S_MUTED_ITALIC)]]
    if c.verdicts:
        n = len(c.verdicts)
        head: list[Part] = [
            (f"{n} read{'s' if n != 1 else ''} judged", S_INK),
            (" · ", S_MUTED),
            (f"{len(kept)} kept", S_OK if kept else S_MUTED),
            (f" · {n - len(kept)} cut", S_MUTED),
        ]
    else:
        head = [("no reads to judge", S_MUTED)]
    to = "a summary" if c.method == "state" else "a list of their calls"
    cut = f"{tokens_text(c.tokens_before)} → {tokens_text(c.tokens_after)} {shrink(c)}"
    lines = [head, [(clip(f"{cut} · {c.turns_compacted} turns cut to {to}", room), S_MUTED)]]
    for v in sorted(kept, key=lambda v: -v.keep)[:KEPT_SHOWN]:
        score = f"  {v.keep:.2f}"
        path = fit_path(short_path(v.path, cwd), max(8, room - 2 - len(score)))
        lines.append([("✓ ", S_OK), (path, S_INK), (score, S_MUTED)])
    if len(kept) > KEPT_SHOWN:
        lines.append([(f"  and {len(kept) - KEPT_SHOWN} more kept", S_MUTED)])
    return lines


def handover_lines(h: HandoverJudgement, room: int) -> list[list[Part]]:
    """The run's hand-over in a line: System 1's P(ready) turn by turn, and, once the run has handed over, to which
    model and at which turn."""
    head: list[Part] = [("hand-over ", S_MUTED), *readiness(h)]
    if h.target:
        said = f"  → {model_name(h.target)} at turn {h.handed_at}"
        head.append((clip(said, max(8, room - Content.assemble(*head).cell_length)), S_BRAND_BOLD))
    return [head]


def other_lines(o: OtherJudgement, room: int) -> list[list[Part]]:
    """Any other ask in a line: what it was about, then System 1's answer."""
    said = o.choice.replace("_", " ")
    answer: list[Part] = [(said, S_WARN_BOLD if o.told else S_INK), (f" {o.probability:.2f}", S_MUTED)]
    if o.told:
        answer.append(("  ⚑ told", S_WARN))
    size = Content.assemble(*answer).cell_length
    first = o.subject.strip().splitlines()[0] if o.subject.strip() else ""
    # A command by what it runs, not the `cd` before it: the row is as wide as a few words.
    about = f"$ {brief(o.subject, room)}" if first and o.kind == "jobs" else first or o.purpose.replace("_", " ")
    return [[(clip(about, max(8, room - size - 2)), S_MUTED), "  ", *answer]]


def row_lines(row: Row, room: int, cwd: Path | None = None) -> tuple[Part, list[list[Part]]]:
    """A row's sign for the gutter, and its lines without it."""
    match row:
        case EffortJudgement():
            return (AUTO_SIGN, S_BRAND_BOLD), effort_lines(row, room)
        case HandoverJudgement():
            return (HANDOVER_SIGN, S_BRAND_BOLD if row.target else S_MUTED), handover_lines(row, room)
        case TurnJudgement():
            return " ", watch_lines(row, room)
        case GuardJudgement():
            return verdict_sign(row.refused), guard_lines(row, room)
        case RuleRefusal():
            return verdict_sign(True), rule_lines(row, room)
        case SearchJudgement():
            return (SIGN["search"], S_MUTED), search_lines(row, room, cwd)
        case CompactionJudgement():
            return (SIGN["compaction"], S_MUTED), compaction_lines(row, room, cwd)
        case OtherJudgement():
            return (SIGN[row.kind], S_MUTED), other_lines(row, room)


def feed(runs: list[RunLedger], width: int, cwd: Path | None = None) -> Content:
    """The session's rows, oldest first: each run under its prompt, each row behind its sign."""
    width = max(MIN_WIDTH, width)
    room = width - GUTTER
    lines: list[list[Part]] = []
    for run in runs:
        if not run.feed:
            continue
        if lines:
            lines.append([])
        if run.prompt:
            lines.append([("› ", S_HEAD), (clip(" ".join(run.prompt.split()), width - 2), S_HEAD)])
        for row in run.feed:
            sign, body = row_lines(row, room, cwd)
            lines.append([sign, " ", *body[0]])
            lines += [[" " * GUTTER, *line] for line in body[1:]]
    if len(lines) > FEED_ROWS:
        hidden = len(lines) - FEED_ROWS
        lines = [[(f"… {hidden:,} earlier lines", S_MUTED_ITALIC)], *lines[-FEED_ROWS:]]
    return stacked(lines)


# -- the panel ----------------------------------------------------------------------------------------------


class System1Panel(Vertical, can_focus=False):
    """System 1's judgements, drawn (the module's docstring)."""

    def __init__(self) -> None:
        super().__init__(id="system1")
        self._ledger = Ledger()
        self.cwd: Path | None = None
        self.border_title = Content.assemble((" System 1 ", S_TAB), (" thinking fast ", S_MUTED))

    def compose(self) -> ComposeResult:
        yield Static(id="system1-usage")
        with Following(id="system1-feed"):
            yield Static(Content.styled(EMPTY, S_MUTED), id="system1-rows")

    def show(self, ledger: Ledger) -> None:
        """The session's ledger as it now stands."""
        self._ledger = ledger
        self._draw()

    def on_resize(self) -> None:
        self._draw()

    def _draw(self) -> None:
        scroll = self.query_one("#system1-feed", Following)
        width = max(MIN_WIDTH, scroll.scrollable_content_region.width - 1)
        used = self.query_one("#system1-usage", Static)
        drawn = usage(self._ledger.usage, max(MIN_WIDTH, self.content_size.width))
        used.display = bool(drawn.plain)
        used.update(drawn)
        rows = feed(self._ledger.runs, width, self.cwd)
        self.query_one("#system1-rows", Static).update(rows if rows.plain else Content.styled(EMPTY, S_MUTED))
