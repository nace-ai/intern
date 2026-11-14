"""The results hub serves recorded numbers and does not invent a lane."""

import json

from fastapi.testclient import TestClient

from billproof.config import DATA_DIR
from billproof.results.cases import get_case, list_cases
from billproof.results.catalog import build_summary
from billproof.server.app import app


def test_summary_matches_recorded_files():
    recorded = json.loads((DATA_DIR / "metrics" / "hard_outcome_drex-v1_line_verdict.json").read_text())
    persius = json.loads((DATA_DIR / "metrics" / "c_persius_test.json").read_text())
    document = build_summary()
    by_id = {bench["id"]: bench for bench in document["benchmarks"]}
    gpt = next(lane for lane in by_id["persius"]["lanes"] if lane["id"] == "frontier")
    assert gpt["value"] == persius["lanes"]["frontier"]["accuracy"]
    assert gpt["k"] == persius["lanes"]["frontier"]["n_correct"]
    assert gpt["n"] == persius["lanes"]["frontier"]["n"]
    drex = next(lane for lane in by_id["hard"]["lanes"] if lane["id"] == "drex-v1")
    assert drex["value"] == recorded["value"]
    assert drex["k"] == recorded["k"]
    assert drex["n"] == recorded["n"]
    assert by_id["hard"]["chart"]["metric"] == "line_verdict"
    assert drex["metrics"]["line_verdict"]["k"] == recorded["k"]
    all_six = json.loads((DATA_DIR / "metrics" / "hard_test_drex-v1-subset_line_verdict.json").read_text())
    assert drex["metrics"]["line_all_checks"]["k"] == all_six["k"]
    assert drex["metrics"]["line_all_checks"]["n"] == all_six["n"]
    haiku = next(lane for lane in by_id["hard"]["lanes"] if lane["id"] == "anthropic__claude-haiku-5.5")
    assert haiku["metrics"]["line_all_checks"]["value"] is None
    pa = next(lane for lane in by_id["pa_criteria_gym"]["lanes"] if lane["id"] == "gpt")
    assert by_id["pa_criteria_gym"]["chart"]["metric"] == "whole_case"
    assert pa["metrics"]["whole_case"]["k"] == 370
    assert pa["metrics"]["decision"]["k"] == 370
    page = list_cases("persius", lane="frontier", only_wrong=True, limit=3)
    assert page["n"] > 0
    one = get_case("persius", page["cases"][0]["case_id"])
    assert one is not None
    assert one["lanes"]["frontier"]["correct"] is False
    assert "appeal_text" not in json.dumps(one)
    client = TestClient(app)
    summary = client.get("/api/results/summary")
    assert summary.status_code == 200
    ids = {bench["id"] for bench in summary.json()["benchmarks"]}
    assert {"synthetic_v1", "pa_criteria_gym", "persius", "persius_combo", "hard", "ladder", "cc_queue", "cc_rule_change", "cc_what_decides", "full_chain"} <= ids
    recorded_combo = json.loads((DATA_DIR / "metrics" / "g_persius_combo.json").read_text())
    combo = next(lane for lane in by_id["persius_combo"]["lanes"] if lane["id"] == "combo")
    assert combo["k"] == recorded_combo["test"]["k"]
    assert combo["n"] == recorded_combo["test"]["n"]
    assert combo["value"] == recorded_combo["test"]["accuracy"]
    assert by_id["persius_combo"]["mcnemar"]["vs_gpt"] == recorded_combo["test"]["mcnemar_vs_gpt"]
    counted = 0
    offset = 0
    while True:
        page = list_cases("persius_combo", lane="combo", offset=offset, limit=200)
        counted += sum(1 for case in page["cases"] if case["lanes"]["combo"]["correct"])
        offset += len(page["cases"])
        if offset >= page["n"] or not page["cases"]:
            break
    assert counted == recorded_combo["test"]["k"]
    hard = client.get("/api/results/hard")
    assert hard.status_code == 200
    assert hard.json()["metric"] == "line_verdict"
    wrong = client.get("/api/results/persius/cases", params={"lane": "frontier", "only_wrong": "true", "limit": 1})
    assert wrong.status_code == 200
    case_id = wrong.json()["cases"][0]["case_id"]
    detail = client.get(f"/api/results/persius/case/{case_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["lanes"]["frontier"]["correct"] is False
    assert body["gold"] is not None
    assert "appeal_text" not in json.dumps(body)
    assert client.get("/api/results/missing-bench").status_code == 404
