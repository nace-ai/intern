"""The engagement extension: one capability switch behind the whole pentest harness.

It owns the session's ``EngagementStore`` and adds the tool families to the session. The store is
created eagerly, on a throwaway ``session-<stamp>`` folder, so every tool can
hold it from construction; naming a target (``/engage``, ``set_engagement``, ``intern engage``)
switches the store onto the real ``out/<slug>/`` folder and discards the throwaway. The drex tools
get the session's ``DecisionClient`` at start; the dispatch tool gets a child-session factory that
runs each specialist on the same models and config, its transcript under the engagement's logs.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar

from intern.browser import BROWSER_NEEDS
from intern.engagement.banner import show as show_banner
from intern.engagement.store import EngagementStore, slugify
from intern.events import EventSink
from intern.hooks import Extension, RunContext
from intern.needs import Needs
from intern.providers.base import ProviderRequest
from intern.tools.base import Tool

if TYPE_CHECKING:
    from intern.session import Session

#: Engagement folders live under ``out/`` in the working directory, so a later session in the
#: same folder — a TUI session after a headless run — picks up the same objects.
OUT_ROOT_NAME = "out"


def _playbooks_for(category_id: str) -> list[str]:
    from intern.engagement.playbooks import playbooks_for

    return list(playbooks_for(category_id))


class EngagementExtension(Extension):
    """The pentest harness behind the ``engagement`` capability. Off by default."""

    name: ClassVar[str] = "engagement"
    needs: ClassVar[Needs | None] = BROWSER_NEEDS
    """The browser the evidence pipeline (screenshots, logins, report PDF) drives: Playwright and the
    Chromium build it uses. `/caps` offers to install them before switching the capability on, and a
    client that cannot install says why; the agent-side commands are the same either way."""

    def __init__(self) -> None:
        from intern import __version__

        stamp = time.strftime("%Y-%m-%d-%H-%M-%S")
        self.store = EngagementStore(_out_root(), f"session-{stamp}", f"session-{stamp}", made_with=__version__)
        self._decisions: object | None = None
        self._spawn: Callable[[EventSink], object] | None = None

    # -- tools ---------------------------------------------------------------------------------

    def tools(self) -> list[Tool]:
        from intern.engagement.api_tools import ApiDiscoverTool, ApiProbeTool
        from intern.engagement.browser_sessions import BrowserSessionTool
        from intern.engagement.diagram import ReportDiagramTool
        from intern.engagement.dispatch import DispatchTool
        from intern.engagement.exploits import ExploitResearchTool
        from intern.engagement.host_tooling import HostToolsTool, InstallToolsTool
        from intern.engagement.jsintel import JsAnalyzeTool
        from intern.engagement.oast import OastTool
        from intern.engagement.payloads import PayloadsTool
        from intern.engagement.playbooks import LoadPlaybookTool
        from intern.engagement.report_tools import ReportContentTool, ReportTool
        from intern.engagement.screenshot import EvidenceTool, ScreenshotTool
        from intern.engagement.tools import (
            DrexDecideTool,
            DrexTriageTool,
            SetEngagementTool,
            StateQueryTool,
            StateUpdateTool,
        )

        store = self.store
        self._drex_tools = [DrexTriageTool(store), DrexDecideTool(store)]
        self._dispatch_tool = DispatchTool(store, self._child_session)
        return [
            SetEngagementTool(store),
            StateUpdateTool(store, _playbooks_for),
            StateQueryTool(store, _playbooks_for),
            *self._drex_tools,
            EvidenceTool(store),
            ScreenshotTool(store),
            BrowserSessionTool(store),
            ReportContentTool(store),
            ReportTool(store),
            ReportDiagramTool(store),
            ExploitResearchTool(store),
            OastTool(store),
            JsAnalyzeTool(store),
            ApiDiscoverTool(store),
            ApiProbeTool(store),
            LoadPlaybookTool(store),
            PayloadsTool(store),
            HostToolsTool(),
            InstallToolsTool(),
            self._dispatch_tool,
        ]

    # -- lifecycle -----------------------------------------------------------------------------

    def engage(
        self,
        target: str,
        *,
        scope: list[str] | None = None,
        objective: str | None = None,
        resume: bool = False,
        quiet: bool = False,
    ) -> str:
        """Name (or switch) the engagement's target; returns the human-readable result line."""
        slug = slugify(target)
        self.store.switch_target(target, objective, resume)
        if scope:
            self.store.configure(scope=scope)
        if resume and self.store.state.slug == slug:
            findings, endpoints = self.store.load_prior()
            if findings or endpoints:
                self.store.log("engagement", f"resumed prior: +{findings} finding(s), +{endpoints} endpoint(s)")
        result = (
            f"engagement set: target={self.store.state.target} dir={self.store.dir} "
            f"targets=[{', '.join(self.store.state.scope)}]"
        )
        if self.store.prior_meta and not resume:
            when = self.store.prior_meta.get("when")
            findings = self.store.prior_meta.get("findings")
            result += (
                f" — prior run from {_iso(when)} with {findings} finding(s) archived "
                "(reusing recon/creds/footholds; say resume to load it)"
                if isinstance(when, (int, float)) and isinstance(findings, int)
                else ""
            )
        if not quiet:
            show_banner(self.store.state.target, str(self.store.dir), objective=self.store.state.objective)
        return result

    def retest(self) -> str:
        """Enter retest mode on the current engagement."""
        from intern.engagement.retest import retest_kickoff

        return retest_kickoff(self.store)

    async def before_agent_start(self, ctx: RunContext) -> None:
        for tool in getattr(self, "_drex_tools", []):
            tool.decisions = ctx.decisions
        self._spawn = ctx.spawn_session

    async def before_provider_request(self, request: ProviderRequest, ctx: RunContext) -> ProviderRequest | None:
        """Prepend the engagement guide to the system prompt once, naming the current target."""
        from intern.engagement.prompt import GUIDE

        store = self.store
        if store.state.target.startswith("session-") and "/" not in store.state.target:
            return None  # still the throwaway: no target named yet
        head = (
            f"# Engagement\n\nTarget: {store.state.target}\n"
            f"Scope: {', '.join(store.state.scope) or store.state.target}\n"
            f"Objective: {store.state.objective or '(none stated)'}\n"
            f"Record: {store.state_file} (state_query what=summary shows where you stand)\n\n{GUIDE}"
        )
        messages = list(request.messages)
        for i, message in enumerate(messages):
            text = getattr(message, "text", None)
            if getattr(message, "role", None) == "system" and isinstance(text, str) and "# Engagement" not in text:
                messages[i] = message.model_copy(update={"text": f"{head}\n\n{text}"})
                return request.model_copy(update={"messages": messages})
        return None

    def close(self) -> None:
        """Drop the throwaway folder when nothing was recorded in it."""
        self.store.discard_if_unused()

    async def _child_session(self, label: str, sink: EventSink) -> Session:
        """A specialist's session: the parent's models and config, its events teed to the sink."""
        from intern.session import Session

        spawn = self._spawn
        if spawn is None:
            raise RuntimeError("specialist sessions need the session's spawn hook (before_agent_start)")
        session = spawn(sink)
        if isinstance(session, Session):
            return session
        return await session  # type: ignore[return-value]


def _out_root():
    from pathlib import Path

    return Path(OUT_ROOT_NAME)


def _iso(ms: object) -> str:
    if isinstance(ms, (int, float)):
        return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))
    return "?"


__all__ = ["EngagementExtension"]
