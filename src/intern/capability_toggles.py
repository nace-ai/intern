"""The capabilities a user can switch on and off while a session runs: the TUI's `/caps` rows and the ACP `cap.*`
options are both these toggles. Behind a toggle is a tool pack, the browser, an MCP connection, the subagents' `agent`
tool or an extension (code search, the workspace, memory, the guard, the tool-result guard). Fixed extensions have no
toggle: the profile and `--extension` choose them. See CONTRIBUTING, "Extensions and capabilities"."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from intern.capability_settings import CapabilitySettings

PREFIX = "cap."
MCP_PREFIX = f"{PREFIX}mcp."
BASE = f"{PREFIX}base"
"""The `base` pack's row: listed by the TUI, never offered to ACP clients."""

PACKS = ("base", "web-search", "web-read")
"""The tool packs a session can switch (`docs/capability-settings.md`)."""
PACK_NAMES = {"base": "Base tools", "web-search": "Web search", "web-read": "Web read"}
PACK_NOTES = {
    "base": "read, write, edit, bash, grep, find, process: the agent's hands on the working directory",
    "web-search": "web_search: the web for source evidence, through Brave, Tavily, Exa or SearXNG",
    "web-read": "web_read: a URL fetched as source text the agent can read and quote",
}
PACK_SYSTEM1 = {
    "web-search": ("web_search", "the decision model keeps the results the task needs, best first"),
    "web-read": ("web_reader", "the decision model keeps the parts of a long page the task needs"),
}
"""The packs System 1 can judge for: each one's options table, whose `system1` turns it on, and what it does."""
RETIRED = frozenset({f"{PREFIX}process"})
"""Toggles no longer offered that a client or a journal may still set: accepted, and they change nothing. `process`
is a base tool."""
BROWSER_NOTE = "browser: Playwright pages the agent opens, reads, clicks, fills and screenshots"
CODE_SEARCH_NOTE = "decision_code: the decision model searches the repository for where something is defined or changed"
WORKSPACE_NOTE = "index_files, decision_files, sql, read: a folder of PDFs, Word files and workbooks the agent indexes"
MEMORY_NOTE = "a git repository of pages the agent recalls from at the start and adds to"
GUARD_NOTE = (
    "checks each tool call before it runs: fixed rules refuse the destructive, the decision model judges the rest"
)
TOOL_RESULT_GUARD_NOTE = (
    "the decision model removes instructions addressed to the agent from web, browser and MCP output"
)
SUBAGENTS_NOTE = "agent: the agent hands self-contained tasks to subagents with its own tools, or fewer, and its budget"


class Toggle(BaseModel):
    id: str
    """Stable across releases: the ACP config option id, and what the journal records."""
    name: str
    """What clients show."""
    label: str
    """What the TUI's `/caps` picker shows."""
    note: str
    on: bool


def _system1_note(caps: CapabilitySettings, pack: str) -> str:
    """What System 1 does for `pack`, or how to have it do so while its `system1` option is off; "" for other packs."""
    if pack not in PACK_SYSTEM1:
        return ""
    table, does = PACK_SYSTEM1[pack]
    on = getattr(caps, table).system1
    return f"; {does}" if on else f"; with [capabilities.{table}] system1 = true, {does}"


def packs_in_force(caps: CapabilitySettings, profile_packs: Sequence[str]) -> list[str]:
    """The tool packs in force: the settings' list, else the System 2 profile's."""
    return list(caps.tool_packs) if caps.tool_packs is not None else list(profile_packs)


