"""Prompt-cache hit rate from session journals: how much of each System 2 prompt the provider read from its cache,
and the replies where it stopped hitting, with what the journal shows happened in between.

    uv run evals/cache_hits.py                          every journal in ~/.intern/sessions
    uv run evals/cache_hits.py <journal or folder> ... [--window 131072] [--ttl 300] [--out cache.json]

The rate is cache read tokens ÷ input tokens (input counts the whole prompt), over System 2's replies and its State
writes, which send the agent's prefix too; System 1's asks go to another backend and are left out. A model none of
whose replies reports a cache read or write is listed as no data, not 0%: some OpenAI-compatible hosts never say.

A break is a call after the first whose rate is under 50% where the call before it was over 80%. Each is listed with
what the journal shows between the two: a compaction, a model change, a changed system prompt or tool list, a
capability switch, a memory recall, a window cap, a branch, an idle gap past the cache's lifetime (`--ttl`), or a
prompt near the prompt limit, where squeeze shortens the oldest tool results. The working window comes from the
profile, the session's cap or `--window`; when none says, squeeze is not judged. Squeeze itself is not journaled.
Breaks with nothing found are the ones to fix.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError

from intern.events import State
from intern.journal import (
    CapabilitiesChangeEntry,
    CompactionEntry,
    Entry,
    EventEntry,
    MemoryRecallEntry,
    MessageEntry,
    ModelChangeEntry,
    SystemPromptEntry,
    WindowCapEntry,
)
from intern.messages import AssistantMessage, Usage
from intern.models.profile import Profile, load_profile, profile_candidates

SESSIONS = Path.home() / ".intern" / "sessions"
PROFILES = Path.home() / ".intern" / "profiles"
BREAK_BELOW = 0.5
BREAK_AFTER = 0.8
# Squeeze runs once the estimate passes the prompt limit; the estimate is calibrated on the provider's count, so a
# prompt this close to the limit is taken as squeezed.
SQUEEZE_SHARE = 0.9
NOTHING = "nothing found"

_entry: TypeAdapter[Entry] = TypeAdapter(Entry)


@dataclass
class Call:
    """One System 2 request, as its reply's usage shows it."""

    session: str
    number: int
    kind: Literal["reply", "state"]
    model: str
    usage: Usage
    sent_at: int
    done_at: int
    entry_id: str
    between: list[str] = field(default_factory=list)
    """What the journal shows since the previous call; the reasons a break could have."""
    prompt_limit: int | None = None

    @property
    def rate(self) -> float:
        return self.usage.cache_read_tokens / self.usage.input_tokens


@dataclass
class Break:
    session: str
    call: int
    kind: str
    model: str
    rate_before: float
    rate: float
    input_tokens: int
    cache_read_tokens: int
    """0 is the whole prompt missed, the system prompt too: a change in the tools or the system prompt, or the
    provider's; more is the length of the prefix that still matched."""
    causes: list[str]


@dataclass
class Session:
    name: str
    calls: list[Call]
    parents: dict[str, str | None]

    def ancestors(self, entry_id: str) -> set[str]:
        seen: set[str] = set()
        cursor: str | None = entry_id
        while cursor is not None and cursor not in seen:
            seen.add(cursor)
            cursor = self.parents.get(cursor)
        return seen


def read_entries(path: Path) -> list[Entry]:
    """The journal's entries in append order, read only: lines that do not parse (a torn tail, an older format) are
    skipped and counted, never repaired as opening the session would."""
    entries, skipped = [], 0
    for line in path.read_bytes().splitlines():
        if line.strip():
            try:
                entries.append(_entry.validate_json(line))
            except ValidationError:
                skipped += 1
    if skipped:
        print(f"{path.name}: skipped {skipped} unreadable lines", file=sys.stderr)
    return entries


def working_window(profile: Profile, cap: int | None, window: int | None) -> int | None:
    """The smallest known of the advertised window (`--window`, else the profile's), the effective one and the cap;
    None when none is known, as when the window came from the provider's listing."""
    known = [w for w in (window or profile.window, profile.effective_window, cap) if w]
    return min(known) if known else None


def session_calls(path: Path, profiles: list[tuple[str, dict[str, Any]]], window: int | None) -> Session:
    """Every System 2 call in the journal, in the order it was made, each with what happened since the one before."""
    calls: list[Call] = []
    parents: dict[str, str | None] = {}
    between: list[str] = []
    system: tuple[str, list[str]] | None = None
    toggles: dict[str, bool] | None = None
    recalled: str | None = None
    cap: int | None = None
    model = ""
    for entry in read_entries(path):
        parents[entry.id] = entry.parent_id
        usage, kind, took = None, "reply", 0
        match entry:
            case SystemPromptEntry(content=content, tools=tools):
                if system is not None and content != system[0]:
                    between.append("system prompt changed")
                if system is not None and tools != system[1]:
                    between.append("tool list changed")
                system = (content, tools)
            case CapabilitiesChangeEntry(toggles=new):
                if toggles is not None and new != toggles:
                    flipped = sorted(k for k in new.keys() | toggles.keys() if new.get(k) != toggles.get(k))
                    between.append(f"capabilities switched: {', '.join(flipped)}")
                toggles = new
            case MemoryRecallEntry(text=text):
                if text != recalled:
                    between.append("memory recall")
                recalled = text
            case CompactionEntry(method=method):
                between.append(f"compaction ({method})")
            case ModelChangeEntry(role="system2", model=ref):
                between.append(f"model change to {ref}")
            case WindowCapEntry(cap=new):
                between.append(f"window cap {new or 'removed'}")
                cap = new
            case MessageEntry(message=AssistantMessage() as message):
                usage, took, model = message.usage, message.duration_ms, message.model or model
            case EventEntry(event=State(usage=Usage() as used) as state):
                usage, kind, took, model = used, "state", state.duration_ms or 0, state.model or model
        if usage is None or usage.input_tokens <= 0:
            continue
        profile = load_profile(model, candidates=profiles)
        working = working_window(profile, cap, window)
        calls.append(
            Call(
                session=path.stem,
                number=len(calls) + 1,
                kind=kind,
                model=model,
                usage=usage,
                sent_at=entry.timestamp - took,
                done_at=entry.timestamp,
                entry_id=entry.id,
                between=between,
                prompt_limit=int(working * (1 - profile.thresholds.output_reserve)) if working else None,
            )
        )
        between = []
    return Session(path.stem, calls, parents)


