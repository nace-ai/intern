"""NDI workflow with scripted responses. Hidden generator facts are not filled in."""

import asyncio
import json
from decimal import Decimal
from pathlib import Path

from benchmark_ndi import (
    charge_accounting,
    evidence_from_fields,
    ground_summary,
    run_live_case,
    score_fields,
)
import benchmark_ndi
from config import NDI_BUDGET_USD

ROOT = Path(__file__).resolve().parents[1]


def showcase_case() -> dict:
    cases = {case["case_id"]: case for case in json.loads((ROOT / "data" / "test_100.json").read_text(encoding="utf-8"))}
    return cases["TST-056"]


def install(monkeypatch, script: dict) -> list:
    calls: list = []

    class Fake:
        def __init__(self, http, api_key=None) -> None:
            self.http = http

        async def create_upload_grant(self, path, total):
            calls.append("grant")
            return {
                "workspace_id": "ws",
                "upload_url": "https://upload.test/grant",
                "token": "tok",
                "expires_at": "2099-01-01T00:00:00Z",
            }

        async def redeem_upload(self, grant, filename, data, path):
            calls.append("redeem")
            return script["redeem"]

        async def parse(self, workspace_id, file_id, key):
            calls.append("parse")
            return script["parse"]

        async def extract(self, parse_job_id, schema, key):
            calls.append("extract")
            return script["extract"]

        async def ground(self, parse_job_id, targets, key):
            calls.append(("ground", targets))
            return script["ground"]

        async def wait_job(self, job, deadline_s=180):
            calls.append(("wait", job.get("kind"), job.get("status")))
            return job

    monkeypatch.setattr(benchmark_ndi, "DocumentClient", Fake)
    return calls


def test_missing_file_id_stops_before_parse(monkeypatch):
    calls = install(monkeypatch, {"redeem": {"result": {"file": {}}}})

    row = asyncio.run(run_live_case(None, showcase_case(), Decimal("0")))
    assert row["status"] == "error"
    assert "file_id" in row["error"]
    assert "parse" not in calls
    assert "extract" not in calls
    assert "decisions" not in row


def test_failed_parse_does_not_extract_or_ground(monkeypatch):
    calls = install(
        monkeypatch,
        {
            "redeem": {"result": {"file": {"file_id": "file-1"}}},
            "parse": {"job_id": "parse-1", "kind": "parse", "status": "failed"},
        },
    )
    row = asyncio.run(run_live_case(None, showcase_case(), Decimal("0")))
    assert row["status"] == "failed"
    assert calls.count("parse") == 1
    assert "extract" not in calls
    assert not any(call[0] == "ground" for call in calls if isinstance(call, tuple))


def test_parse_timeout_does_not_create_another_job(monkeypatch):
    calls = install(
        monkeypatch,
        {
            "redeem": {"result": {"file": {"file_id": "file-1"}}},
            "parse": {"job_id": "parse-1", "kind": "parse", "status": "running", "poll_timeout": True},
        },
    )
    row = asyncio.run(run_live_case(None, showcase_case(), Decimal("0")))
    assert row["status"] == "poll_timeout"
    assert "duplicate" in row["error"] or "not succeed" in row["error"] or "timed out" in row["error"]
    assert calls.count("parse") == 1
    assert "extract" not in calls


def test_failed_extraction_does_not_substitute_generator_facts(monkeypatch):
    captured: dict[str, str] = {}

    async def capture(_mock, _http, case_id, evidence):
        captured["evidence"] = evidence
        base = {
            "case_id": case_id,
            "status": "ok",
            "verdict": "REVIEW",
            "input_hash": "abc",
            "timestamp": "2026-11-14T00:00:00+00:00",
            "mode": "live",
            "cost_usd": None,
        }
        return {
            "drex": {**base, "provider": "drex"},
            "openrouter": {**base, "provider": "openrouter"},
            "input_hash": "abc",
        }

    monkeypatch.setattr(benchmark_ndi, "decide_on_evidence", capture)
    markdown = "PARSE BODY WITHOUT FACTS"
    install(
        monkeypatch,
        {
            "redeem": {"result": {"file": {"file_id": "file-1"}}},
            "parse": {
                "job_id": "parse-1",
                "kind": "parse",
                "status": "succeeded",
                "result": {"document": {"markdown": markdown}, "ocr_applied": False},
                "cost": {"total_usd": "0.01"},
            },
            "extract": {"job_id": "extract-1", "kind": "extract", "status": "failed"},
        },
    )
    case = showcase_case()
    row = asyncio.run(run_live_case(None, case, Decimal("0")))
    evidence = captured["evidence"]
    assert evidence.startswith("Extraction did not succeed. Generator facts were not substituted.")
    assert markdown in evidence
    for hidden in ("expected_verdict", "source_annotations", "rule_signature", "generator_facts"):
        assert hidden not in evidence
    for value in case["facts"].values():
        if not isinstance(value, str) or value in markdown:
            continue
        assert value not in evidence
    assert any(job.get("kind") == "extract" and job.get("status") == "failed" for job in row["jobs"])


