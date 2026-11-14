"""Benchmark control flow with fake clients and a temporary result store."""

import argparse
import asyncio
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

import benchmark_decisions
from benchmark_decisions import resolve_plan, run_benchmark, selected_cases
from prompts import input_hash, shared_input_for_case
from schemas import DecisionResult
from storage import ResultStore

ROOT = Path(__file__).resolve().parents[1]


def _bind_store(tmp_path):
    class Bound(ResultStore):
        def __init__(self, root=None):
            super().__init__(tmp_path)

    return Bound


def _records(tmp_path: Path, mode: str, track: str) -> list[dict]:
    path = tmp_path / mode / track / "records.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run_json(tmp_path: Path, mode: str, track: str) -> dict:
    runs = list((tmp_path / mode / track / "runs").iterdir())
    assert len(runs) == 1
    return json.loads((runs[0] / "run.json").read_text(encoding="utf-8"))


def test_mock_run_is_deterministic_hashes_match_and_stays_out_of_live(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark_decisions, "ResultStore", _bind_store(tmp_path))
    asyncio.run(run_benchmark(True, 3, "dev"))
    other = tmp_path / "second"
    monkeypatch.setattr(benchmark_decisions, "ResultStore", _bind_store(other))
    asyncio.run(run_benchmark(True, 3, "dev"))

    first = _run_json(tmp_path, "mock", "decisions")
    second = _run_json(other, "mock", "decisions")
    expected = [case["case_id"] for case in selected_cases(benchmark_decisions.load_cases("dev"), 3, "dev")]
    assert first["case_ids"] == second["case_ids"] == expected
    assert first["measurements"] == "simulated"
    rows = _records(tmp_path, "mock", "decisions")
    assert len(rows) == 6
    by_case: dict[str, list[dict]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], []).append(row)
    cases = {case["case_id"]: case for case in benchmark_decisions.load_cases("dev")}
    assert set(by_case) == set(expected)
    for case_id, pair in by_case.items():
        assert len(pair) == 1 or len(pair) == 2
        assert {item["provider"] for item in pair} == {"drex", "openrouter"}
        shared = input_hash(shared_input_for_case(cases[case_id]))
        assert pair[0]["input_hash"] == pair[1]["input_hash"] == shared
        assert all(item["mode"] == "mock" and item["cost_usd"] is None for item in pair)
    assert not (tmp_path / "live").exists()
    assert not (tmp_path / "mock" / "hard_cases").exists()


def test_live_budget_stops_after_one_attempt_per_provider(tmp_path, monkeypatch):
    calls: list[str] = []

    class Fake:
        def __init__(self, provider: str) -> None:
            self.provider = provider
            self.output_field = "max_tokens"
            self.max_tokens = 16
            self.usd_per_input_token = Decimal("0.00000003")

        async def decide(self, request):
            calls.append(self.provider)
            return DecisionResult.model_validate(
                {
                    "case_id": request.case_id,
                    "provider": self.provider,
                    "status": "ok",
                    "verdict": "CLEAR",
                    "latency_ms": 12,
                    "cost_usd": "0.03",
                    "input_hash": request.input_hash,
                    "timestamp": "2026-11-14T00:00:00+00:00",
                    "mode": "live",
                }
            )

    async def prepare(_http):
        return {
            "drex_price_confirmed": True,
            "pricing_provenance": "test",
            "openrouter_model": {},
            "openrouter_output_field": "max_tokens",
            "openrouter_output_limit": 16,
        }

    monkeypatch.setattr(benchmark_decisions, "ResultStore", _bind_store(tmp_path))
    monkeypatch.setattr(benchmark_decisions, "prepare_live", prepare)
    monkeypatch.setattr(benchmark_decisions, "DrexClient", lambda http, api_key=None, model=None: Fake("drex"))
    monkeypatch.setattr(benchmark_decisions, "OpenRouterClient", lambda http, api_key=None, model=None: Fake("openrouter"))
    monkeypatch.setattr(benchmark_decisions, "DREX_BUDGET_USD", Decimal("0.04"))
    monkeypatch.setattr(benchmark_decisions, "DREX_RESERVE", Decimal("0.03"))
    monkeypatch.setattr(benchmark_decisions, "OPENROUTER_BUDGET_USD", Decimal("5"))
    monkeypatch.setattr(benchmark_decisions, "OPENROUTER_RESERVE", Decimal("0.01"))
    asyncio.run(run_benchmark(False, 3, "hard"))
    rows = _records(tmp_path, "live", "hard_cases")
    assert calls == ["drex", "openrouter"]
    assert [row["case_id"] for row in rows] == [benchmark_decisions.load_cases("hard")[0]["case_id"]] * 2
    metadata = _run_json(tmp_path, "live", "hard_cases")
    assert metadata["live_opt_in"] is True
    assert metadata["published_benchmark"] is False
    assert metadata["case_ids"][0] == "HRD-001"
    assert not (tmp_path / "live" / "frozen_settings.json").exists()
    assert not (tmp_path / "live" / "decisions").exists()
    assert not (tmp_path / "mock").exists()


def test_hard_mock_run_keeps_pair_order_and_does_not_touch_published_files(tmp_path, monkeypatch):
    published = {
        name: (ROOT / "data" / name).read_bytes()
        for name in ("dev_20.json", "test_100.json", "split_manifest.json", "hard_cases.json", "hard_manifest.json")
    }
    monkeypatch.setattr(benchmark_decisions, "ResultStore", _bind_store(tmp_path))
    asyncio.run(run_benchmark(True, 34, "hard"))
    metadata = _run_json(tmp_path, "mock", "hard_cases")
    assert metadata["measurements"] == "simulated"
    assert metadata["case_ids"] == [case["case_id"] for case in benchmark_decisions.load_cases("hard")]
    assert metadata["case_ids"][:2] == ["HRD-001", "HRD-002"]
    rows = _records(tmp_path, "mock", "hard_cases")
    assert len(rows) == 68
    assert not (tmp_path / "mock" / "decisions").exists()
    for name, payload in published.items():
        assert (ROOT / "data" / name).read_bytes() == payload


def test_hard_live_run_requires_an_explicit_opt_in():
    with pytest.raises(SystemExit, match="confirm-live"):
        resolve_plan(argparse.Namespace(mock=False, pilot=None, full=None, hard=True, confirm_live=False))
    mock, limit, split = resolve_plan(argparse.Namespace(mock=True, pilot=None, full=None, hard=True, confirm_live=False))
    assert (mock, split) == (True, "hard")
    assert limit == 34
    with pytest.raises(SystemExit, match="choose either"):
        resolve_plan(argparse.Namespace(mock=True, pilot=None, full=100, hard=True, confirm_live=False))
    completed = subprocess.run(
        [sys.executable, "benchmark_decisions.py", "--hard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode != 0
    assert "confirm-live" in completed.stderr + completed.stdout


def test_development_plan_is_unchanged():
    mock, limit, split = resolve_plan(argparse.Namespace(mock=False, pilot=None, full=None, hard=False, confirm_live=False))
    assert (mock, limit, split) == (False, 5, "dev")
    full = resolve_plan(argparse.Namespace(mock=True, pilot=None, full=100, hard=False, confirm_live=False))
    assert full == (True, 100, "test")
