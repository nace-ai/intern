"""System 1's decisions as a ledger the TUI shows: its pick of a run's thinking level at `auto`, one judgement per turn
it looked at, one per tool call the guard asked about, one per search (`decision_code`, `decision_files`), one per
compaction, and one for every other ask a turn made, per run, in the order they came (`RunLedger.feed`); and what all
of System 1's asks came to, by kind (`Ledger.usage`).

Built from the event stream alone (`run_start`, `decision`, `state_writing`, `state_delta`, `state`, `nudge`,
`compaction`, `tool_guard`, `process_check`, a search's calls and its `search_trace`s), the
way the stream is meant to be audited: a stuck strike, a dropped read, a State written or a refused call can be
followed from it. Stuck strikes are counted the way `supervise.py` counts them, from its constants, so the row
that earned a nudge says so; the guard's verdict is read the way the guard extension reads it, from its thresholds.
A search's own asks (a card an ask, hundreds a search) are counted, and the search is one row.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from pydantic import ValidationError

from intern.events import (
    Compaction,
    Decision,
    Event,
    ExtensionEvent,
    Handoff,
    MessageEvent,
    Nudge,
    ProcessCheck,
    RunStart,
    State,
    StateDelta,
    StateWriting,
    ToolCallEnd,
    ToolCallStart,
    ToolGuard,
)
from intern.evidence import WORKING
from intern.extensions.guard import ASKED_THRESHOLD, RISK_THRESHOLD, RISKS
from intern.locate.trace import KIND as SEARCH_TRACE
from intern.locate.trace import SearchTrace
from intern.messages import JsonObject, UserMessage
from intern.supervise import MAX_NUDGES, STRIKES, STUCK, is_strike

WORKING_ON = "working_on"
SEARCH_TOOLS = ("decision_code", "decision_files")
"""The tools that are System 1 searching: a row each on its panel."""
TOP = 3
"""The places a search's row keeps, best first."""
Kind = Literal["hand-over", "watch", "guard", "search", "compaction", "jobs", "memory", "other"]
KINDS: tuple[Kind, ...] = ("hand-over", "watch", "guard", "search", "compaction", "jobs", "memory", "other")
"""What System 1 is asked for, in the order its panel counts them."""


def kind_of(purpose: str) -> Kind:
    """What an ask was for, from the purpose its caller gave it."""
    if purpose == "handoff":
        return "hand-over"
    if purpose in ("supervise", "wrap_up"):
        return "watch"
    if purpose in ("guard", "workspace_table_load"):
        return "guard"
    if purpose == "keep":
        return "compaction"
    if purpose.startswith("workspace."):
        return "search"
    if purpose in ("background", "processes"):
        return "jobs"
    return "memory" if purpose.startswith(("memory_", "steward_")) else "other"


REASONS = tuple(WORKING.criteria)
"""System 1's choices for a read being cut, in the question's order: kept, then the two reasons to cut it."""


@dataclass
class TurnJudgement:
    """The supervisor's look at a turn's end: what the latest turns are doing; then what the loop did about it."""

    turn: int
    mode: str
    probabilities: dict[str, float]
    backend: str
    cached: bool
    duration_ms: int
    cost_usd: float
    strikes: int | None = None
    """Consecutive stuck verdicts, this one included; None once the run's nudges are spent."""
    nudged: bool = False
    """A stuck nudge went into the next request."""
    wrap: str = ""
    """The look's second question, whether the request is met: working, done_polishing, done_unsaved or unclear."""
    wrap_probability: float = 0.0
    wrapped: bool = False
    """The wrap-up nudge went into the next request."""

    @property
    def stuck(self) -> bool:
        return self.mode in STUCK

    @property
    def confidence(self) -> float:
        return self.probabilities.get(self.mode, 0.0)


@dataclass
class KeepVerdict:
    path: str
    choice: str
    probabilities: dict[str, float]
    cached: bool

    @property
    def kept(self) -> bool:
        return self.choice == WORKING_ON

    @property
    def keep(self) -> float:
        """P(working on): how close the read came to being kept, whatever won."""
        return self.probabilities.get(WORKING_ON, 1.0 if self.kept else 0.0)


