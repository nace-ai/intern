"""The operator-lifecycle layer: notifications, the completion gates, the budget, the unattended
supervisor loop, and the retest kickoff."""

from __future__ import annotations

import asyncio

from intern.engagement.models import EvidenceRecord, Observation, Service
from intern.engagement.owasp import coverage_categories
from intern.engagement.pivot import open_opportunities
from intern.engagement.retest import retest_kickoff
from intern.engagement.runner import (
    Budget,
    follow_up_prompt,
    gates,
    kickoff_prompt,
    progress_mark,
    run_engagement,
    wrap_gates,
)
from intern.engagement.store import EngagementStore
from intern.hooks import RunOutcome

# -- notify ------------------------------------------------------------------------------


def _notifier(tmp_path, *, platform="darwin", env=None, run=None, post=None, has=None):
    from intern.engagement.notify import Notifier

    return Notifier(
        platform=platform,
        has=has or (lambda cmd: True),
        run=run or (lambda cmd, args: None),
        post=post or (lambda url, payload: None),
        env=env if env is not None else {},
    )


def test_notify_macos_osascript():
    calls = []
    n = _notifier(tmp_path=None, platform="darwin", run=lambda cmd, args: calls.append((cmd, args)))
    used = n.run_needs_you("an approval is waiting", title="intern · target")
    assert "desktop" in used and "bell" in used
    cmd, args = calls[0]
    assert cmd == "osascript"
    assert args[0] == "-e"
    assert 'display notification "Needs you: an approval is waiting"' in args[1]
    assert 'with title "intern · target"' in args[1]


def test_notify_linux_notify_send():
    calls = []
    n = _notifier(tmp_path=None, platform="linux", run=lambda cmd, args: calls.append((cmd, args)))
    used = n.run_needs_you("question")
    assert "desktop" in used
    cmd, args = calls[0]
    assert cmd == "notify-send"
    assert args == ["--app-name=intern", "--", "intern", "Needs you: question"]


def test_notify_no_desktop_command():
    n = _notifier(tmp_path=None, platform="linux", has=lambda cmd: False)
    assert "desktop" not in n.run_needs_you("question")


def test_notify_silenced():
    n = _notifier(tmp_path=None, env={"INTERN_NOTIFY": "0"})
    assert n.run_needs_you("question") == []
    assert n.run_ended("target", 3600.0) == []


def test_notify_webhook_posts_text():
    posts = []
    n = _notifier(
        tmp_path=None,
        platform="vague",
        post=lambda url, payload: posts.append((url, payload)),
        env={"INTERN_NOTIFY_URL": "https://hooks.example/incoming/x"},
    )
    used = n.run_needs_you("the proxy is down")
    assert "webhook" in used
    url, payload = posts[0]
    assert url == "https://hooks.example/incoming/x"
    assert "the proxy is down" in payload["text"]


def test_notify_webhook_ignores_non_http(monkeypatch):
    import intern.engagement.notify as notify_mod

    posts = []
    monkeypatch.setattr(notify_mod.httpx, "post", lambda *a, **k: posts.append((a, k)))
    notify_mod._thread_post("ftp://nope/x", {"text": "hi"})
    assert posts and posts[0][1]["json"] == {"text": "hi"}
    assert posts[0][1]["timeout"] == notify_mod.WEBHOOK_TIMEOUT_S == 5.0


def test_notify_webhook_never_raises(monkeypatch):
    import intern.engagement.notify as notify_mod

    def boom(*a, **k):
        raise OSError("unreachable")

    monkeypatch.setattr(notify_mod.httpx, "post", boom)
    notify_mod._thread_post("https://hooks.example/x", {"text": "hi"})  # must not raise


def test_notify_duration_threshold():
    posts = []
    n = _notifier(
        tmp_path=None,
        platform="vague",
        post=lambda url, payload: posts.append(payload),
        env={"INTERN_NOTIFY_AFTER": "10", "INTERN_NOTIFY_URL": "https://hooks.example/x"},
    )
    assert n.run_ended("target", 5.0) == []
    used = n.run_ended("target", 20.0)
    assert used and "target" in posts[0]["text"]


# -- store helper ------------------------------------------------------------------------


def make_store(tmp_path) -> EngagementStore:
    return EngagementStore(tmp_path / "out", "example-com", "example.com", scope=["example.com"])


def all_category_ids() -> list[str]:
    return [c.id for c in coverage_categories(False)]


