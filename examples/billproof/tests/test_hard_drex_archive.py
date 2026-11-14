"""Compact Drex logs: one question line, case-level cost, scorer fallback."""
import gzip
import json

from billproof.bench.hard import drex_archive, metrics, runner
from billproof.bench.metrics import brier


def _rich(case_id: str, latency: float, cost: float, yes_conf: float, no_conf: float) -> dict:
    return {
        "case_id": case_id,
        "split": "test",
        "lane": "drex-v1",
        "model": "drex-max-v1.1",
        "simulated": False,
        "error": None,
        "latency_ms": latency,
        "cost_usd": cost,
        "input_tokens": 10,
        "output_tokens": 20,
        "request_id": "req-drop-me",
        "answers": {
            "dup_L1": {"choice": "yes", "confidence": yes_conf, "confidence_kind": "native",
                       "probabilities": {"yes": yes_conf, "no": 1 - yes_conf}, "invalid": False},
            "pr_L1": {"choice": "no", "confidence": no_conf, "confidence_kind": "native",
                      "probabilities": {"yes": 1 - no_conf, "no": no_conf}, "invalid": False},
        },
        "correct": {"dup_L1": True, "pr_L1": False},
        "invalid": {"dup_L1": False, "pr_L1": False},
        "gold": {"dup_L1": "yes", "pr_L1": "yes"},
    }


def _patch(monkeypatch, tmp_path):
    runs = tmp_path / "runs"
    data = tmp_path / "data"
    (data / "synthetic" / "hard").mkdir(parents=True)
    (data / "synthetic" / "hard" / "test.jsonl").write_text(
        json.dumps({"case_id": "C1", "gold": {"dup_L1": "yes", "pr_L1": "yes"}}) + "\n"
        + json.dumps({"case_id": "C2", "gold": {"dup_L1": "yes", "pr_L1": "yes"}}) + "\n"
    )
    monkeypatch.setattr(drex_archive, "RUNS_DIR", runs)
    monkeypatch.setattr(drex_archive, "FULL_DIR", runs / "full")
    monkeypatch.setattr(drex_archive, "DATA_DIR", data)
    monkeypatch.setattr(runner, "RUNS_DIR", runs)
    return runs


def test_compact_round_trip_keeps_case_cost_and_brier(tmp_path, monkeypatch):
    runs = _patch(monkeypatch, tmp_path)
    runs.mkdir()
    rich = runs / "hard_test_drex-v1.jsonl"
    first = _rich("C1", 10.0, 0.01, 0.9, 0.8)
    second = _rich("C2", 30.0, 0.02, 0.7, 0.6)
    rich.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n")

    path = drex_archive.write_compact("drex-v1")
    assert path.stat().st_size < 8000000
    with gzip.open(path, "rt") as handle:
        lines = [json.loads(line) for line in handle if line.strip()]
    assert [set(line) for line in lines] == [
        {"case_id", "question_id", "answer", "confidence", "correct", "latency_ms", "cost_usd"},
    ] * 4
    assert lines[0]["latency_ms"] == 10.0 and lines[1]["cost_usd"] == 0.01

    rows = drex_archive.expand_rows("drex-v1")
    correct, total, invalid = metrics.accuracy(rows)
    assert (correct, total, invalid) == (2, 4, 0)
    per_thousand = metrics.cost_per_1000(rows)
    assert per_thousand == 1000.0 * (0.01 + 0.02) / 2
    probs = [rows[0]["answers"]["dup_L1"]["probabilities"], rows[0]["answers"]["pr_L1"]["probabilities"]]
    labels = ["yes", "yes"]
    assert rows[0]["answers"]["dup_L1"]["confidence_kind"] == "native"
    assert brier(probs, labels) == brier(
        [{"yes": 0.9, "no": 0.1}, {"yes": 0.2, "no": 0.8}],
        labels,
    )

    rich.unlink()
    restored = metrics.read_jsonl(drex_archive.full_path("drex-v1"))
    assert [row["case_id"] for row in restored] == ["C1", "C2"]
    assert "request_id" not in restored[0]
    assert runner.done_ids(drex_archive.full_path("drex-v1")) == {"C1", "C2"}
    costs = [float(row["cost_usd"]) for row in runner._iter_rows() if row.get("lane") == "drex-v1"]
    assert costs == [0.01, 0.02]


def test_jsonl_path_prefers_full_and_falls_back(tmp_path, monkeypatch):
    runs = _patch(monkeypatch, tmp_path)
    assert runner.jsonl_path("test", "drex-v1") == runs / "full" / "hard_test_drex-v1.jsonl"
    legacy = runs / "hard_test_drex-v1.jsonl"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("")
    assert runner.jsonl_path("test", "drex-v1") == legacy
    full = runs / "full" / "hard_test_drex-v1.jsonl"
    full.parent.mkdir(parents=True)
    full.write_text("")
    assert runner.jsonl_path("test", "drex-v1") == full
    assert runner.jsonl_path("dev", "drex-v1") == runs / "hard_dev_drex-v1.jsonl"


def test_expand_does_not_replace_a_rich_log(tmp_path, monkeypatch):
    runs = _patch(monkeypatch, tmp_path)
    runs.mkdir()
    (runs / "hard_test_drex-v1.jsonl").write_text(json.dumps(_rich("C1", 10.0, 0.01, 0.9, 0.8)) + "\n")
    drex_archive.write_compact("drex-v1")
    target = runs / "full" / "hard_test_drex-v1.jsonl"
    target.parent.mkdir()
    target.write_text("keep\n")
    assert drex_archive.expand_file("drex-v1") == target
    assert target.read_text() == "keep\n"


def test_committed_gzip_reproduces_bench():
    v1 = drex_archive.compact_path("drex-v1")
    finance = drex_archive.compact_path("drex-finance")
    if not v1.exists() or not finance.exists():
        import pytest
        pytest.skip("compact Drex archives are not in this checkout")
    drex_archive.assert_published()
