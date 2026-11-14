"""On-demand attack-surface playbooks: bundled methodology pulled into context only when the agent
reaches that surface, so the base prompt stays lean while coverage grows. Each ``.md`` file is a
self-contained, payload-level methodology; its front-matter tags (``owasp``, ``attack``,
``payloads``) drive the coverage matrix and the cross-reference to the payloads library.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel

from intern.engagement.owasp import normalize_owasp
from intern.tools.base import Effect, Tool, ToolContext, ToolOutput, fail

if TYPE_CHECKING:
    from intern.engagement.store import EngagementStore

_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


@dataclass(frozen=True)
class Playbook:
    name: str
    title: str
    triggers: str
    owasp: tuple[str, ...]
    attack: tuple[str, ...]
    #: PayloadsAllTheThings categories (or Methodology pages) that back this playbook.
    payloads: tuple[str, ...]
    body: str


def parse_playbook(raw: str) -> Playbook:
    """Minimal front-matter parse: a leading ``---\\n...\\n---`` block of ``key: value`` lines."""
    match = _FRONTMATTER.match(raw.lstrip())
    if match is None:
        return Playbook("unknown", "", "", (), (), (), raw.strip())
    meta: dict[str, str] = {}
    for line in match.group(1).split("\n"):
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip().strip("\"'")
    owasp = normalize_owasp([x.strip() for x in meta.get("owasp", "").split(",") if x.strip()])[0]
    attack = tuple(x.strip().lower() for x in meta.get("attack", "").split(",") if x.strip())
    payloads = tuple(x.strip() for x in meta.get("payloads", "").split(";") if x.strip())
    return Playbook(
        name=meta.get("name", "unknown"),
        title=meta.get("title") or meta.get("name", ""),
        triggers=meta.get("triggers", ""),
        owasp=tuple(owasp),
        attack=attack,
        payloads=payloads,
        body=match.group(2).strip(),
    )


_PLAYBOOKS: tuple[Playbook, ...] | None = None


def playbooks() -> tuple[Playbook, ...]:
    """The bundled playbooks, parsed once (file order, so the index is stable)."""
    global _PLAYBOOKS
    if _PLAYBOOKS is None:
        folder = Path(__file__).parent / "assets" / "playbooks"
        raw = sorted(folder.glob("*.md"))
        _PLAYBOOKS = tuple(parse_playbook(p.read_text(encoding="utf-8")) for p in raw)
    return _PLAYBOOKS


def playbooks_for(category_id: str) -> list[str]:
    """Names of the playbooks that cover an OWASP category (or an ATT&CK tactic tag)."""
    ids, _ = normalize_owasp([category_id])
    if ids:
        return [p.name for p in playbooks() if ids[0] in p.owasp]
    low = category_id.strip().lower()
    return [p.name for p in playbooks() if low in p.attack]


def get_playbook(surface: str) -> Playbook | None:
    """Resolve a playbook by exact name, then by loose name/title substring, then by OWASP id."""
    n = surface.strip().lower()
    if not n:
        return None
    ids = normalize_owasp([surface])[0]
    by_owasp = ids[0] if ids else None
    for p in playbooks():
        if p.name.lower() == n:
            return p
    for p in playbooks():
        if n in p.name.lower() or n in p.title.lower():
            return p
    if by_owasp:
        return next((p for p in playbooks() if by_owasp in p.owasp), None)
    return None


class LoadPlaybookArgs(BaseModel):
    surface: str | None = None


class LoadPlaybookTool(Tool):
    """Pull one playbook's methodology into context on demand."""

    name = "load_playbook"
    description = (
        "Pull in a deep, payload-level methodology for a specific attack surface, on demand. Call "
        "with NO argument to list the available playbooks (name + when to use each); call with "
        "surface=<name> to load that playbook's full checklist into context. Load the matching "
        "playbook BEFORE deep-testing a surface (e.g. SQL injection, JWT auth, cloud credentials) "
        "so you follow a complete methodology instead of improvising."
    )
    Args = LoadPlaybookArgs
    effects: ClassVar[frozenset[Effect]] = frozenset({"read"})

    def __init__(self, store: EngagementStore) -> None:
        self._store = store

    async def run(self, args: LoadPlaybookArgs, ctx: ToolContext) -> ToolOutput:
        if not args.surface:

            def index_line(p: Playbook) -> str:
                tags = f" [{', '.join(p.owasp)}]" if p.owasp else ""
                return f"- **{p.name}**{tags} — {p.triggers}"

            index = "\n".join(index_line(p) for p in playbooks())
            return ToolOutput(content=f"Available playbooks (load one with surface=<name>):\n{index}")
        playbook = get_playbook(args.surface)
        if playbook is None:
            names = ", ".join(p.name for p in playbooks())
            return fail(
                f"no playbook '{args.surface}'. Available: {names}", f"Call load_playbook with one of: {names}."
            )
        self._store.log("playbook", f"loaded {playbook.name}")
        refs = ""
        if playbook.payloads:
            quoted = ", ".join(f'`payloads action=show category="{c}"`' for c in playbook.payloads)
            refs = (
                "\n\n## Payload references (PayloadsAllTheThings)\n"
                f"For payload variants, filter/WAF bypasses and engine-specific technique: {quoted}."
            )
        return ToolOutput(content=f"# Playbook — {playbook.title}\n\n{playbook.body}{refs}")
