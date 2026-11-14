"""Drex-then-DeepInfra scoring uses recorded rows only and keeps the GPT dial."""

from fastapi.testclient import TestClient

from billproof.dial.qcombo import prefix_ids, summarize
from billproof.server.app import app


def _case(conf: float, drex_ok: bool, qwen_ok: bool) -> dict:
    return {
        "case_id": "c1",
        "questions": [{
            "id": "overturn",
            "family": "overturn",
            "conf": conf,
            "drex_ok": drex_ok,
            "gpt_ok": qwen_ok,
            "drex_choice": "yes",
            "gpt_choice": "no",
            "gold": "yes",
        }],
        "drex_cost_usd": 0.01,
        "drex_ms": 100.0,
        "di_cost_usd": 0.02,
        "di_ms": 50.0,
        "di_input_tokens": 1_000,
        "di_output_tokens": 100,
        "di_model": "Qwen/Qwen3.8-Max",
        "di_cost_basis": "provider_estimated_cost",
        "case": {},
    }


def test_prefix_stops_at_the_first_hole():
    assert prefix_ids(["a", "b", "c"], {"a", "c"}) == ["a"]
    assert prefix_ids(["a", "b", "c"], {"a", "b", "c"}) == ["a", "b", "c"]
    assert prefix_ids(["a", "b"], set()) == []


def test_threshold_keeps_confident_drex_and_scales_qwen_cost():
    case = _case(0.8, drex_ok=True, qwen_ok=False)
    kept = summarize("persius", [case], 0.50, routed="threshold")
    sent = summarize("persius", [case], 0.90, routed="threshold")
    alone = summarize("persius", [case], None, routed="all")
    drex = summarize("persius", [case], None, routed="none")
    assert kept["metrics"]["accuracy"]["k"] == 1
    assert kept["questions_routed"]["share"] == 0.0
    assert kept["cost_per_1000_usd"] == 10.0
    assert kept["latency_p50_ms"] == 100.0
    assert sent["metrics"]["accuracy"]["k"] == 0
    assert sent["questions_routed"]["share"] == 1.0
    assert kept["cost_per_1000_usd"] == drex["cost_per_1000_usd"] == 10.0
    # Fully routed: Drex's recorded cost plus the whole recorded DeepInfra call.
    assert sent["cost_per_1000_usd"] == drex["cost_per_1000_usd"] + alone["cost_per_1000_usd"]
    assert sent["latency_p50_ms"] == 150.0
    assert alone["cost_parts_per_1000_usd"]["drex"] == 0.0


def test_recorded_qwen_dial_and_hub_lane():
    from billproof.results.catalog import build_summary

    client = TestClient(app)
    dial = client.get("/api/results/persius/dial", params={"escalate": "qwen"})
    assert dial.status_code == 200
    body = dial.json()
    assert body["escalate"] == "qwen"
    assert body["escalate_model"] == "Qwen/Qwen3.8-Max"
    assert body["threshold"] == 0.9
    assert len(body["curve"]) == 51
    assert body["qwen_only"]["metrics"]["accuracy"]["n"] == body["n_cases"]
    point = client.get("/api/results/persius/dial", params={"escalate": "qwen", "t": 0.9})
    assert point.status_code == 200
    assert point.json()["routed_by_case"]
    summary = build_summary()
    persius = next(item for item in summary["benchmarks"] if item["id"] == "persius")
    ids = [lane["id"] for lane in persius["lanes"]]
    assert "qwen38max" in ids
    assert ids.index("drex_then_qwen38max") < ids.index("drex_then_qwen3827b")
    combo = next(lane for lane in persius["lanes"] if lane["id"] == "drex_then_qwen38max")
    assert combo["extra"]["questions_routed"]["share"] is not None
    assert combo["metrics"]["accuracy"]["ci_method"] == "wilson"


def test_gpt_dial_stays_available_and_qwen_param_is_accepted():
    client = TestClient(app)
    gpt = client.get("/api/results/persius/dial")
    assert gpt.status_code == 200
    body = gpt.json()
    assert body.get("escalate") in (None, "gpt")
    assert "curve" in body
    unknown = client.get("/api/results/persius/dial", params={"escalate": "qwen", "model": "not-a-model"})
    assert unknown.status_code == 400
    family = client.get("/api/results/hard/dial", params={"escalate": "qwen", "router": "family", "t": 0.9})
    assert family.status_code == 400