def add_confirmed(store: EngagementStore, *, severity="medium", title="Reflected XSS in search", stopped=True) -> str:
    fields: dict[str, object] = {
        "host": "example.com",
        "title": title,
        "severity": severity,
        "status": "confirmed",
        "confidence": 0.95,
    }
    if stopped:
        fields["stop_reason"] = "at depth for this class"
        fields["stop_kind"] = "rules"
    v = store.add_vuln(fields)
    v.confidence = 0.95
    store.save()
    return v.id


# -- gates -------------------------------------------------------------------------------


def test_gates_list_each_open_gate(tmp_path):
    store = make_store(tmp_path)
    add_confirmed(store, stopped=False)
    store.add_host("10.0.0.5", services=[Service(port=22, name="ssh")])
    store.add_cred(username="admin")
    ids = [g.id for g in gates(store)]
    assert "coverage" in ids and "depth" in ids and "pivots" in ids
    coverage = next(g for g in gates(store) if g.id == "coverage")
    assert coverage.items and "A01" in coverage.items[0]


def test_gates_pass_when_all_resolved(tmp_path):
    store = make_store(tmp_path)
    vid = add_confirmed(store)
    store.mark_coverage(all_category_ids(), status="tested", note="all categories exercised")
    for opp in open_opportunities(store.state):
        store.mark_pivot(opp.id, status="skipped")
    store.recheck_vuln(vid, remediated=False)
    store.state.evidence.append(
        EvidenceRecord(file="evidence/x.png", name="x.png", description="proof", vuln_ids=[vid], ts=1)
    )
    store.update_report(
        synopsis="s",
        headline="h",
        summary="s",
        worked_well="w",
        coverage_notes="c",
        observations=[Observation(title="o", detail="d")],
    )
    assert gates(store) == []
    assert wrap_gates(store) == []


def test_progress_mark_changes_on_new_vuln(tmp_path):
    store = make_store(tmp_path)
    before = progress_mark(store)
    add_confirmed(store)
    assert progress_mark(store) != before


# -- budget ------------------------------------------------------------------------------


def test_budget_wraps_at_85_and_stops_at_100():
    now = {"t": 0.0}
    budget = Budget(started=0.0, max_minutes=10.0, clock=lambda: now["t"])
    assert budget.phase() == "explore"
    now["t"] = 10 * 60 * 0.85
    assert budget.phase() == "wrap"
    assert "85% of the 10.0-minute budget used" in budget.reason()
    now["t"] = 10 * 60 * 0.999
    assert budget.phase() == "wrap"
    now["t"] = 10 * 60
    assert budget.phase() == "stop"


def test_budget_cost_fraction():
    budget = Budget(started=0.0, max_minutes=10.0, max_cost=2.0, clock=lambda: 0.0)
    budget.cost = 1.8
    assert budget.phase() == "wrap"
    assert "$2.0" in budget.reason() or "$2" in budget.reason()


# -- supervisor --------------------------------------------------------------------------


class FakeSession:
    def __init__(self, store: EngagementStore, script=()):
        self.store = store
        self.script = list(script)
        self.prompts: list[str] = []

    async def run(self, prompt: str) -> RunOutcome:
        self.prompts.append(prompt)
        if self.script:
            self.script.pop(0)(self.store)
        return RunOutcome(reason="completed", detail=None, turns=1, final_text="ok")


def _add_finding(store: EngagementStore) -> None:
    add_confirmed(store)


def _close_out(store: EngagementStore) -> None:
    vid = store.state.vulns[0].id
    store.state.evidence.append(
        EvidenceRecord(file="evidence/x.png", name="x.png", description="proof", vuln_ids=[vid], ts=1)
    )
    for opp in open_opportunities(store.state):
        store.mark_pivot(opp.id, status="skipped")
    store.mark_coverage(all_category_ids(), status="tested", note="all categories exercised")

    store.recheck_vuln(vid, remediated=False)
    store.update_report(
        synopsis="s",
        headline="h",
        summary="s",
        worked_well="w",
        coverage_notes="c",
        observations=[Observation(title="o", detail="d")],
    )


def _drive(store: EngagementStore, session: FakeSession, **kw):
    records: list[dict] = []
    code = asyncio.run(
        run_engagement(
            None,
            store,
            lambda: session,
            target="example.com",
            scope=["example.com"],
            objective="test the target",
            emit_log=records.append,
            **kw,
        )
    )
    return code, records