def causes(session: Session, before: Call, call: Call, ttl_s: int) -> list[str]:
    found = list(call.between)
    if call.model != before.model and not any(c.startswith("model change") for c in found):
        found.append(f"model change to {call.model}")
    if before.entry_id not in session.ancestors(call.entry_id):
        found.append("branch")
    idle_s = (call.sent_at - before.done_at) / 1000
    if idle_s > ttl_s:
        found.append(f"idle {idle_s / 60:.0f} min")
    if call.prompt_limit and call.usage.input_tokens >= SQUEEZE_SHARE * call.prompt_limit:
        found.append(f"squeeze (prompt at {call.usage.input_tokens / call.prompt_limit:.0%} of its limit)")
    return found or [NOTHING]


def breaks(session: Session, ttl_s: int) -> list[Break]:
    return [
        Break(
            session=session.name,
            call=call.number,
            kind=call.kind,
            model=call.model,
            rate_before=before.rate,
            rate=call.rate,
            input_tokens=call.usage.input_tokens,
            cache_read_tokens=call.usage.cache_read_tokens,
            causes=causes(session, before, call, ttl_s),
        )
        for before, call in zip(session.calls, session.calls[1:], strict=False)
        if before.rate > BREAK_AFTER and call.rate < BREAK_BELOW
    ]


def reports_cache(calls: list[Call]) -> bool:
    return any(c.usage.cache_read_tokens or c.usage.cache_write_tokens for c in calls)


def rate(calls: list[Call]) -> float:
    return sum(c.usage.cache_read_tokens for c in calls) / max(sum(c.usage.input_tokens for c in calls), 1)


def journals(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        found += sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("paths", nargs="*", type=Path, default=[SESSIONS], help="Journals, or folders of them.")
    parser.add_argument("--window", type=int, help="The advertised window, where the profile does not set one.")
    parser.add_argument("--ttl", type=int, default=300, help="Seconds the provider keeps a prefix cached.")
    parser.add_argument("--out", type=Path, help="Write per-session rates and the breaks here as JSON.")
    args = parser.parse_args(argv)

    profiles = profile_candidates([PROFILES])
    sessions = [s for s in (session_calls(p, profiles, args.window) for p in journals(args.paths)) if s.calls]
    if not sessions:
        print("no System 2 calls in these journals", file=sys.stderr)
        return 2
    by_model: dict[str, list[Call]] = defaultdict(list)
    for session in sessions:
        for call in session.calls:
            by_model[call.model].append(call)
    with_data = {m for m, calls in by_model.items() if reports_cache(calls)}
    found = [b for s in sessions for b in breaks(s, args.ttl) if b.model in with_data]

    def shown(calls: list[Call]) -> str:
        return f"{rate(calls):.0%}" if {c.model for c in calls} <= with_data else "no data"

    print("| model | sessions | calls | input tokens | cache read | hit rate |")
    print("|---|---|---|---|---|---|")
    for model, calls in sorted(by_model.items(), key=lambda kv: -len(kv[1])):
        read, total = sum(c.usage.cache_read_tokens for c in calls), sum(c.usage.input_tokens for c in calls)
        n = len({c.session for c in calls})
        print(f"| {model} | {n} | {len(calls)} | {total:,} | {read:,} | {shown(calls)} |")

    print("\n| session | calls | input tokens | hit rate | breaks |")
    print("|---|---|---|---|---|")
    for s in sessions:
        total = sum(c.usage.input_tokens for c in s.calls)
        n = sum(b.session == s.name for b in found)
        print(f"| {s.name} | {len(s.calls)} | {total:,} | {shown(s.calls)} | {n} |")

    unexplained = [b for b in found if b.causes == [NOTHING]]
    print(f"\n{len(found)} breaks, {len(unexplained)} with nothing found")
    if found:
        print("\n| session | call | kind | before | rate | input tokens | cache read | between |")
        print("|---|---|---|---|---|---|---|---|")
        for b in found:
            print(
                f"| {b.session} | {b.call} | {b.kind} | {b.rate_before:.0%} | {b.rate:.0%} | {b.input_tokens:,} "
                f"| {b.cache_read_tokens:,} | {'; '.join(b.causes)} |"
            )
    if args.out:
        rows = {
            "sessions": [
                {"session": s.name, "calls": len(s.calls), "rate": rate(s.calls) if reports_cache(s.calls) else None}
                for s in sessions
            ],
            "breaks": [asdict(b) for b in found],
        }
        args.out.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