@dataclass
class GuardJudgement:
    """System 1's judgement of one tool call before it ran: what the call would do (routine, one of the risks, or
    unclear) and whether the user asked for it; then whether the guard refused the call on it."""

    turn: int
    subject: str
    """The tool and its arguments, as the guard named them."""
    kind: str
    probabilities: dict[str, float]
    asked: float
    backend: str
    cached: bool
    duration_ms: int
    cost_usd: float
    refused: bool = False
    reason: str = ""

    @property
    def tool(self) -> str:
        return self.subject.split(" ", 1)[0]

    @property
    def risk(self) -> float:
        """P(any risk): the risks' probabilities together, the way the guard reads them."""
        return min(1.0, sum(self.probabilities.get(name, 0.0) for name in RISKS))

    @property
    def would_block(self) -> bool:
        return self.risk >= RISK_THRESHOLD and self.asked < ASKED_THRESHOLD


@dataclass
class RuleRefusal:
    """A fixed rule refused a tool call; no model was asked."""

    turn: int
    call_id: str
    name: str
    rule: str
    reason: str
    arguments: JsonObject | None = None
    """The call's, when the ledger saw it start."""


@dataclass
class Found:
    """A place a search ranked: its path, where in it (a symbol and its lines, a page), its score."""

    path: str
    where: str
    score: float


@dataclass
class SearchJudgement:
    """One `decision_code` or `decision_files` call: System 1 walking an index, a card an ask. The call gives the
    question; its traces, while it runs, what the walk came to. A resumed session has only the call."""

    turn: int
    call_id: str
    tool: str
    question: str
    scope: str | None = None
    search: str | None = None
    """The trace's id for this search, once its first trace came."""
    asks: int = 0
    judged: int = 0
    cost_usd: float = 0.0
    elapsed_ms: int = 0
    found: list[Found] = field(default_factory=list)
    """Its best places, once it ranked them."""
    status: str | None = None
    """How the call ended: ok, error or refused; None while it runs."""


@dataclass
class HandoverJudgement:
    """System 1's judgement, turn by turn, of whether the cheaper model can finish the run: one row a run."""

    turn: int
    ready: list[float] = field(default_factory=list)
    """P(ready) at each turn it was asked, oldest first."""
    target: str = ""
    """The model the run handed over to, once it has."""
    handed_at: int | None = None


@dataclass
class OtherJudgement:
    """An ask with no row of its own kind, in one line: what a command that went on running is (`background`), a
    look at a running command (`processes`), whatever else an extension asked for a turn."""

    turn: int | None
    purpose: str
    subject: str
    choice: str
    probability: float
    told: bool = False
    """The verdict was told to the agent."""

    @property
    def kind(self) -> Kind:
        return kind_of(self.purpose)


@dataclass
class CompactionJudgement:
    """A compaction: System 2's summary being written, the keep-or-cut verdict on every read being cut, then the
    cut itself."""

    turn: int
    verdicts: list[KeepVerdict] = field(default_factory=list)
    writing: bool = False
    """System 2 is writing its summary (the State) for this compaction."""
    draft: str = ""
    """The write's text so far, as it streams (edits to a standing summary do not stream)."""
    state_written: bool = False
    """A State was written for this compaction, because the standing one did not cover the cut."""
    method: str | None = None
    turns_compacted: int = 0
    kept: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    summary: str = ""
    """What the agent continues from: the State, or with `drop` the list of the dropped calls."""

    @property
    def done(self) -> bool:
        return self.method is not None

    def kept_reads(self) -> list[KeepVerdict]:
        return [v for v in self.verdicts if v.kept]

    def cut_reads(self) -> dict[str, list[KeepVerdict]]:
        """The reads cut, by System 1's reason, the closest to being kept first: the reasons in the order the
        question gives them, so `reference` (read to understand) comes before `done with`."""
        by_reason: dict[str, list[KeepVerdict]] = {}
        for v in sorted((v for v in self.verdicts if not v.kept), key=lambda v: -v.keep):
            by_reason.setdefault(v.choice, []).append(v)
        order = {reason: n for n, reason in enumerate(REASONS)}
        return dict(sorted(by_reason.items(), key=lambda kv: order.get(kv[0], len(order))))


