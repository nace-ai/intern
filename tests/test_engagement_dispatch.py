"""The specialist dispatch: role catalog, role prompt composition, and the dispatch tool."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import intern.engagement.dispatch as dispatch
from intern.engagement import roles
from intern.engagement.dispatch import MAX_CONCURRENT, DispatchTool
from intern.engagement.roles import RoleDef
from intern.engagement.store import EngagementStore
from intern.events import EventSink
from intern.hooks import RunOutcome
from intern.tools.base import ToolContext

EXPECTED_ROLES = {
    "recon",
    "osint",
    "webapp",
    "api",
    "infra",
    "identity",
    "cloud",
    "container",
    "cicd",
    "mobile",
    "postex",
    "exploit",
    "verifier",
    "generic",
}


def role_or_fail(id: str) -> RoleDef:
    role = roles.get_role(id)
    assert role is not None
    return role


def make_store(tmp_path: Path) -> EngagementStore:
    return EngagementStore(
        tmp_path / "out",
        "example-com",
        "example.com",
        scope=["example.com", "*.example.com"],
        objective="assess the public attack surface",
    )


# -- roles catalog ----------------------------------------------------------------------


def test_catalog_covers_the_fourteen_roles():
    assert {r.id for r in roles.ROLES} == EXPECTED_ROLES
    assert set(roles.ROLE_IDS) == EXPECTED_ROLES


def test_every_playbook_exists_on_disk():
    for role in roles.ROLES:
        for name in role.playbooks:
            assert (roles.ASSETS / f"{name}.md").is_file(), f"{role.id}: missing playbook {name}"
    assert roles.get_role("nope") is None


def test_verifier_brief_disproves_and_reviews():
    brief = role_or_fail("verifier").brief
    assert "DISPROVE" in brief
    assert "action=review stage=verifier" in brief


def test_role_index_lists_every_role():
    index = roles.render_role_index()
    assert all(f"{r.id} ({r.when})" in index for r in roles.ROLES)


def test_render_role_prompt_composition():
    role = role_or_fail("recon")
    prompt = roles.render_role_prompt(role, "Map the external hosts.", "target example.com; scope: example.com")
    assert prompt.startswith(role.brief)
    assert "load_playbook surface=<name>" in prompt
    assert "misconfig-errors, external-exposure" in prompt
    assert "Your engagement: target example.com; scope: example.com" in prompt
    assert "Map the external hosts." in prompt
    assert "state_update" in prompt  # the shared-state discipline
    generic = roles.render_role_prompt(role_or_fail("generic"), "t", "target x")
    assert "load_playbook" not in generic  # a role without playbooks gets no load line


# -- dispatch tool ----------------------------------------------------------------------


@dataclass
class FakeSession:
    final_text: str = "found X"
    fail: bool = False
    store: EngagementStore | None = None
    add_vuln: bool = False
    gate: asyncio.Event | None = None
    prompts: list[str] = field(default_factory=list)

    async def run(self, prompt: str) -> RunOutcome:
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("model exploded")
        if self.add_vuln:
            assert self.store is not None
            self.store.add_vuln({"host": "example.com", "title": "sql injection", "severity": "high"})
        if self.gate is not None:
            await self.gate.wait()
        return RunOutcome(reason="completed", detail=None, turns=1, final_text=self.final_text)


class FakeFactory:
    """session_factory double: hands out queued sessions (in order) or plain fakes."""

    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []
        self._queued: list[FakeSession] = []

    async def __call__(self, role: str, sink: EventSink) -> FakeSession:
        session = self._queued.pop(0) if self._queued else FakeSession()
        self.sessions.append(session)
        return session

    def queue(self, *sessions: FakeSession) -> None:
        self._queued.extend(sessions)


@pytest.fixture
def tracker():
    fresh = dispatch.AgentTracker()
    saved = dispatch.TRACKER
    dispatch.TRACKER = fresh
    try:
        yield fresh
    finally:
        dispatch.TRACKER = saved


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path, executor=None, abort=asyncio.Event(), max_output_chars=1000)  # type: ignore[arg-type]


def dargs(tool: DispatchTool, **kw: Any) -> Any:
    return tool.Args(**kw)  # type: ignore[attr-defined]


async def test_dispatch_sends_role_prompt_and_tracks_done(tmp_path: Path, tracker, ctx) -> None:
    store = make_store(tmp_path)
    factory = FakeFactory()
    tool = DispatchTool(store, factory)
    out = await tool.run(dargs(tool, role="recon", task="enumerate hosts"), ctx)
    assert out.status == "ok"
    assert "found X" in out.content
    assert f"transcript: {store.logs_dir / 'specialists' / 'recon-1.jsonl'}" in out.content
    session = factory.sessions[0]
    assert len(session.prompts) == 1
    assert session.prompts[0].startswith(role_or_fail("recon").brief)
    assert "enumerate hosts" in session.prompts[0]
    assert "target example.com" in session.prompts[0]
    (run,) = tracker.snapshot()
    assert run.status == "done" and run.role == "recon" and run.ended_at is not None


async def test_dispatch_failure_is_data_not_exception(tmp_path: Path, tracker, ctx) -> None:
    store = make_store(tmp_path)
    factory = FakeFactory()
    factory.queue(FakeSession(fail=True), FakeSession(final_text="all good"))
    tool = DispatchTool(store, factory)
    bad = await tool.run(dargs(tool, role="webapp", task="t1"), ctx)
    assert bad.status == "error"
    assert "failed" in bad.content
    assert tracker.snapshot()[0].status == "failed"
    good = await tool.run(dargs(tool, role="webapp", task="t2"), ctx)
    assert good.status == "ok"  # a failed specialist does not break the next dispatch
    assert "all good" in good.content
    assert tracker.snapshot()[1].status == "done"


async def test_ninth_concurrent_dispatch_refused(tmp_path: Path, tracker, ctx) -> None:
    store = make_store(tmp_path)
    factory = FakeFactory()
    gates = [asyncio.Event() for _ in range(MAX_CONCURRENT)]
    factory.queue(*(FakeSession(gate=gate) for gate in gates))
    tool = DispatchTool(store, factory)
    tasks = [asyncio.create_task(tool.run(dargs(tool, role="generic", task="hold"), ctx)) for _ in gates]
    await asyncio.sleep(0.05)
    assert await tracker.running_count() == MAX_CONCURRENT
    refused = await tool.run(dargs(tool, role="generic", task="one too many"), ctx)
    assert refused.status == "error"
    assert f"{MAX_CONCURRENT} specialists are already running" in refused.content
    for gate in gates:
        gate.set()
    results = await asyncio.gather(*tasks)
    assert all(r.status == "ok" for r in results)
    assert await tracker.running_count() == 0


async def test_findings_counter_follows_store_changes(tmp_path: Path, tracker, ctx) -> None:
    store = make_store(tmp_path)
    factory = FakeFactory()
    factory.queue(FakeSession(store=store, add_vuln=True))
    tool = DispatchTool(store, factory)
    out = await tool.run(dargs(tool, role="exploit", task="prove it"), ctx)
    assert out.status == "ok"
    (run,) = tracker.snapshot()
    assert run.findings == 1
    assert len(store.state.vulns) == 1


async def test_tracker_tool_tap_shows_current_tool(tracker) -> None:
    run = await tracker.register("recon", "recon#1", None)
    assert run.id == "1.1" and run.label == "recon#1"
    tracker.tool_tap(run.id, "bash", "nmap -sV example.com")
    (snap,) = tracker.snapshot()
    assert snap.tool is not None and snap.tool.name == "bash"
    assert "nmap" in snap.tool.detail
    tracker.tool_done(run.id)
    assert tracker.snapshot()[0].tool is None
    await tracker.finish(run)
    assert tracker.snapshot()[0].status == "done"
    assert tracker.find(run.id) is run and tracker.find("9.9") is None