def toggles(
    caps: CapabilitySettings, at_start: CapabilitySettings, profile_packs: Sequence[str], *, base: bool = False
) -> list[Toggle]:
    """One toggle per capability in capability order: the tool packs (`base` only when asked), the browser, code
    search, the workspace, memory, the guard, the tool-result guard, subagents, each MCP connection `at_start` (the
    configured settings) names, so one switched off is still listed. Where commands run is no toggle: it is the
    session's runner (`intern.runners`). `base=True` includes the TUI-only schedule row too."""
    packs = packs_in_force(caps, profile_packs)
    rows = [
        Toggle(
            id=f"{PREFIX}{pack}",
            name=PACK_NAMES[pack],
            label=f"{pack} tools" + (" · decision model" if pack in PACK_SYSTEM1 else ""),
            note=PACK_NOTES[pack] + _system1_note(caps, pack),
            on=pack in packs,
        )
        for pack in PACKS
        if base or pack != "base"
    ]
    rows += [
        Toggle(id=f"{PREFIX}browser", name="Browser", label="browser", note=BROWSER_NOTE, on=caps.browser.enabled),
        Toggle(
            id=f"{PREFIX}code-search",
            name="Code search",
            label="code search · decision model",
            note=CODE_SEARCH_NOTE,
            on=caps.code_search.enabled,
        ),
        Toggle(
            id=f"{PREFIX}workspace",
            name="Workspace",
            label="workspace · decision model",
            note=WORKSPACE_NOTE,
            on=caps.workspace.enabled,
        ),
        Toggle(id=f"{PREFIX}memory", name="Memory", label="memory drive", note=MEMORY_NOTE, on=caps.memory.enabled),
        Toggle(
            id=f"{PREFIX}guard", name="Guard", label="guard · decision model", note=GUARD_NOTE, on=caps.guard.enabled
        ),
        Toggle(
            id=f"{PREFIX}tool-result-guard",
            name="Tool-result guard",
            label="tool-result guard · decision model",
            note=TOOL_RESULT_GUARD_NOTE,
            on=caps.tool_result_guard.enabled,
        ),
        Toggle(
            id=f"{PREFIX}subagents", name="Subagents", label="subagents", note=SUBAGENTS_NOTE, on=caps.subagents.enabled
        ),
    ]
    if base:
        rows.append(
            Toggle(
                id="cap.schedule",
                name="Timers",
                label="schedule",
                on=caps.schedule.enabled,
                note="schedule, list_schedules, cancel_schedule: prompts on timers while this session stays open",
            )
        )
    for entry in at_start.mcp:
        names = list(entry.remote_tools)
        listed = ", ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")
        rows.append(
            Toggle(
                id=f"{MCP_PREFIX}{entry.namespace}",
                name=f"MCP {entry.namespace}",
                label=f"MCP {entry.namespace}",
                note=f"remote tools over {entry.transport}: {listed}",
                on=any(e.namespace == entry.namespace for e in caps.mcp),
            )
        )
    return rows


def flip(
    caps: CapabilitySettings, at_start: CapabilitySettings, toggle_id: str, on: bool, profile_packs: Sequence[str]
) -> CapabilitySettings:
    """`caps` with one toggle set to `on`. Raises KeyError for an id that is not a toggle of these settings."""
    if toggle_id in RETIRED:
        return caps
    if toggle_id.startswith(MCP_PREFIX):
        name = toggle_id.removeprefix(MCP_PREFIX)
        entry = next((e for e in at_start.mcp if e.namespace == name), None)
        if entry is None:
            raise KeyError(f"no MCP connection called {name!r} in the config")
        mcp = [e for e in caps.mcp if e.namespace != name]
        return caps.model_copy(update={"mcp": [*mcp, entry] if on else mcp})
    pack = toggle_id.removeprefix(PREFIX)
    if toggle_id.startswith(PREFIX) and pack in PACKS:
        packs = [p for p in packs_in_force(caps, profile_packs) if p != pack]
        return caps.model_copy(update={"tool_packs": [*packs, pack] if on else packs})
    match toggle_id:
        case "cap.browser":
            return caps.model_copy(update={"browser": caps.browser.model_copy(update={"enabled": on})})
        case "cap.code-search":
            return caps.model_copy(update={"code_search": caps.code_search.model_copy(update={"enabled": on})})
        case "cap.workspace":
            return caps.model_copy(update={"workspace": caps.workspace.model_copy(update={"enabled": on})})
        case "cap.memory":
            return caps.model_copy(update={"memory": caps.memory.model_copy(update={"enabled": on})})
        case "cap.guard":
            return caps.model_copy(update={"guard": caps.guard.model_copy(update={"enabled": on})})
        case "cap.tool-result-guard":
            screen = caps.tool_result_guard.model_copy(update={"enabled": on})
            return caps.model_copy(update={"tool_result_guard": screen})
        case "cap.schedule":
            return caps.model_copy(update={"schedule": caps.schedule.model_copy(update={"enabled": on})})
        case "cap.subagents":
            return caps.model_copy(update={"subagents": caps.subagents.model_copy(update={"enabled": on})})
        case _:
            raise KeyError(f"no capability toggle {toggle_id!r}")


def values(caps: CapabilitySettings, at_start: CapabilitySettings, profile_packs: Sequence[str]) -> dict[str, bool]:
    """Each client toggle's id and whether it is on."""
    return {toggle.id: toggle.on for toggle in toggles(caps, at_start, profile_packs)}


def applied(
    caps: CapabilitySettings, at_start: CapabilitySettings, picks: Mapping[str, bool], profile_packs: Sequence[str]
) -> CapabilitySettings:
    """`caps` with every pick set. Raises KeyError for an id that is not a client toggle of these settings."""
    known = values(caps, at_start, profile_packs)
    for toggle_id in picks:
        if toggle_id not in known and toggle_id not in RETIRED:
            raise KeyError(f"no capability toggle {toggle_id!r}")
    for toggle_id, on in picks.items():
        caps = flip(caps, at_start, toggle_id, on, profile_packs)
    return caps