Row = (
    HandoverJudgement
    | TurnJudgement
    | GuardJudgement
    | RuleRefusal
    | SearchJudgement
    | CompactionJudgement
    | OtherJudgement
)
"""What the System 1 panel lists, and what `observe` returns: the row an event made or changed."""


@dataclass
class Usage:
    """What System 1 was asked over the session: the asks it answered by kind, and what they took."""

    asks: Counter[Kind] = field(default_factory=Counter)
    cached: int = 0
    """Answers that came from the cache: no ask was sent."""
    failed: int = 0
    """Attempts System 1 did not answer; each was retried."""
    duration_ms: int = 0
    """The time the answered asks took, together."""

    @property
    def total(self) -> int:
        return sum(self.asks.values())


@dataclass
class RunLedger:
    run_id: str
    prompt: str = ""
    """What the user asked for: the run's first message."""
    feed: list[Row] = field(default_factory=list)
    """Every row of the run, in the order System 1 was first asked for it."""
    handover: HandoverJudgement | None = None
    turns: dict[int, TurnJudgement] = field(default_factory=dict)
    compactions: dict[int, CompactionJudgement] = field(default_factory=dict)
    guards: list[GuardJudgement] = field(default_factory=list)
    refusals: int = 0
    """Calls the guard refused, by a rule or on System 1's judgement."""
    strikes: int = 0
    nudges: int = 0
    wraps: dict[int, tuple[str, float]] = field(default_factory=dict)
    """Each turn's wrap-up verdict, which may come before the turn's judgement: the two are asked together."""
    calls: dict[str, JsonObject] = field(default_factory=dict)
    """The arguments of the calls in progress, by call id: a refusal names its call."""


