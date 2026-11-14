"""Demo routes: offline replay is honest, live is opt-in, missing stays missing."""
import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from billproof.config import settings
from billproof.server.demo_routes import _manifest_from_file, router

@pytest.fixture(scope="module", autouse=True)
def _mock_mode():
    old = os.environ.get("MOCK_MODE")
    os.environ["MOCK_MODE"] = "1"
    settings.cache_clear()
    yield
    if old is None:
        os.environ.pop("MOCK_MODE", None)
    else:
        os.environ["MOCK_MODE"] = old
    settings.cache_clear()


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _events(response) -> list[tuple[str, dict]]:
    out, event = [], None
    for line in response.iter_lines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: ") and event:
            out.append((event, json.loads(line[6:])))
            event = None
    return out


def test_cases_endpoint_lists_demo_cases(client):
    resp = client.get("/api/demo/cases")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n"] == len(body["cases"]) and body["n"] > 0
    assert all(c["case_id"] and c["source_case_id"] for c in body["cases"])


def test_manifest_reader_is_permissive(tmp_path, monkeypatch):
    import billproof.server.demo_routes as dr

    monkeypatch.setattr(dr, "SHOWCASE_PATH", tmp_path / "cases.json")
    assert _manifest_from_file() == ([], None)  # absent -> empty, no error
    (tmp_path / "cases.json").write_text(json.dumps(
        {"schema_version": 1, "cases": [{"case_id": "SC-1", "source_case_id": "SYN-D01", "title": "t"}]}
    ))
    rows, err = _manifest_from_file()
    assert err is None and rows[0]["source_case_id"] == "SYN-D01"
    (tmp_path / "cases.json").write_text(json.dumps([{"case_id": "SC-2", "source_case_id": "SYN-D02"}]))
    rows, err = _manifest_from_file()
    assert err is None and rows[0]["case_id"] == "SC-2"  # bare array accepted


def test_replay_run_streams_all_stages_offline(client):
    with client.stream("GET", "/api/demo/run/SYN-D01?mode=replay") as resp:
        assert resp.status_code == 200
        events = _events(resp)
    names = [n for n, _ in events]
    assert names[0] == "start" and names[-1] == "complete"
    staged = [p["stage"] for n, p in events if n == "stage" and p["status"] != "running"]
    assert staged == ["upload", "read", "rules", "decide", "ask", "browser", "verdict", "act"]
    for _, p in events:
        if p.get("stage") is None:
            continue
        for field in ("stage", "status", "color", "key_number", "duration_ms",
                      "elapsed_ms", "mode", "simulated", "provenance", "data"):
            assert field in p
        assert p["mode"] == "replay"
        assert set(p["key_number"]) == {"value", "unit", "label"}
        assert "kind" in p["provenance"]
    done = [p for n, p in events if n == "stage" and p["status"] not in ("running",)]
    assert {p["stage"]: p["status"] for p in done}["decide"] == "ok"
    assert all(p["status"] in ("ok", "missing", "blocked", "error") for p in done)
    complete = events[-1][1]
    assert complete["verdict"]["case_action"] in {"OK", "DISPUTE", "APPEAL", "ASK_PATIENT", "INSUFFICIENT_EVIDENCE"}


def test_live_mode_blocks_without_credentials(client):
    with client.stream("GET", "/api/demo/run/SYN-D01?mode=live") as resp:
        events = _events(resp)
    done = {p["stage"]: p for n, p in events if n == "stage" and p["status"] != "running"}
    # MOCK_MODE=1 -> no live credentials -> decide must be blocked, never simulated.
    assert done["decide"]["status"] == "blocked"
    assert done["decide"]["simulated"] is False
    assert done["read"]["status"] == "blocked"
    assert done["verdict"]["status"] == "blocked"


def test_numbers_endpoint_is_honest_about_missing_metrics(client):
    resp = client.get("/api/demo/numbers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["metrics_dir_present"] is False or body["n_metrics"] >= 0
    assert isinstance(body["missing"], list) and body["missing"]
    for stem, entry in body["metrics"].items():
        for required in ("value", "n", "source_run", "simulated", "computed_at"):
            assert required in entry


def test_unknown_case_404(client):
    resp = client.get("/api/demo/run/SYN-9999")
    assert resp.status_code == 404


def test_all_showcase_outcomes_without_network(monkeypatch):
    import socket
    from billproof.pipeline import run_showcase
    from billproof.showcase import showcase_entries, load_showcase_case

    def no_network(*args, **kwargs):
        raise AssertionError("offline replay attempted network access")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    for entry in showcase_entries():
        result = run_showcase(load_showcase_case(entry["case_id"]), manifest=entry)
        completed = result["complete"]
        assert completed["ok"]
        assert completed["verdict"]["case_action"] == entry["expected_outcome"]
        verdict = next(e for e in result["events"] if e["stage"] == "verdict" and e["status"] != "running")
        assert verdict["color"] == entry["status_color"]
        if entry["case_id"] == "SC-04":
            assert completed["verdict"]["owed_cents"] is None


def test_recorded_search_reaches_draft_and_negative_control_does_not(monkeypatch):
    import socket
    from billproof.pipeline import run_showcase
    from billproof.showcase import load_showcase_case, showcase_case_entry

    def no_network(*args, **kwargs):
        raise AssertionError("recorded T3 entry point attempted network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    for cid, expected in (("SC-05", True), ("SC-04", False)):
        result = run_showcase(load_showcase_case(cid), manifest=showcase_case_entry(cid))
        stages = {e["stage"]: e for e in result["events"] if e["status"] != "running"}
        assert stages["ask"]["provenance"]["kind"] == "recorded_live"
        assert stages["ask"]["simulated"] is False
        assert bool(stages["ask"]["data"]["letter_support"]) is expected
        clauses = result["complete"]["letter"]["clauses"]
        assert any(c["clause_id"] == "plan_language" for c in clauses) is expected
        if expected:
            assert stages["browser"]["status"] == "ok"
            assert stages["browser"]["provenance"]["kind"] == "recorded_live"
            assert stages["act"]["data"]["support_documents"]
