"""Harness events and journal entries as ACP `session/update`s, and run ends as ACP stop reasons.

Pure functions: what they need from the session comes in a `MappingContext`, so the table in
docs/acp.md is tested row by row.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from acp import text_block, tool_content, tool_diff_content, update_agent_message_text, update_agent_thought_text
from acp import update_user_message_text as user_text
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    ContentToolCallContent,
    Cost,
    FileEditToolCallContent,
    StopReason,
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
    ToolKind,
    UsageUpdate,
    UserMessageChunk,
)
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from intern.events import (
    ContextBudget,
    Event,
    FreeNotice,
    Handoff,
    ReasoningDelta,
    ReplyDiscarded,
    RunEnd,
    SecretUnavailable,
    TextDelta,
    ToolCallEnd,
    WindowFallback,
)
from intern.events import StopReason as RunStopReason
from intern.events import ToolCallStart as HarnessToolCallStart
from intern.hooks import RunError
from intern.journal import Entry, EventEntry, MessageEntry
from intern.memory.spend import StewardSpend
from intern.messages import (
    AssistantMessage,
    FileDiff,
    JsonObject,
    System1Usage,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from intern.models.registry import FREE_FINAL
from intern.tools.base import Effect

Update = UserMessageChunk | AgentMessageChunk | AgentThoughtChunk | ToolCallStart | ToolCallProgress | UsageUpdate
HANDOFF_METHOD = "nace/handoff"
"""The extension notification a hand-over is sent as; ACP puts `_` before it on the wire (`_nace/handoff`)."""

STOP_REASONS: dict[RunStopReason, StopReason] = {
    "completed": "end_turn",
    "stopped_by_hook": "end_turn",
    "stopped_by_tool": "end_turn",
    "stopped_by_guard": "end_turn",
    "aborted": "cancelled",
    "turn_budget": "max_turn_requests",
    "output_limit": "max_tokens",
    "waiting": "end_turn",  # never sent: ACP sessions hold the run instead of handing over
}

# Tools whose effects (read) undersell what they do: they search.
SEARCH_TOOLS = frozenset({"grep", "find"})
# The argument that says what a call is about, in the order they are looked for, for its title.
TITLE_ARGUMENTS = ("path", "command", "pattern", "query", "url")
TITLE_CHARS = 80

ProviderErrorKind = Literal["rate_limit", "quota", "auth", "provider"]


class ProviderFailure(BaseModel):
    """A run that ended on a provider error, as the JSON-RPC error's `data`."""

    kind: ProviderErrorKind
    detail: str
    status: int | None = None
    code: str | None = None
    """The Nace console's `error.code`, when it refused the request."""
    meter: str | None = None
    next: str | None = None
    """For a free credit refused for good: the System 2 the next run will use, None when no own model can run."""

    def data(self) -> dict[str, Any]:
        """The fields a console refusal adds only when it names them; `next` whenever the free credit ended."""
        data = self.model_dump(exclude={"code", "meter", "next"})
        data.update(self.model_dump(include={"code", "meter"}, exclude_none=True))
        if self.code in FREE_FINAL:
            data["next"] = self.next
        return data


@dataclass(frozen=True)
class MappingContext:
    """What the mapping needs to know about the session an event came from."""

    cwd: Path
    effects: Mapping[str, frozenset[Effect]] = field(default_factory=dict)
    """Each tool's declared effects, by tool name."""
    working_window: int = 0
    context_tokens: int = 0
    """Tokens in the latest request, from its `context_budget`."""
    cost_usd: float = 0.0
    """What the session has cost so far, System 1 included."""
    model: str = ""
    """The model whose replies and tool calls the run streams now: System 2, or the one the run handed over to."""
    free: Mapping[str, Any] | None = None
    """`_meta.nace.free` on usage updates: the free credit's meters and what each system runs on, while it is in
    use."""


_STATUS = re.compile(r"\bHTTP (\d{3})\b")
_RATE_LIMIT = re.compile(r"rate.?limit|too many requests", re.IGNORECASE)
_QUOTA = re.compile(r"quota|credits?\b|insufficient|billing|payment required", re.IGNORECASE)


def provider_failure(detail: str | None, error: RunError | None = None) -> ProviderFailure:
    """Classify a `provider_error` detail, so clients can show a rate limit or a spent quota as limited; with the
    console's `error`, its code, meter and next model."""
    failure = _classified(detail)
    if error is not None:
        failure.code, failure.meter, failure.next = error.code, error.meter, error.next
    return failure


def system1_failure(error: RunError | None) -> dict[str, Any]:
    """`system1_unavailable`'s JSON-RPC `data`: the reason, and the console's code and meter when it refused."""
    data: dict[str, Any] = {"reason": "system1_unavailable"}
    if error is not None:
        data["code"] = error.code
        if error.meter is not None:
            data["meter"] = error.meter
    return data


