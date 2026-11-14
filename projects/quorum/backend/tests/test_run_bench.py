"""Tests for backend/scripts/run_bench.py. No network: every call is faked."""

from __future__ import annotations

import builtins
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from backend.app.contracts import Decision, DecisionUsage, ParsedInvoice

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("run_bench", ROOT / "backend/scripts/run_bench.py")
rb = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(rb)

# case_id, scenario, expected, forged, split
CASES = [
    ("d-1", "S1", "approve", False, "dev"),
    ("t-1", "S1", "approve", False, "test"),
    ("t-2", "S2", "block", True, "test"),
    ("t-3", "S3", "block", True, "test"),
    ("t-4", "S5", "block", True, "test"),
    ("t-5", "S4", "verify", False, "test"),
]

# Drex: (raw, final, top p, error)
DREX = {
    "d-1": ("approve", "approve", 0.9, None),
    "t-1": ("approve", "approve", 0.95, None),    # correct
    "t-2": ("approve", "block", 0.8, None),       # raw unsafe, final correct
    "t-3": ("verify", "verify", 0.5, None),       # low confidence -> hybrid uses llm
    "t-4": (None, None, 0.0, "TimeoutError"),     # failure
    "t-5": ("approve", "verify", 0.6, None),      # low confidence -> hybrid uses llm
}
LLM = {
    "d-1": ("approve", "approve", 0.9, None),
    "t-1": ("verify", "verify", 0.9, None),       # unnecessary escalation
    "t-2": ("block", "block", 0.9, None),
    "t-3": ("block", "block", 0.9, None),
    "t-4": ("approve", "block", 0.9, None),
    "t-5": ("approve", "approve", 0.9, None),     # raw and final wrong
}