def test_ambiguous_field_and_ungrounded_citation_are_not_replaced(monkeypatch):
    captured: dict[str, str] = {}

    async def capture(_mock, _http, case_id, evidence):
        captured["evidence"] = evidence
        base = {
            "case_id": case_id,
            "status": "ok",
            "verdict": "REVIEW",
            "input_hash": "abc",
            "timestamp": "2026-11-14T00:00:00+00:00",
            "mode": "live",
            "cost_usd": None,
        }
        return {
            "drex": {**base, "provider": "drex"},
            "openrouter": {**base, "provider": "openrouter"},
            "input_hash": "abc",
        }

    monkeypatch.setattr(benchmark_ndi, "decide_on_evidence", capture)
    calls = install(
        monkeypatch,
        {
            "redeem": {"result": {"file": {"file_id": "file-1"}}},
            "parse": {
                "job_id": "parse-1",
                "kind": "parse",
                "status": "succeeded",
                "result": {"document": {"markdown": "parsed"}, "ocr_applied": True},
                "cost": {"total_usd": "0.25"},
            },
            "extract": {
                "job_id": "extract-1",
                "kind": "extract",
                "status": "succeeded",
                "result": {
                    "fields": [
                        {
                            "path": "/hazard_class",
                            "value": "extractor-ordinary",
                            "status": "found",
                            "ambiguous": True,
                            "citations": [{"source_text": "   "}, {"source_text": "quoted hazard line"}],
                        }
                    ]
                },
            },
            "ground": {
                "job_id": "ground-1",
                "kind": "ground",
                "status": "succeeded",
                "result": {"targets": [{"id": "hazard_class", "status": "not_found"}]},
                "cost": {"total_usd": "0.50"},
            },
        },
    )
    row = asyncio.run(run_live_case(None, showcase_case(), Decimal("0")))
    ground_calls = [call for call in calls if isinstance(call, tuple) and call[0] == "ground"]
    assert len(ground_calls) == 1
    assert ground_calls[0][1][0]["text"] == "quoted hazard line"
    assert all(target["text"].strip() for target in ground_calls[0][1])
    assert "extractor-ordinary" in captured["evidence"]
    assert "source_annotations" not in captured["evidence"]
    assert row["grounding"] == {"targets": 1, "found": 0, "coverage": 0}
    assert row["field_score"]["ambiguous"] >= 1
    assert row["field_score"]["packet_perfect"] is False
    assert row["cost_usd"] is None
    assert row["charge_accounting"]["known_partial_usd"] == "0.75"
    assert row["charge_accounting"]["unknown_count"] == 1


def test_charge_accounting_distinguishes_zero_partial_and_unknown():
    assert charge_accounting([])["cost_usd"] is None
    zero = charge_accounting([{"cost": {"total_usd": "0"}}])
    assert zero["cost_usd"] == "0"
    assert zero["known_partial_usd"] is None
    assert zero["unknown_count"] == 0
    partial = charge_accounting([{"cost": {"total_usd": "0.10"}}, {"kind": "extract"}, {"cost": None}])
    assert partial["cost_usd"] is None
    assert partial["known_partial_usd"] == "0.10"
    assert partial["unknown_count"] == 2
    complete = charge_accounting([{"cost": {"total_usd": "0.25"}}, {"cost": {"total_usd": "0.50"}}])
    assert complete["cost_usd"] == "0.75"
    assert complete["known_partial_usd"] is None


def test_absent_and_ambiguous_fields_do_not_count_as_perfect():
    case = {"facts": {"hazard_class": "other", "seal_pressure_kpa": "101", "conflicted_fields": []}}
    fields = [
        {"path": "/hazard_class", "value": None, "status": "not_found", "ambiguous": False},
        {"path": "/seal_pressure_kpa", "value": "101", "status": "not_found", "ambiguous": True},
    ]
    score = score_fields(case, fields)
    assert score["field_matches"] == 0
    assert score["ambiguous"] == 1
    assert score["missing_or_not_found"] == 3
    assert score["packet_perfect"] is False
    assert fields[0]["value"] is None
    assert ground_summary(None)["coverage"] is None
    assert ground_summary({"status": "failed", "result": {"targets": [{"status": "found"}]}})["coverage"] is None
    assert ground_summary({"status": "succeeded", "result": {"targets": [{"status": "not_found"}, {"status": "found"}]}}) == {
        "targets": 2,
        "found": 1,
        "coverage": 0.5,
    }


def test_evidence_renderer_uses_extractor_output_only():
    text = evidence_from_fields(
        [
            {
                "path": "/hazard_class",
                "status": "found",
                "ambiguous": True,
                "value": "extractor-ordinary",
                "citations": [{"source_text": "quoted hazard line"}],
            }
        ],
        "Fields and citations below are the extractor output.",
    )
    assert "extractor-ordinary" in text
    assert "quoted hazard line" in text
    assert "expected_verdict" not in text
    assert "generator_facts" not in text


def test_budget_reservation_skips_before_upload():
    row = asyncio.run(run_live_case(None, {"case_id": "TST-056"}, NDI_BUDGET_USD))
    assert row["status"] == "skipped"
    assert "budget" in row["error"]
