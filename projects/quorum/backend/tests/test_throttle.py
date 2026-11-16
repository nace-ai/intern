"""Throttle, rate cap and refusal breaker in nace.py; all-failed stop in the arena. No network."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app import arena, config, nace

Q = {"q1": {"type": "noul", "instructions": "Is it paid?"}}


class FakeError(Exception):
    def __init__(self, status: int | None, body: Any = None) -> None:
        super().__init__(f"status {status}")
        self.status = status
        self.body = body


class FakeClient:
    def __init__(self, delay: float = 0.0, errors: list[Exception | None] | None = None) -> None:
        self.delay = delay
        self.errors = list(errors or [])
        self.calls = 0
        self.inflight = 0
        self.peak = 0

    async def system_one(self, state: Any, questions: Any, model: Any = None) -> Any:
        self.calls += 1
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            err = self.errors.pop(0) if self.errors else None
            if err is not None:
                raise err
            return SimpleNamespace(usage=None, model="m", answers={},
                                   evaluation_time_ms=1, request_id="r")
        finally:
            self.inflight -= 1


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "USAGE_FILE", tmp_path / "usage.json")
    monkeypatch.setattr(nace, "_client", None)
    nace.reset_throttle()
    yield
    nace.reset_throttle()


def _use(monkeypatch, client: FakeClient) -> FakeClient:
    monkeypatch.setattr(nace, "_client", client)
    return client


def test_concurrency_never_exceeds_limit(monkeypatch):
    client = _use(monkeypatch, FakeClient(delay=0.01))

    async def run() -> None:
        await asyncio.gather(*(nace.drex({}, Q) for _ in range(40)))

    asyncio.run(run())
    assert client.calls == 40
    assert client.peak <= config.CONCURRENCY
    assert nace.drex_inflight_max() == nace.DREX_INFLIGHT_MAX <= config.CONCURRENCY
    assert nace.DREX_INFLIGHT_MAX == config.CONCURRENCY
    # semaphores are per loop: a second loop still works
    asyncio.run(nace.drex({}, Q))
    assert client.calls == 41


def test_rate_cap_delays_call_101(monkeypatch):
    client = _use(monkeypatch, FakeClient())
    monkeypatch.setattr(nace, "DREX_WINDOW_S", 0.3)
    assert nace.DREX_PER_MINUTE == 100

    async def run() -> float:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await asyncio.gather(*(nace.drex({}, Q) for _ in range(100)))
        assert loop.time() - t0 < 0.25
        await nace.drex({}, Q)
        return loop.time() - t0

    elapsed = asyncio.run(run())
    assert client.calls == 101
    assert elapsed >= 0.29


def test_three_403_open_breaker(monkeypatch):
    client = _use(monkeypatch, FakeClient(errors=[FakeError(403)] * 3))

    async def run() -> None:
        for _ in range(3):
            with pytest.raises(FakeError):
                await nace.drex({}, Q)
        with pytest.raises(nace.NaceBlocked):
            await nace.drex({}, Q)

    asyncio.run(run())
    assert client.calls == 3  # fourth call sent nothing, no retries


def test_breaker_reopens_after_block_time(monkeypatch):
    client = _use(monkeypatch, FakeClient(errors=[FakeError(429)] * 3))
    now = [1000.0]
    monkeypatch.setattr(nace, "_now", lambda: now[0])

    async def run() -> None:
        for _ in range(3):
            with pytest.raises(FakeError):
                await nace.drex({}, Q)
        now[0] += nace.BREAKER_BLOCK_S - 1
        with pytest.raises(nace.NaceBlocked):
            await nace.drex({}, Q)
        now[0] += 2
        await nace.drex({}, Q)

    asyncio.run(run())
    assert client.calls == 4


def test_success_resets_count(monkeypatch):
    errs: list[Exception | None] = [FakeError(403), FakeError(403), None,
                                    FakeError(403), FakeError(403), None]
    client = _use(monkeypatch, FakeClient(errors=errs))

    async def run() -> None:
        for e in errs:
            if e is None:
                await nace.drex({}, Q)
            else:
                with pytest.raises(FakeError):
                    await nace.drex({}, Q)

    asyncio.run(run())
    assert client.calls == 6


def test_html_body_counts_as_refusal(monkeypatch):
    html = "  <!DOCTYPE html><title>Vercel Security Checkpoint</title>"
    client = _use(monkeypatch, FakeClient(errors=[FakeError(200, html), FakeError(None, "<HTML>"),
                                                  FakeError(500, html)]))

    async def run() -> None:
        for _ in range(3):
            with pytest.raises(FakeError):
                await nace.drex({}, Q)
        with pytest.raises(nace.NaceBlocked):
            await nace.drex({}, Q)

    asyncio.run(run())
    assert client.calls == 3


def test_is_refusal():
    assert nace.is_refusal(FakeError(403))
    assert nace.is_refusal(FakeError(429))
    assert nace.is_refusal(FakeError(502, "<html><body>x</body></html>"))
    assert not nace.is_refusal(FakeError(500, "server error"))
    assert not nace.is_refusal(FakeError(400, {"error": "bad"}))
    assert not nace.is_refusal(ValueError("x"))


def test_non_refusal_errors_do_not_open_breaker(monkeypatch):
    client = _use(monkeypatch, FakeClient(errors=[FakeError(500)] * 4))

    async def run() -> None:
        for _ in range(4):
            with pytest.raises(FakeError):
                await nace.drex({}, Q)

    asyncio.run(run())
    assert client.calls == 4


def test_all_failed():
    ok = {"decisions": {"a": {"error": None}, "b": {"error": "x"}}}
    bad = {"decisions": {"a": {"error": "NaceBlocked"}, "b": {"error": "403"}}}
    assert arena.all_failed([bad, bad])
    assert not arena.all_failed([bad, ok])
    assert not arena.all_failed([])


def _round_rec(r: int, failed: bool) -> dict[str, Any]:
    return {"round": r, "mode": "redirect", "attacker": {},
            "defenders": {"drex": {"raw_breaches": 0, "breaches": 0, "false_alarms": 0,
                                   "failures": 5, "min_risk": None}},
            "all_failed": failed}


def _run_arena_module():
    sys.path.insert(0, str(Path(config.ROOT / "backend" / "scripts")))
    import run_arena

    return run_arena


def test_arena_stops_after_all_failed_round(monkeypatch, capsys):
    run_arena = _run_arena_module()
    calls: list[int] = []

    async def fake_run_round(r, *a, **k):
        calls.append(r)
        return _round_rec(r, failed=True)

    monkeypatch.setattr(arena, "run_round", fake_run_round)
    monkeypatch.setattr(arena, "next_round", lambda: 4)
    asyncio.run(run_arena.main(["--rounds", "3"]))
    out = capsys.readouterr().out
    assert calls == [4]
    assert "STOPPED: all requests failed in round 4 (API refusing?)" in out
    assert "usage:" not in out


def test_arena_continues_when_round_ok(monkeypatch, capsys):
    run_arena = _run_arena_module()
    calls: list[int] = []

    async def fake_run_round(r, *a, **k):
        calls.append(r)
        return _round_rec(r, failed=False)

    monkeypatch.setattr(arena, "run_round", fake_run_round)
    monkeypatch.setattr(arena, "next_round", lambda: 1)
    monkeypatch.setattr(run_arena, "should_enable_risk", lambda rec, w: False)
    asyncio.run(run_arena.main(["--rounds", "3"]))
    assert calls == [1, 2, 3]
    assert "STOPPED" not in capsys.readouterr().out