@dataclass
class Ledger:
    runs: list[RunLedger] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)

    @property
    def current(self) -> RunLedger:
        if not self.runs:
            self.runs.append(RunLedger(run_id=""))
        return self.runs[-1]

    def observe(self, event: Event) -> Row | None:
        """Take one event; the row it created or changed, for the panel to redraw, or None."""
        match event:
            case RunStart(run_id=run_id):
                self.runs.append(RunLedger(run_id=run_id))
            case MessageEvent(message=UserMessage(content=content, source="user")):
                self.current.prompt = self.current.prompt or content
            case Decision():
                return self._asked(event)
            case ToolCallStart(turn=turn, call_id=call_id, name=name, arguments=arguments):
                self.current.calls[call_id] = arguments
                if name in SEARCH_TOOLS:
                    question, scope = arguments.get("question"), arguments.get("scope")
                    search = SearchJudgement(
                        turn=turn,
                        call_id=call_id,
                        tool=name,
                        question=question if isinstance(question, str) else "",
                        scope=scope if isinstance(scope, str) else None,
                    )
                    self.current.feed.append(search)
                    return search
            case ToolCallEnd(call_id=call_id, status=status, duration_ms=duration_ms):
                self.current.calls.pop(call_id, None)
                for row in self.current.feed:
                    if isinstance(row, SearchJudgement) and row.call_id == call_id:
                        row.status, row.elapsed_ms = status, row.elapsed_ms or duration_ms
                        return row
            case ExtensionEvent(kind=kind, data=data) if kind == SEARCH_TRACE:
                return self._searched(data)
            case ProcessCheck(turn=turn, verdicts=verdicts):
                for v in verdicts:
                    look = OtherJudgement(
                        turn, "processes", v.command, v.verdict, v.probabilities.get(v.verdict, 1.0), told=v.told
                    )
                    self.current.feed.append(look)
            case ToolGuard(layer="system1", reason=reason):
                self.current.refusals += 1
                if (judged := self.current.guards[-1:]) and not judged[0].refused:  # the one just asked about
                    judged[0].refused = True
                    judged[0].reason = reason
                    return judged[0]
            case ToolGuard(layer="rule", turn=turn, call_id=call_id, name=name, rule=rule, reason=reason):
                self.current.refusals += 1
                refusal = RuleRefusal(turn, call_id, name, rule, reason, arguments=self.current.calls.get(call_id))
                self.current.feed.append(refusal)
                return refusal
            case Handoff(turn=turn, target=target, probability=probability):
                run = self.current
                if run.handover is None:  # a journal from before the asks were kept
                    run.handover = HandoverJudgement(turn, [probability])
                    run.feed.append(run.handover)
                run.handover.target, run.handover.handed_at = target, turn
                return run.handover
            case StateWriting(turn=turn):
                compaction = self._compaction(turn)
                compaction.writing, compaction.draft = True, ""
                return compaction
            case StateDelta(turn=turn, text=text):
                self._compaction(turn).draft += text  # the card's own frames draw it, not a redraw per delta
            case State(turn=turn):
                compaction = self._compaction(turn)
                compaction.state_written, compaction.writing, compaction.draft = True, False, ""
                return compaction
            case Nudge(reason="stuck_gate" | "wrap_up" as reason, turn=turn):
                if judgement := self.current.turns.get(turn):
                    judgement.nudged |= reason == "stuck_gate"
                    judgement.wrapped |= reason == "wrap_up"
                    return judgement
            case Compaction(turn=turn):
                compaction = self._compaction(turn)
                compaction.method = event.method
                compaction.turns_compacted = event.turns_compacted
                compaction.kept = event.kept
                compaction.tokens_before = event.tokens_before
                compaction.tokens_after = event.tokens_after
                compaction.summary = event.summary
                return compaction
            case _:
                pass
        return None

    def _asked(self, event: Decision) -> Row | None:
        """One attempt at an ask: counted, and its row when a turn made it. A failed attempt is retried, and the
        answer that follows is the one shown."""
        purpose, turn, answers = event.purpose, event.turn, event.answers
        if not event.ok:
            self.usage.failed += 1
            return None
        if event.cached:
            self.usage.cached += 1
        else:
            self.usage.asks[kind_of(purpose)] += 1
            self.usage.duration_ms += event.duration_ms
        run = self.current
        if turn is None or answers is None:
            return None  # a tool's own ask (a search's, the recall's): counted, and the tool's row shows it
        if purpose == "handoff":
            choice, probabilities = _choice(answers.get("ready"))
            if run.handover is None:
                run.handover = HandoverJudgement(turn)
                run.feed.append(run.handover)
            run.handover.ready.append(probabilities.get("ready", float(choice == "ready")))
            return run.handover
        if purpose == "supervise":
            return self._supervised(turn, event, answers)
        if purpose == "wrap_up":
            choice, probabilities = _choice(answers.get("wrap"))
            run.wraps[turn] = wrap = (choice, probabilities.get(choice, 0.0))
            if judgement := run.turns.get(turn):
                judgement.wrap, judgement.wrap_probability = wrap
            return judgement
        if purpose == "keep":
            if event.subject is None:
                return None
            choice, probabilities = _choice(answers.get("w"))
            verdict = KeepVerdict(event.subject, choice, probabilities, cached=event.cached)
            compaction = self._compaction(turn)
            compaction.verdicts = [v for v in compaction.verdicts if v.path != verdict.path] + [verdict]
            return compaction
        if purpose == "guard":
            return self._guarded(turn, event.subject or "", event, answers) if event.subject is not None else None
        if purpose == "processes":
            return None  # the `process_check` that follows names each command with its verdict
        choice, probability = _first(answers)
        other = OtherJudgement(turn, purpose, event.subject or "", choice, probability)
        run.feed.append(other)
        return other

    def _searched(self, data: JsonObject) -> SearchJudgement | None:
        """A search's trace, onto the row of the call it belongs to: the one with its id, else the earliest call
        still running that asked its question (searches of one turn run side by side)."""
        try:
            trace = SearchTrace.model_validate(data)
        except ValidationError:
            return None
        rows = [r for r in self.current.feed if isinstance(r, SearchJudgement)]
        waiting = [r for r in rows if r.search is None and r.status is None]
        row = (
            next((r for r in rows if r.search == trace.search), None)
            or next((r for r in waiting if r.question == trace.question), None)
            or next(iter(waiting), None)
        )
        if row is None:
            return None
        row.search = trace.search
        row.asks, row.judged, row.cost_usd = trace.asks, trace.judged, trace.cost_usd
        row.elapsed_ms = trace.elapsed_ms or row.elapsed_ms
        if trace.ranking:  # the last trace's: the files in their final order, each with its best place
            row.found = [Found(r.path, r.where, r.score) for r in trace.ranking[:TOP]]
        return row

    def _compaction(self, turn: int) -> CompactionJudgement:
        run = self.current
        if turn not in run.compactions:
            run.compactions[turn] = CompactionJudgement(turn=turn)
            run.feed.append(run.compactions[turn])
        return run.compactions[turn]

    def _guarded(self, turn: int, subject: str, event: Decision, answers: Mapping[str, JsonObject]) -> GuardJudgement:
        kind, probabilities = _choice(answers.get("risk"))
        if not probabilities and kind != "?":  # a bare choice, the way the guard reads one
            probabilities = {kind: 1.0}
        asked = answers.get("asked")
        judgement = GuardJudgement(
            turn=turn,
            subject=subject,
            kind=kind,
            probabilities=probabilities,
            asked=_number(asked.get("noul")) if isinstance(asked, dict) else 0.0,
            backend=event.backend,
            cached=event.cached,
            duration_ms=event.duration_ms,
            cost_usd=event.cost_usd,
        )
        self.current.guards.append(judgement)
        self.current.feed.append(judgement)
        return judgement

    def _supervised(self, turn: int, event: Decision, answers: Mapping[str, JsonObject]) -> TurnJudgement:
        run = self.current
        mode, probabilities = _choice(answers.get("mode"))
        judgement = TurnJudgement(
            turn=turn,
            mode=mode,
            probabilities=probabilities,
            backend=event.backend,
            cached=event.cached,
            duration_ms=event.duration_ms,
            cost_usd=event.cost_usd,
        )
        # Strikes as the supervisor counts them: its looks, consecutive, starting over after a nudge.
        if run.nudges < MAX_NUDGES:
            run.strikes = run.strikes + 1 if is_strike(mode, probabilities) else 0
            judgement.strikes = run.strikes
            if run.strikes >= STRIKES:
                run.strikes = 0
                run.nudges += 1
        if "wrap" in answers:  # the same ask's second question; a session from before 0.39 has it as its own ask
            choice, wrap_probabilities = _choice(answers["wrap"])
            run.wraps[turn] = (choice, wrap_probabilities.get(choice, 0.0))
        if wrap := run.wraps.get(turn):
            judgement.wrap, judgement.wrap_probability = wrap
        if (before := run.turns.get(turn)) is not None and before in run.feed:
            run.feed[run.feed.index(before)] = judgement
        else:
            run.feed.append(judgement)
        run.turns[turn] = judgement
        return judgement


def _number(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _first(answers: Mapping[str, JsonObject]) -> tuple[str, float]:
    """An ask's first answer in one line's worth: a choice and its probability, or a noul's probability alone."""
    answer = next(iter(answers.values()), None)
    if isinstance(answer, dict) and "noul" in answer:
        return "", _number(answer["noul"])
    choice, probabilities = _choice(answer)
    return choice, probabilities.get(choice, 1.0 if choice != "?" else 0.0)


def _choice(answer: object) -> tuple[str, dict[str, float]]:
    """A compact choice answer's option and probabilities; `?` and none when the event carries no answer."""
    if not isinstance(answer, dict):
        return "?", {}
    choice = answer.get("choice")
    raw = answer.get("probabilities")
    probabilities = {str(k): _number(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    return (choice if isinstance(choice, str) else "?"), probabilities