def _classified(detail: str | None) -> ProviderFailure:
    text = detail or "the provider failed"
    match = _STATUS.search(text)
    status = int(match.group(1)) if match else None
    kind: ProviderErrorKind = "provider"
    if status == 429 or _RATE_LIMIT.search(text):
        kind = "rate_limit"
    elif status == 402 or _QUOTA.search(text):
        kind = "quota"
    elif status in (401, 403):
        kind = "auth"
    return ProviderFailure(kind=kind, detail=text, status=status)


class _Wire(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class HandoffNotice(_Wire):
    """`_nace/handoff`'s params, less the session id the server adds: a run handed over at `turn`."""

    turn: int
    source: str = Field(serialization_alias="from")
    target: str = Field(serialization_alias="to")
    probability: float


Outgoing = Update | HandoffNotice
"""What a session sends: `session/update`s, and `_nace/handoff` in its place among them."""


def handoff_notice(event: Handoff) -> HandoffNotice:
    return HandoffNotice(turn=event.turn, source=event.source, target=event.target, probability=event.probability)


class System2PromptUsage(_Wire):
    model: str
    input_tokens: int
    output_tokens: int
    cached_read_tokens: int
    cached_write_tokens: int
    thought_tokens: int
    cost_usd: float


class System1PromptUsage(_Wire):
    model: str
    """The System 1 in force: a decision model or `decisions:<model>`."""
    asks: int
    failed: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class PromptUsage(_Wire):
    """`_meta.nace.usage` on a prompt response: what each system did for that prompt, follow-ups included. After a
    hand-over, `handoff` is the cheaper model's share and `system2` the rest."""

    system2: System2PromptUsage
    system1: System1PromptUsage
    handoff: System2PromptUsage | None = None


def _model_usage(model: str, usage: Usage) -> System2PromptUsage:
    return System2PromptUsage(
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_read_tokens=usage.cache_read_tokens,
        cached_write_tokens=usage.cache_write_tokens,
        thought_tokens=usage.reasoning_tokens,
        cost_usd=usage.cost_usd,
    )


def prompt_usage(
    system2_model: str,
    system2: Usage,
    system1_model: str,
    system1: System1Usage,
    handoff: tuple[str, Usage] | None = None,
) -> PromptUsage:
    """`system2` is everything System 2's side spent; `handoff`, the model handed over to and its share of it."""
    return PromptUsage(
        system2=_model_usage(system2_model, system2 - handoff[1] if handoff else system2),
        handoff=_model_usage(*handoff) if handoff else None,
        system1=System1PromptUsage(
            model=system1_model,
            asks=system1.asks,
            failed=system1.failed,
            input_tokens=system1.input_tokens,
            output_tokens=system1.output_tokens,
            cost_usd=system1.cost_usd,
        ),
    )


class StewardSystem1Usage(_Wire):
    asks: int
    failed: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class StewardWriterUsage(_Wire):
    input_tokens: int
    output_tokens: int
    cached_read_tokens: int
    cached_write_tokens: int
    thought_tokens: int
    cost_usd: float


class StewardUsage(_Wire):
    runs: int
    cost_usd: float
    system1: StewardSystem1Usage
    writer: StewardWriterUsage


def steward_update(ctx: MappingContext, spent: StewardSpend) -> UsageUpdate:
    writer = spent.writer
    usage = StewardUsage(
        runs=spent.runs,
        cost_usd=spent.cost_usd,
        system1=StewardSystem1Usage(**spent.system1.model_dump()),
        writer=StewardWriterUsage(
            input_tokens=writer.input_tokens,
            output_tokens=writer.output_tokens,
            cached_read_tokens=writer.cache_read_tokens,
            cached_write_tokens=writer.cache_write_tokens,
            thought_tokens=writer.reasoning_tokens,
            cost_usd=writer.cost_usd,
        ),
    )
    return UsageUpdate(
        session_update="usage_update",
        used=ctx.context_tokens,
        size=ctx.working_window,
        cost=Cost(amount=ctx.cost_usd, currency="USD"),
        field_meta={"nace": {"steward": usage.model_dump(by_alias=True)}},
    )


def stop_reason(reason: RunStopReason) -> StopReason | None:
    """The ACP stop reason for a run's end; None when the run failed and the prompt gets an error instead."""
    return STOP_REASONS.get(reason)


def tool_kind(name: str, effects: frozenset[Effect]) -> ToolKind:
    if name in SEARCH_TOOLS:
        return "search"
    for effect, kind in (("exec", "execute"), ("network", "fetch"), ("write", "edit"), ("read", "read")):
        if effect in effects:
            return kind  # pyright: ignore[reportReturnType]
    return "other"


def tool_title(name: str, arguments: JsonObject) -> str:
    subject = next((arguments[k] for k in TITLE_ARGUMENTS if isinstance(arguments.get(k), str)), None)
    if not subject:
        return name
    line = str(subject).strip().splitlines()[0] if str(subject).strip() else ""
    title = f"{name} {line}".strip()
    return title if len(title) <= TITLE_CHARS else title[: TITLE_CHARS - 3] + "..."


def tool_locations(arguments: JsonObject, cwd: Path) -> list[ToolCallLocation] | None:
    path = arguments.get("path")
    if not isinstance(path, str) or not path:
        return None
    resolved = Path(path).expanduser()
    resolved = resolved if resolved.is_absolute() else cwd / resolved
    offset = arguments.get("offset")
    line = offset if isinstance(offset, int) and not isinstance(offset, bool) else None
    return [ToolCallLocation(path=str(resolved), line=line)]


def _tool_start(call_id: str, name: str, arguments: JsonObject, ctx: MappingContext) -> ToolCallStart:
    return ToolCallStart(
        session_update="tool_call",
        tool_call_id=call_id,
        title=tool_title(name, arguments),
        kind=tool_kind(name, ctx.effects.get(name, frozenset())),
        status="in_progress",
        locations=tool_locations(arguments, ctx.cwd),
        raw_input=arguments,
    )


def _tool_end(call_id: str, status: str, content: str, diff: FileDiff | None = None) -> ToolCallProgress:
    items: list[ContentToolCallContent | FileEditToolCallContent] = []
    if content:
        items.append(tool_content(text_block(content)))
    if diff is not None:
        items.append(tool_diff_content(diff.path, diff.new_text, diff.old_text))
    return ToolCallProgress(
        session_update="tool_call_update",
        tool_call_id=call_id,
        status="completed" if status == "ok" else "failed",
        content=list(items) or None,
    )


def _usage(ctx: MappingContext, tokens: int, cost: bool, notice: str | None = None) -> UsageUpdate:
    free = {**ctx.free, **({"notice": notice} if notice else {})} if ctx.free is not None else None
    return UsageUpdate(
        session_update="usage_update",
        used=tokens,
        size=ctx.working_window,
        cost=Cost(amount=ctx.cost_usd, currency="USD") if cost else None,
        field_meta={"nace": {"free": free}} if free is not None else None,
    )


def _by[U: AgentMessageChunk | AgentThoughtChunk | ToolCallStart | ToolCallProgress](update: U, model: str) -> U:
    """`update` with `_meta.nace.model`, the model that wrote it or called the tool, when that is known."""
    if model:
        update.field_meta = {**(update.field_meta or {}), "nace": {"model": model}}
    return update


def event_updates(event: Event, ctx: MappingContext) -> list[Outgoing]:
    """The updates one live engine event becomes; most events become none."""
    match event:
        case TextDelta(text=text) if text:
            return [_by(update_agent_message_text(text), ctx.model)]
        case ReasoningDelta(text=text) if text:
            return [_by(update_agent_thought_text(text), ctx.model)]
        case ReplyDiscarded():
            # ACP cannot take chunks back: a line of the harness's own separates the broken reply from the new one.
            return [update_agent_message_text("\n\nThe reply broke off; it starts again:\n\n")]
        case HarnessToolCallStart(call_id=call_id, name=name, arguments=arguments):
            return [_by(_tool_start(call_id, name, arguments, ctx), ctx.model)]
        case ToolCallEnd(call_id=call_id, status=status, content=content, diff=diff):
            return [_by(_tool_end(call_id, status, content, diff), ctx.model)]
        case Handoff():
            return [handoff_notice(event)]
        case SecretUnavailable(message=message):
            return [update_agent_message_text(f"Secret not loaded: {message}\n\n")]
        case WindowFallback(message=message):
            return [update_agent_message_text(f"Window not known: {message}\n\n")]
        case ContextBudget(tokens=tokens):
            return [_usage(ctx, tokens, cost=False)]
        case FreeNotice(text=text):
            return [_usage(ctx, ctx.context_tokens, cost=False, notice=text)]
        case RunEnd():
            return [_usage(ctx, ctx.context_tokens, cost=True)]
        case _:
            return []


def replay_updates(path: list[Entry], ctx: MappingContext) -> list[Outgoing]:
    """The updates that show a loaded session's active branch as if it had just run, each hand-over in its place.
    Messages the harness added itself (nudges) are left out."""
    updates: list[Outgoing] = []
    called_by: dict[str, str] = {}
    for entry in path:
        if isinstance(entry, EventEntry) and isinstance(entry.event, Handoff):
            updates.append(handoff_notice(entry.event))
        if not isinstance(entry, MessageEntry):
            continue
        match entry.message:
            case UserMessage(content=content, source="user"):
                updates.append(user_text(content))
            case AssistantMessage(text=text, reasoning=reasoning, tool_calls=calls, model=model):
                if reasoning:
                    updates.append(_by(update_agent_thought_text(reasoning), model))
                if text:
                    updates.append(_by(update_agent_message_text(text), model))
                for c in calls:
                    called_by[c.id] = model
                    updates.append(_by(_tool_start(c.id, c.name, c.arguments, ctx), model))
            case ToolResultMessage(result=result):
                update = _tool_end(result.call_id, result.status, result.content)
                updates.append(_by(update, called_by.get(result.call_id, "")))
            case _:
                pass
    return updates