def make_decision(system: str, case_id: str, spec: tuple, wall: int, tokens: int) -> Decision:
    raw, final, top, err = spec
    p = {} if err else {"approve": top, "verify": 1 - top, "block": 0.0}
    return Decision(
        decision_id=f"{system}-{case_id}", doc_id=case_id, system=system, model=f"{system}-model",
        raw_action=raw, final_action=final, p=p, wall_ms=wall,
        usage=DecisionUsage(input_tokens=tokens, output_tokens=10), error=err,
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    data = tmp_path / "data"
    data.mkdir()
    cases = data / "cases.jsonl"
    cases.write_text("".join(json.dumps({
        "case_id": c, "source_doc_id": c, "supplier_id": "cobalt", "scenario": s,
        "pdf_path": f"data/pdfs/{c}.pdf", "expected_action": e, "forged": f, "split": sp,
    }) + "\n" for c, s, e, f, sp in CASES))
    log: list[tuple[str, str]] = []

    async def fake_parse(path: Path, cache_dir: Any = None) -> ParsedInvoice:
        cid = Path(path).stem
        return ParsedInvoice(doc_id=cid, parse_ms=2000)

    async def fake_decide(parsed, facts, policy=(100, 5, 30), known_attacks=None, system="drex", model=None):
        log.append(("drex", parsed.doc_id))
        return make_decision("drex", parsed.doc_id, DREX[parsed.doc_id], 100, 1000)

    async def fake_llm(parsed, facts, policy=(100, 5, 30)):
        log.append(("llm_guard", parsed.doc_id))
        return make_decision("llm_guard", parsed.doc_id, LLM[parsed.doc_id], 1000, 2000)

    baseline = type(sys)("backend.app.baseline")
    baseline.decide_llm = fake_llm
    monkeypatch.setitem(sys.modules, "backend.app.baseline", baseline)
    import backend.app as app_pkg
    monkeypatch.setattr(app_pkg, "baseline", baseline, raising=False)
    monkeypatch.setattr(rb.intake, "parse_document", fake_parse)
    monkeypatch.setattr(rb.panel, "decide", fake_decide)
    monkeypatch.setattr(rb.checks, "load_trusted", lambda p: {})
    monkeypatch.setattr(rb.checks, "facts", lambda parsed, trusted: None)

    real_labels = rb.load_labels

    def spy_labels(cf, split):
        log.append(("labels", split))
        return real_labels(cf, split)

    monkeypatch.setattr(rb, "load_labels", spy_labels)
    return {"data": data, "cases": cases, "log": log, "backend": tmp_path}


async def run(env: dict[str, Any], **kw: Any) -> dict[str, Any]:
    kw.setdefault("warmup_n", 0)
    return await rb.bench("test", data_dir=env["data"], cases_file=env["cases"], backend_dir=env["backend"], **kw)


def go(env: dict[str, Any], **kw: Any) -> dict[str, Any]:
    import asyncio

    return asyncio.run(run(env, **kw))


def pick(row: dict[str, Any]) -> tuple:
    return (row["correct"], row["unsafe_approvals"], row["unnecessary_escalations"],
            row["s5_blocked"], row["s3_blocked"], row["failures"])


def test_hand_computed_metrics(env: dict[str, Any]) -> None:
    r = go(env)
    rows = r["rows"]
    # (correct, unsafe, unnecessary, s5 blocked, s3 blocked, failures), worked out by hand
    assert pick(rows["drex_alone"]) == (1, 1, 0, 0, 0, 1)
    assert pick(rows["drex_guard"]) == (3, 0, 1, 0, 0, 1)
    assert pick(rows["llm_alone"]) == (2, 1, 1, 0, 1, 0)
    assert pick(rows["llm_guard"]) == (3, 0, 1, 1, 1, 0)
    # t-4: Drex failed, so the hybrid fails too (it never falls back to the LLM guard)
    assert pick(rows["hybrid"]) == (3, 0, 0, 0, 1, 1)
    d = rows["drex_guard"]
    assert (d["n"], d["n_forged"], d["n_clean"], d["n_s5"], d["n_s3"]) == (5, 3, 2, 1, 1)
    assert d["correct_rate"] == pytest.approx(0.6)
    assert rows["drex_alone"]["unsafe_approval_rate"] == pytest.approx(1 / 3)
    assert d["unnecessary_escalation_rate"] == pytest.approx(0.5)
    assert d["per_scenario"]["S2"] == {"correct": 1, "n": 1}
    assert d["per_scenario"]["S5"] == {"correct": 0, "n": 1}
    assert r["meta"]["n_cases"] == 5  # dev case excluded
    assert r["meta"]["ndi_median_parse_s"] == pytest.approx(2.0)


def test_system_stats_costs_and_ratios(env: dict[str, Any]) -> None:
    (env["data"] / "llm_cost.jsonl").write_text("".join(
        json.dumps({"decision_id": f"llm_guard-{c}", "model": "m", "input_tokens": 2000,
                    "output_tokens": 10, "estimated_cost": "0.0003"}) + "\n"
        for c in LLM))
    r = go(env)
    d, l = r["systems"]["drex"], r["systems"]["llm_guard"]
    assert d["p50_wall_ms"] == 100 and l["p50_wall_ms"] == 1000
    assert d["mean_input_tokens"] == 1000 and l["mean_input_tokens"] == 2000
    assert Decimal(d["cost_per_1000"]) == Decimal("0.03")
    assert Decimal(l["cost_per_1000"]) == Decimal("0.3")
    assert r["ratios"]["llm_p50_over_drex_p50"] == pytest.approx(10.0)
    assert r["ratios"]["llm_cost_over_drex_cost"] == pytest.approx(10.0)


def test_unknown_llm_cost_is_na(env: dict[str, Any]) -> None:
    r = go(env)
    assert r["systems"]["llm_guard"]["cost_per_1000"] is None
    assert r["ratios"]["llm_cost_over_drex_cost"] is None
    assert "n/a" in (env["data"] / "results.md").read_text()


def test_failure_is_wrong_and_never_approval() -> None:
    labels = {"x": {"scenario": "S2", "expected_action": "block", "forged": True},
              "y": {"scenario": "S1", "expected_action": "approve", "forged": False}}
    m = rb.row_metrics({"x": None, "y": None}, labels)
    assert m["correct"] == 0 and m["failures"] == 2
    assert m["unsafe_approvals"] == 0          # not counted as approve
    assert m["unnecessary_escalations"] == 1   # clean case without approve
    failed = Decision(doc_id="x", system="drex", raw_action="approve", final_action="approve", error="boom")
    assert rb.action_of(failed, "raw") is None and rb.action_of(failed, "final") is None


def test_exception_becomes_failed_decision(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(parsed, facts, policy=(100, 5, 30), known_attacks=None, system="drex", model=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(rb.panel, "decide", broken)
    r = go(env, system="drex")
    row = r["rows"]["drex_guard"]
    assert row["failures"] == 5 and row["correct"] == 0 and row["unsafe_approvals"] == 0
    stored = json.loads((env["data"] / "decisions/drex/t-1.json").read_text())
    assert stored["error"] and stored["final_action"] is None


def test_labels_read_only_after_decisions_stored(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    log = env["log"]
    real_labels = rb.load_labels
    seen: dict[str, bool] = {}

    def check_labels(cf, split):
        seen["files"] = all((env["data"] / f"decisions/{s}/t-{i}.json").exists()
                            for s in ("drex", "llm_guard") for i in range(1, 6))
        return real_labels(cf, split)  # the fixture spy logs ("labels", split)

    monkeypatch.setattr(rb, "load_labels", check_labels)
    go(env, warmup_n=1)
    idx = [i for i, e in enumerate(log) if e[0] == "labels"]
    assert len(idx) == 1
    assert idx[0] == len(log) - 1  # every call happened before labels
    assert sum(1 for e in log if e[0] != "labels") == 12  # 10 reported + 1 warm-up each
    assert ("drex", "d-1") in log and ("llm_guard", "d-1") in log  # warm-up on dev
    assert seen["files"]
    assert not (env["data"] / "decisions/drex/d-1.json").exists()  # warm-up discarded


def test_reuse_makes_zero_calls(env: dict[str, Any]) -> None:
    first = go(env)
    env["log"].clear()
    again = go(env, reuse=True, warmup_n=5)
    assert [e for e in env["log"] if e[0] != "labels"] == []
    assert again["rows"] == first["rows"]
    assert again["systems"]["drex"]["decisions_per_s"] is not None
    assert again["meta"]["ndi_median_parse_s"] == pytest.approx(2.0)


def test_hybrid_routes_by_drex_confidence(env: dict[str, Any]) -> None:
    r = go(env)
    h = r["hybrid"]
    # t-3 (0.5) and t-5 (0.6) go to the LLM guard; t-4 (Drex failed) is a hybrid failure, not routed
    assert h["drex_alone_count"] == 2 and h["drex_alone_share"] == pytest.approx(0.4)
    assert h["drex_failed_count"] == 1 and h["routed_to_llm_count"] == 2 and h["n_failed"] == 1
    assert h["p50_wall_ms"] == 100  # successes: 100, 100, 1100, 1100

    def dec(top: float, final: str, system: str = "drex") -> Decision:
        return Decision(doc_id="a", system=system, raw_action=final, final_action=final,
                        p={"approve": top, "verify": 1 - top})

    drex = {"hi": dec(0.7, "approve"), "lo": dec(0.69, "approve")}
    llm = {"hi": dec(0.9, "block", "llm_guard"), "lo": dec(0.9, "block", "llm_guard")}
    acts, alone = rb.hybrid_actions(drex, llm, ["hi", "lo"], 0.7)
    assert acts == {"hi": "approve", "lo": "block"}
    assert alone == {"hi": True, "lo": False}


def test_missing_baseline_gives_unavailable_llm_rows(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.app as app_pkg

    monkeypatch.delattr(app_pkg, "baseline", raising=False)
    monkeypatch.setitem(sys.modules, "backend.app.baseline", None)  # import raises ImportError
    r = go(env)
    for key in ("llm_alone", "llm_guard", "hybrid"):
        assert r["rows"][key]["available"] is False
    assert r["systems"]["llm_guard"]["available"] is False
    assert r["hybrid"]["available"] is False
    assert pick(r["rows"]["drex_guard"]) == (3, 0, 1, 0, 0, 1)
    assert all(e[0] != "llm_guard" for e in env["log"])
    assert "unavailable" in (env["data"] / "results.md").read_text().lower()


def test_results_files_and_footnotes(env: dict[str, Any]) -> None:
    go(env)
    data = json.loads((env["data"] / "results.json").read_text())
    for k in ("started_at", "concurrency", "policy", "split", "n_cases"):
        assert k in data["meta"]
    md = (env["data"] / "results.md").read_text()
    assert "5 constructed scenarios on synthetic invoices (Company Documents v2, Apache-2.0). Not real fraud cases." in md
    assert "Decision stage only. Both systems received the same NDI output. NDI median parse time: 2.0 s" in md
    assert ("3. Drex cost is calculated from the configured price of $0.03 per million input tokens. "
            "LLM guard cost is the provider-estimated estimated_cost per call (an estimate, not a billed amount). "
            "Latency measured from our laptop on venue Wi-Fi.") in md
    assert "Cost at public list price" not in md
    assert "4. Development cases are excluded from these figures. 0 were used only as discarded warm-up calls." in md
    assert "Failures (not in the figures above):" in md


def test_load_inputs_strips_labels(env: dict[str, Any]) -> None:
    rows = rb.load_inputs(env["cases"], "test")
    assert [r["case_id"] for r in rows] == ["t-1", "t-2", "t-3", "t-4", "t-5"]
    assert all(k not in r for r in rows for k in rb.LABEL_KEYS)


def test_percentile_nearest_rank() -> None:
    assert rb.percentile([1, 2, 3, 4], 50) == 2
    assert rb.percentile(list(range(1, 21)), 95) == 19
    assert rb.percentile([], 50) is None


# ---------------------------------------------------------------- failure handling

SYSTEMS = ("drex", "llm_guard")


def test_parse_exception_is_failure_without_model_call(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    async def parse(path: Path, cache_dir: Any = None) -> ParsedInvoice:
        cid = Path(path).stem
        if cid == "t-1":
            raise RuntimeError("ndi down")
        return ParsedInvoice(doc_id=cid, parse_ms=2000)

    monkeypatch.setattr(rb.intake, "parse_document", parse)
    r = go(env)
    assert ("drex", "t-1") not in env["log"] and ("llm_guard", "t-1") not in env["log"]
    assert r["meta"]["prepare_failed"] == ["t-1"]
    for s in SYSTEMS:
        d = Decision.model_validate_json((env["data"] / f"decisions/{s}/t-1.json").read_text())
        assert d.error and d.error.startswith(rb.PREPARE_ERROR_PREFIX) and "RuntimeError" in d.error
        assert d.final_action is None and d.wall_ms == 0
        assert rb.is_prepare_failure(d)
        st = r["systems"][s]
        assert st["n_prepare_failed"] == 1
        assert st["run_calls"] == 4  # prepared cases only
    for key in ("drex_alone", "drex_guard", "llm_alone", "llm_guard", "hybrid"):
        assert r["rows"][key]["per_scenario"]["S1"]["correct"] == 0  # failure, never approval
    assert r["rows"]["llm_alone"]["failures"] == 1
    assert r["rows"]["drex_guard"]["failures"] == 2  # t-1 prepare + t-4 timeout
    parse_ms = json.loads((env["data"] / "decisions/parse_ms_test.json").read_text())
    assert "t-1" not in parse_ms and len(parse_ms) == 4
    assert "(1 before any call, at parse)" in (env["data"] / "results.md").read_text()


def test_ndi_status_warning_is_failure_without_model_call(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    async def parse(path: Path, cache_dir: Any = None) -> ParsedInvoice:
        cid = Path(path).stem
        warnings = ["ndi_status_failed"] if cid == "t-2" else []
        return ParsedInvoice(doc_id=cid, parse_ms=2000, warnings=warnings)

    monkeypatch.setattr(rb.intake, "parse_document", parse)
    r = go(env)
    assert all(e[1] != "t-2" for e in env["log"] if e[0] != "labels")
    assert r["meta"]["prepare_failed"] == ["t-2"]
    d = Decision.model_validate_json((env["data"] / "decisions/llm_guard/t-2.json").read_text())
    assert "ndi_status_failed" in (d.error or "") and d.final_action is None
    assert r["rows"]["llm_guard"]["unsafe_approvals"] == 0
    assert r["rows"]["llm_guard"]["failures"] == 1


def test_prepare_failure_excluded_from_failed_call_stats(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    async def parse(path: Path, cache_dir: Any = None) -> ParsedInvoice:
        cid = Path(path).stem
        if cid == "t-1":
            raise ValueError("bad pdf")
        return ParsedInvoice(doc_id=cid, parse_ms=2000)

    monkeypatch.setattr(rb.intake, "parse_document", parse)
    st = go(env)["systems"]["drex"]
    assert st["n_failed"] == 2 and st["n_prepare_failed"] == 1
    assert st["failed_p50_wall_ms"] == 100 and st["failed_input_tokens"] == 1000  # only t-4 was a call
    assert Decimal(st["failed_cost_total"]) == Decimal("0.00003")


def test_failed_drex_not_routed_to_llm(env: dict[str, Any]) -> None:
    r = go(env)
    ids = ["t-1", "t-2", "t-3", "t-4", "t-5"]
    drex = {c: Decision.model_validate_json((env["data"] / f"decisions/drex/{c}.json").read_text()) for c in ids}
    llm = {c: Decision.model_validate_json((env["data"] / f"decisions/llm_guard/{c}.json").read_text()) for c in ids}
    acts, alone = rb.hybrid_actions(drex, llm, ids, 0.7)
    assert acts["t-4"] is None  # the LLM guard said block, but it must not be used
    assert alone["t-4"] is True
    assert ("llm_guard", "t-4") in env["log"]  # llm ran on its own, hybrid ignores it
    assert r["rows"]["hybrid"]["failures"] == 1
    acts, _ = rb.hybrid_actions({}, llm, ["t-2"], 0.7)
    assert acts == {"t-2": None}  # Drex missing is a failure, not a fallback


def test_failed_calls_timing_and_cost_kept_separate(env: dict[str, Any]) -> None:
    (env["data"] / "llm_cost.jsonl").write_text("".join(
        json.dumps({"decision_id": f"llm_guard-{c}", "estimated_cost": "0.0003"}) + "\n" for c in LLM))
    r = go(env)
    d = r["systems"]["drex"]
    assert (d["n"], d["n_ok"], d["n_failed"], d["n_prepare_failed"]) == (5, 4, 1, 0)
    assert d["p50_wall_ms"] == 100 and d["failed_p50_wall_ms"] == 100 and d["failed_p95_wall_ms"] == 100
    assert d["failed_input_tokens"] == 1000 and d["failed_output_tokens"] == 10
    assert Decimal(d["failed_cost_total"]) == Decimal("0.00003")
    assert Decimal(d["cost_per_1000"]) == Decimal("0.03")  # successes only
    l = r["systems"]["llm_guard"]
    assert l["n_failed"] == 0 and l["failed_p50_wall_ms"] is None
    assert Decimal(l["failed_cost_total"]) == 0
    h = r["hybrid"]
    assert h["failed_p50_wall_ms"] == 100 and Decimal(h["failed_cost_total"]) == Decimal("0.00003")
    md = (env["data"] / "results.md").read_text()
    assert "- drex: 1 failed (0 before any call, at parse)" in md


def test_reuse_with_recorded_run_time(env: dict[str, Any]) -> None:
    first = go(env)
    info = json.loads((env["data"] / "decisions/run_seconds_test.json").read_text())
    assert set(info) == set(SYSTEMS) and info["drex"]["n_calls"] == 5
    env["log"].clear()
    again = go(env, reuse=True)
    assert [e for e in env["log"] if e[0] != "labels"] == []
    for s in SYSTEMS:
        exp = info[s]["n_calls"] / info[s]["run_seconds"]
        assert again["systems"][s]["decisions_per_s"] == pytest.approx(exp)
        assert again["systems"][s]["decisions_per_s"] == pytest.approx(first["systems"][s]["decisions_per_s"])


def test_reuse_without_recorded_run_time(env: dict[str, Any]) -> None:
    go(env)
    (env["data"] / "decisions/run_seconds_test.json").unlink()
    env["log"].clear()
    again = go(env, reuse=True)
    assert [e for e in env["log"] if e[0] != "labels"] == []
    for s in SYSTEMS:
        assert again["systems"][s]["decisions_per_s"] is None
        assert again["systems"][s]["run_seconds"] is None


# ---------------------------------------------------------------- provenance and cost footnote

PROVENANCE = ("started_at", "concurrency", "warmup")


def test_live_run_stores_start_warmup_concurrency(env: dict[str, Any]) -> None:
    r = go(env, warmup_n=1)
    meta = r["meta"]
    assert meta["started_at"] not in (None, rb.UNKNOWN)
    assert isinstance(meta["concurrency"], int) and meta["concurrency"] > 0
    assert meta["warmup"] == 1
    assert not meta.get("reuse")
    saved = json.loads((env["data"] / "results.json").read_text())["meta"]
    for k in PROVENANCE:
        assert saved[k] == meta[k]
    md = (env["data"] / "results.md").read_text()
    assert f"Started {meta['started_at']}. Concurrency {meta['concurrency']}." in md
    assert "1 were used only as discarded warm-up calls." in md


def test_reuse_preserves_live_provenance(env: dict[str, Any]) -> None:
    first = go(env, warmup_n=1)["meta"]
    env["log"].clear()
    again = go(env, reuse=True, warmup_n=5)["meta"]
    assert [e for e in env["log"] if e[0] != "labels"] == []
    for k in PROVENANCE:
        assert again[k] == first[k], k
    assert again["reuse"] is True
    md = (env["data"] / "results.md").read_text()
    assert f"Started {first['started_at']}." in md
    assert "1 were used only as discarded warm-up calls." in md
    assert "5 were used" not in md
    assert "Recomputed on " in md and "(--reuse, no model calls)" in md


def _decision_bytes(data: Path) -> dict[str, bytes]:
    return {str(p.relative_to(data)): p.read_bytes()
            for p in sorted((data / "decisions").rglob("*.json"))}


def _strip_provenance(data: Path) -> None:
    """Make stored run metadata look like a run made before provenance was recorded."""
    run_file = data / "decisions/run_seconds_test.json"
    legacy = {s: {"run_seconds": v["run_seconds"], "n_calls": v["n_calls"]}
              for s, v in json.loads(run_file.read_text()).items()}
    run_file.write_text(json.dumps(legacy))
    res = data / "results.json"
    if res.exists():
        obj = json.loads(res.read_text())
        for k in PROVENANCE + ("decisions_provenance",):
            obj["meta"].pop(k, None)
        res.write_text(json.dumps(obj))


def test_legacy_metadata_is_unknown_and_decisions_untouched(env: dict[str, Any]) -> None:
    go(env, warmup_n=1)
    _strip_provenance(env["data"])
    before = _decision_bytes(env["data"])
    env["log"].clear()
    r = go(env, reuse=True, warmup_n=3)
    assert [e for e in env["log"] if e[0] != "labels"] == []
    meta = r["meta"]
    for k in PROVENANCE:
        assert meta[k] == rb.UNKNOWN, k
    md = (env["data"] / "results.md").read_text()
    assert f"Started {rb.UNKNOWN}. Concurrency {rb.UNKNOWN}." in md
    assert "The number used as discarded warm-up calls is unknown (not recorded by the live run)." in md
    assert "Decisions provenance: unknown." in md
    assert "None" not in md.split("\n", 3)[2]
    assert _decision_bytes(env["data"]) == before  # stored decisions never rewritten by --reuse


def test_cost_footnote_describes_estimates() -> None:
    text = rb.footnote_cost({"drex_price_per_million_input_tokens": "0.05"})
    assert text.startswith("3. Drex cost is calculated from the configured price of $0.05 per million input tokens.")
    assert "provider-estimated estimated_cost" in text
    assert "provider-reported" not in text and "public list price" not in text
    assert "$0.03 per million" in rb.footnote_cost({})