def test_supervisor_exit_zero_when_gates_pass(tmp_path):
    store = make_store(tmp_path)
    session = FakeSession(store, script=[_add_finding, _close_out])
    code, _records = _drive(store, session)
    assert code == 0
    assert session.prompts[0].startswith("# Engagement")
    assert "set_engagement" in session.prompts[0]
    assert any("isn't finished" in p for p in session.prompts[1:])


def test_supervisor_exit_two_without_progress(tmp_path):
    store = make_store(tmp_path)
    session = FakeSession(store)  # does nothing
    code, records = _drive(store, session, max_rounds=12)
    assert code == 2
    assert any(p.startswith("Wrap up now") for p in session.prompts)
    assert records[-1]["type"] == "run_end" and records[-1]["detail"]["stop"] == "no-progress"


def test_supervisor_exit_three_on_fail_on(tmp_path):
    store = make_store(tmp_path)
    session = FakeSession(store, script=[_add_finding, _close_out])
    code, _ = _drive(store, session, fail_on="info")
    assert code == 3


def test_supervisor_exit_three_with_confirmed_high(tmp_path):
    store = make_store(tmp_path)

    def resolve_high(store: EngagementStore) -> None:
        vid = add_confirmed(store, severity="high", title="SQL injection in login")
        store.mark_coverage(all_category_ids(), status="tested", note="done")
        store.state.evidence.append(
            EvidenceRecord(file="evidence/y.png", name="y.png", description="proof", vuln_ids=[vid], ts=1)
        )
        store.recheck_vuln(vid, remediated=False)
        store.update_report(
            synopsis="s",
            headline="h",
            summary="s",
            worked_well="w",
            coverage_notes="c",
            observations=[Observation(title="o", detail="d")],
        )

    session = FakeSession(store, script=[resolve_high])
    code, _ = _drive(store, session, fail_on="high")
    assert code == 3


def test_supervisor_summary_and_events_shapes(tmp_path):
    store = make_store(tmp_path)
    session = FakeSession(store, script=[_add_finding, _close_out])
    code, records = _drive(store, session)
    types = [r["type"] for r in records]
    assert types[0] == "run_start" and "run_end" in types and "summary" in types
    for record in records:
        assert set(record) == {"ts", "type", "detail"}
        assert "T" in record["ts"]  # ISO
    summary = next(r for r in records if r["type"] == "summary")["detail"]
    assert summary["target"] == "example.com"
    assert summary["exit"] == code == 0
    assert summary["gates_passed"] is True
    assert summary["findings"]["confirmed"] == 1
    assert summary["findings"]["by_severity"]["medium"] == 1
    assert summary["coverage"]["accounted"] == summary["coverage"]["total"]
    assert summary["open_pivots"] == 0


def test_supervisor_error_rounds(tmp_path):
    store = make_store(tmp_path)

    class Failing(FakeSession):
        async def run(self, prompt):
            self.prompts.append(prompt)
            return RunOutcome(reason="provider_error", detail="boom", turns=0, final_text="")

    code, records = _drive(store, Failing(store))
    assert code == 1
    assert next(r for r in records if r["type"] == "summary")["detail"]["stop"] == "error"


def test_follow_up_prompt_names_gates_and_approval():
    from intern.engagement.runner import Gate

    prompt = follow_up_prompt(
        [Gate("coverage", "2 categories untested", ("A01 Broken Access Control", "A02 Cryptographic Failures"))],
        3,
        frozenset({"cred-reuse"}),
    )
    assert "Round 3" in prompt
    assert "A01 Broken Access Control" in prompt
    assert "cred-reuse" in prompt


def test_kickoff_prompt_names_approved():
    prompt = kickoff_prompt("t", ["t"], "obj", frozenset({"port-scan"}))
    assert "Pre-approved for this run: port-scan" in prompt


# -- retest ------------------------------------------------------------------------------


def test_retest_kickoff_lists_confirmed_findings(tmp_path):
    store = make_store(tmp_path)
    vid = add_confirmed(store, severity="high", title="SQL injection in login")
    text = retest_kickoff(store)
    assert store.state.retest is not None
    assert f"{vid} [high] SQL injection in login — example.com" in text
    assert "action=retest" in text
    assert "retest_result=fixed|not_fixed|" in text.replace("\n", "")
    assert "remediation-verification" in text
