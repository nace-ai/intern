"""Wire and rule checks for the command center. No network, no parquet."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from billproof.bench.pa.logic import decide
from billproof.command_center.batch import (
    BudgetStop,
    ItemResult,
    WorkItem,
    consume_pack,
    run_pack,
)
from billproof.command_center.rows import (
    and_deps,
    colour_of,
    flip_name,
    invalid_gpt_answers,
    needs_accuracy_split,
    probe_failure,
    seeded_sample_ids,
    single_document_fixes,
)
from billproof.server.cc_routes import router


def test_colour_or_to_and_and_sample():
    assert colour_of("criteria_met") == ("approve", "green")
    assert colour_of("criteria_unmet") == ("deny", "red")
    assert colour_of("incomplete")[1] == "amber"
    assert colour_of("not_applicable") == ("not applicable", "amber")

    facts = {"a": True, "b": False}
    old = decide(facts, {"any": ["a", "b"]}, "applicable")
    new = decide(facts, and_deps({"any": ["a", "b"]}), "applicable")
    assert old["decision"] == "criteria_met"
    assert new["decision"] == "criteria_unmet"
    assert flip_name("approve", "deny") == "approve_to_deny"
    assert flip_name("approve", "approve") is None

    fixes = single_document_fixes(
        {"a": True, "b": False}, {"ca": ["a"], "cb": ["b"]}, "applicable",
    )
    assert fixes == ["b"]
    assert single_document_fixes({"a": True}, {"ca": ["a"]}, "wrong_payer") == []

    ids = seeded_sample_ids(["c", "a", "b", "d"], 2, 20261114)
    assert ids == seeded_sample_ids(["b", "d", "a", "c"], 2, 20261114)
    assert len(ids) == 2
    assert needs_accuracy_split([True] * 8, 8) is False
    assert needs_accuracy_split([False] * 8, 8) is True
    assert needs_accuracy_split([False] * 3, 3) is True
    assert needs_accuracy_split([False], 1) is False


class _Q:
    def __init__(self, name, options):
        self.name = name
        self._options = options

    def options(self):
        return list(self._options)


def test_invalid_gpt_and_probe_failure():
    question = _Q("fact::a", [
        "documented_satisfied", "documented_not_satisfied", "not_documented",
    ])
    assert invalid_gpt_answers([question], {
        "fact::a": {"choice": "documented_satisfied", "error": "invalid choice"},
    }) == ["fact::a"]
    assert invalid_gpt_answers([question], {
        "fact::a": {"choice": "documented_not_satisfied"},
    }) == []
    assert invalid_gpt_answers([question], {}) == ["fact::a"]
    assert probe_failure([{"record": "R1", "error": "missing"}], 2) == "ablation incomplete 0/2"
    assert probe_failure([{"record": "R1"}, {"record": "R2"}], 2) is None
    assert probe_failure([], 0) == "no records to ablate"


def _item(packet_id):
    return WorkItem(packet_id=packet_id, case=None, state={}, questions=[])


def test_split_keeps_billed_half(monkeypatch):
    import billproof.command_center.batch as batch

    def fake(_adapter, items):
        if len(items) > 1:
            raise batch.SplitBatch("HTTP 500")
        if items[0].packet_id == "bad":
            raise RuntimeError("singleton failed")
        return [ItemResult(packet_id=items[0].packet_id, choices={"fact::a": "documented_satisfied"},
                            cost_usd=0.01)]

    monkeypatch.setattr(batch, "_call_live", fake)
    got = run_pack([_item("good"), _item("bad")], adapter=object(), simulated=False)
    assert [row.packet_id for row in got] == ["good", "bad"]
    assert got[0].error is None and got[0].choices["fact::a"] == "documented_satisfied"
    assert got[0].cost_usd == 0.01
    assert got[1].error and got[1].calls == 0


def test_budget_keeps_finished_half(monkeypatch):
    import billproof.command_center.batch as batch

    def fake(_adapter, items):
        if len(items) > 1:
            raise batch.SplitBatch("HTTP 500")
        if items[0].packet_id == "later":
            raise BudgetStop("nace cap")
        return [ItemResult(packet_id=items[0].packet_id, choices={"fact::a": "not_documented"})]

    monkeypatch.setattr(batch, "_call_live", fake)
    try:
        run_pack([_item("early"), _item("later")], adapter=object(), simulated=False)
    except BudgetStop as exc:
        assert [row.packet_id for row in exc.kept] == ["early"]
        assert exc.kept[0].error is None
    else:
        raise AssertionError("BudgetStop was not raised")

    def call():
        raise BudgetStop("nace cap", kept=[ItemResult(packet_id="early", choices={"a": "yes"})])

    pairs, stop = consume_pack([_item("early"), _item("later")], call)
    assert isinstance(stop, BudgetStop)
    assert [item.packet_id for item, _res in pairs] == ["early"]
    assert pairs[0][1].choices["a"] == "yes"


def test_replay_file_is_compact_and_used_when_full_queue_is_absent(tmp_path, monkeypatch):
    import billproof.command_center.report as report

    full = tmp_path / "full.jsonl"
    replay = tmp_path / "replay.jsonl"
    full.write_text(
        '{"case_id":"c1","decision":"criteria_met","colour":"green","latency_ms":12,'
        '"cost_usd":0.01,"correct":true,"criteria":[{"text":"long"}],"qa":{"fact::a":{}}}\n'
    )
    monkeypatch.setattr(report, "REPLAY_PATH", replay)
    report.write_replay(report.load_jsonl(full))
    row = __import__("json").loads(replay.read_text())
    assert set(row) == {"case_id", "decision", "colour", "latency_ms", "cost_usd", "correct"}
    assert "qa" not in row
    monkeypatch.setattr(report, "QUEUE_PATH", tmp_path / "missing.jsonl")
    assert report.replay_rows()[0]["case_id"] == "c1"


def test_cc_routes_empty_and_replay_shape(tmp_path, monkeypatch):
    import billproof.command_center.common as common
    import billproof.command_center.report as report
    import billproof.server.cc_routes as routes

    monkeypatch.setattr(common, "QUEUE_SUMMARY_PATH", tmp_path / "missing-summary.json")
    monkeypatch.setattr(common, "RULE_SUMMARY_PATH", tmp_path / "missing-rule.json")
    monkeypatch.setattr(common, "DECIDE_SUMMARY_PATH", tmp_path / "missing-decide.json")
    monkeypatch.setattr(common, "QUEUE_PATH", tmp_path / "queue.jsonl")
    monkeypatch.setattr(common, "RULE_PATH", tmp_path / "rule.jsonl")
    monkeypatch.setattr(common, "DECIDE_PATH", tmp_path / "decide.jsonl")
    monkeypatch.setattr(common, "SPEND_PATH", tmp_path / "spend.json")
    monkeypatch.setattr(routes, "QUEUE_SUMMARY_PATH", tmp_path / "missing-summary.json")
    monkeypatch.setattr(routes, "FULL_SUMMARY_PATH", tmp_path / "missing-full.json")
    monkeypatch.setattr(routes, "RULE_SUMMARY_PATH", tmp_path / "missing-rule.json")
    monkeypatch.setattr(routes, "DECIDE_SUMMARY_PATH", tmp_path / "missing-decide.json")
    monkeypatch.setattr(report, "QUEUE_PATH", tmp_path / "queue.jsonl")
    monkeypatch.setattr(report, "RULE_PATH", tmp_path / "rule.jsonl")
    monkeypatch.setattr(report, "DECIDE_PATH", tmp_path / "decide.jsonl")

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    summary = client.get("/api/cc/summary")
    assert summary.status_code == 200
    assert summary.json()["status"] == "empty"
    assert client.get("/api/cc/rule_change").json()["flips"] == []
    assert client.get("/api/cc/what_decides").json()["stuck"]["reasons"] == []
    assert client.get("/api/cc/case/nope").status_code == 404

    (tmp_path / "queue.jsonl").write_text(
        '{"case_id":"c1","label":"approve","colour":"green","decision":"criteria_met",'
        '"correct":true,"latency_ms":20,"cost_usd":0.001,"criteria":[]}\n'
    )
    with client.stream("GET", "/api/cc/queue/stream?speed=500") as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "event: start" in body
    assert "event: decision" in body
    assert "event: finish" in body
    assert '"case_id": "c1"' in body or '"case_id":"c1"' in body


def _card():
    from billproof.command_center.whatif import CaseCard
    return CaseCard(
        case_id="c1", list_id="list-1", policy_id="P1",
        family="ar-medicaid-pa-criteria", state="AR", payer="Arkansas Medicaid",
        request_payer="Arkansas Medicaid", service_date="2026-10-15",
        window_from="2026-10-01", window_through="2026-10-31", connective="OR",
        split="validation", stage="initial_packet", title="Example", drug=None,
        deps={"any": ["a", "b"]},
        criteria_text={"any": "A OR B"},
        fact_text={"a": "Clause A", "b": "Clause B"},
        facts={"a": True, "b": None},
        scope_payer="yes", scope_date="yes", gold="criteria_met", cached=True,
    )


def test_what_if_edits_are_deterministic():
    from billproof.command_center.whatif import run_edits, stamp_baseline
    card = _card()
    stamp_baseline(card)
    assert card.base_decision == "criteria_met"
    tightened = run_edits([card], edit={"connective": "AND"}, apply_to={"list_id": "list-1"})
    assert tightened["hypothetical"] is True
    assert tightened["n_flips"] == 1
    assert tightened["flips"][0]["to_decision"] == "incomplete"
    assert tightened["flip_counts"]["approve_to_incomplete"] == 1
    denied = run_edits(
        [card],
        edit={"connective": "AND", "missing_as": "not_met"},
        apply_to={"state": "AR"},
    )
    assert denied["flips"][0]["to_label"] == "deny"
    same = run_edits([card], edit={"missing_as": "missing"}, apply_to={"family": card.family})
    assert same["n_flips"] == 0
    window = run_edits(
        [card],
        edit={"window": {"from": "2027-01-01", "through": "2027-12-31"}},
        apply_to={"policy_id": "P1"},
    )
    assert window["flips"][0]["to_decision"] == "not_applicable"
    payer = run_edits(
        [card], edit={"restrict_payer": "Florida Medicaid"}, apply_to={"list_id": "list-1"},
    )
    assert payer["flips"][0]["to_decision"] == "not_applicable"
    dropped = run_edits([card], edit={"drop_criteria": ["b"]}, apply_to={"list_id": "list-1"})
    assert dropped["n_flips"] == 0
    required = run_edits([card], edit={"require_criteria": ["b"]}, apply_to={"list_id": "list-1"})
    assert required["flips"][0]["to_decision"] == "incomplete"
    other = _card()
    other.case_id = "c2"
    other.state = "WV"
    other.family = "wv-medicaid-pa-criteria"
    other.list_id = "other"
    stamp_baseline(other)
    only_ar = run_edits(
        [card, other], edit={"missing_as": "not_met", "connective": "AND"},
        apply_to={"state": "AR"},
    )
    assert only_ar["n_affected"] == 1
    assert {flip["case_id"] for flip in only_ar["flips"]} == {"c1"}
