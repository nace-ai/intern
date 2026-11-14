"""Offline tests: synthetic data, fake providers and mocked HTTP. No network, no cost.

All numbers here are test data; none of them are benchmark results.
"""

import json
import math

import duckdb
import httpx
import pytest

from evaluation.benchmark import metrics, report, run
from evaluation.benchmark.providers import Decision, DrexProvider, OpenAIProvider, _system_prompt
from evaluation.benchmark.tasks import COMMUNICATION, PRIMARY_PROBLEM, DEMO_ACNS, Case, sample_cases


# ------------------------------------------------------------------------------ data


@pytest.fixture
def db():
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE incidents (acn VARCHAR, narrative VARCHAR, communication_breakdown VARCHAR, primary_problem VARCHAR)")
    problems = ["Human Factors"] * 30 + ["Aircraft"] * 30 + ["Procedure"] * 30 + ["Ambiguous"] * 30 + ["Weather"] * 20 + ["Airport"] * 20 + [None] * 2
    for i, problem in enumerate(problems):
        connection.execute("INSERT INTO incidents VALUES (?, ?, ?, ?)", [str(1000 + i), f"narrative {i} readback", "Party1 Flight Crew; Party2 ATC" if i % 3 == 0 else None, problem])
    for acn in DEMO_ACNS:
        connection.execute("INSERT INTO incidents VALUES (?, 'demo narrative', 'Party1 Flight Crew; Party2 ATC', 'Human Factors')", [acn])
    yield connection
    connection.close()


def test_labels_map_to_bounded_options():
    assert COMMUNICATION.label_of({"communication_breakdown": "Party1 Flight Crew; Party2 ATC", "primary_problem": None}) == "breakdown"
    assert COMMUNICATION.label_of({"communication_breakdown": None, "primary_problem": None}) == "no_breakdown"
    assert PRIMARY_PROBLEM.label_of({"primary_problem": "Weather", "communication_breakdown": None}) == "Other"
    assert PRIMARY_PROBLEM.label_of({"primary_problem": "Aircraft", "communication_breakdown": None}) == "Aircraft"
    assert PRIMARY_PROBLEM.label_of({"primary_problem": None, "communication_breakdown": None}) is None


def test_sampling_is_balanced_deterministic_and_excludes_demo_cases(db):
    first = sample_cases(db, PRIMARY_PROBLEM, 50, seed=7)
    again = sample_cases(db, PRIMARY_PROBLEM, 50, seed=7)
    other = sample_cases(db, PRIMARY_PROBLEM, 50, seed=8)
    assert [c.case_id for c in first] == [c.case_id for c in again]
    assert [c.case_id for c in first] != [c.case_id for c in other]
    assert len(first) == 50 and {c.label for c in first} == set(PRIMARY_PROBLEM.options)
    assert all(sum(c.label == label for c in first) == 10 for label in PRIMARY_PROBLEM.options)
    assert not {c.case_id for c in first} & set(DEMO_ACNS)
    comm = sample_cases(db, COMMUNICATION, 40, seed=7)
    assert sum(c.label == "breakdown" for c in comm) == 20 and len({c.case_id for c in comm}) == 40


def test_sampling_refuses_an_impossible_request(db):
    with pytest.raises(ValueError):
        sample_cases(db, PRIMARY_PROBLEM, 500, seed=1)


# --------------------------------------------------------------------------- metrics


def test_metrics_known_values():
    y, p = ["a", "a", "b", "b"], ["a", "b", "b", "b"]
    assert metrics.accuracy(y, p) == 0.75
    scores = metrics.per_class(y, p, ["a", "b"])
    assert scores["a"]["precision"] == 1.0 and scores["a"]["recall"] == 0.5
    assert metrics.macro_f1(y, p, ["a", "b"]) == pytest.approx((2 / 3 + 0.8) / 2)
    assert metrics.confusion(y, p, ["a", "b"]) == {"a": {"a": 1, "b": 1}, "b": {"a": 0, "b": 2}}
    low, high = metrics.wilson_interval(50, 100)
    assert low < 0.5 < high and low == pytest.approx(0.4038, abs=1e-3) and high == pytest.approx(0.5962, abs=1e-3)


def test_mcnemar_exact_and_bootstrap():
    assert metrics.mcnemar_exact(0, 0) == 1.0
    assert metrics.mcnemar_exact(10, 10) == 1.0
    assert metrics.mcnemar_exact(0, 10) == pytest.approx(2 / 1024)
    diff, low, high = metrics.paired_bootstrap_diff([True] * 90 + [False] * 10, [True] * 60 + [False] * 40, seed=1)
    assert diff == pytest.approx(0.3) and 0 < low < diff < high
    assert math.isnan(metrics.paired_bootstrap_diff([], [])[0])


def test_latency_calibration_and_cost():
    assert metrics.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert metrics.latency_summary([1.0, 3.0])["median"] == 1.0
    assert metrics.expected_calibration_error([1.0, 1.0], [True, False]) == pytest.approx(0.5)
    assert metrics.brier([{"a": 1.0, "b": 0.0}], ["a"], ["a", "b"]) == 0.0
    assert metrics.cost_per_1000([1000, 3000], [10, 30], 0.5, 2.0) == pytest.approx((2000 * 0.5 + 20 * 2.0) / 1e6 * 1000)
    assert metrics.cost_per_1000([1000], [None], None, None) is None  # no price, no cost


# -------------------------------------------------------------------------- providers


def _openai_provider(handler, **kwargs):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAIProvider("test-model", api_key="sk-test-not-real", http_client=http, **kwargs)


def _completion(content, refusal=None):
    return {"id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "test-model-2026",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content, "refusal": refusal}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 5, "total_tokens": 125}}


def test_openai_request_has_identical_options_and_strict_enum():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json=_completion('{"decision": "breakdown"}'))

    decision = _openai_provider(handler).decide(COMMUNICATION, "The crew misheard the clearance.")
    body = seen["body"]
    assert body["model"] == "test-model" and "temperature" not in body
    assert body["messages"][1] == {"role": "user", "content": "The crew misheard the clearance."}
    assert body["messages"][0]["content"] == _system_prompt(COMMUNICATION, structured=True)
    assert COMMUNICATION.instructions in body["messages"][0]["content"]
    assert all(label in body["messages"][0]["content"] for label in COMMUNICATION.options)
    schema = body["response_format"]["json_schema"]
    assert schema["strict"] is True and schema["schema"]["properties"]["decision"]["enum"] == list(COMMUNICATION.options)
    assert (decision.option, decision.input_tokens, decision.output_tokens, decision.model, decision.error) == ("breakdown", 120, 5, "test-model-2026", None)
    assert decision.latency_seconds >= 0


def test_openai_invalid_refusal_and_http_errors_are_recorded_not_raised():
    def respond(payload=None, status=200):
        return lambda request: httpx.Response(status, json=payload)

    invalid = _openai_provider(respond(_completion('{"decision": "maybe"}'))).decide(COMMUNICATION, "x")
    assert invalid.option is None and invalid.error["code"] == "invalid_output" and invalid.raw["content"]
    refused = _openai_provider(respond(_completion(None, refusal="No."))).decide(COMMUNICATION, "x")
    assert refused.option is None and refused.error["code"] == "refusal"
    unauthorized = _openai_provider(respond({"error": {"message": "bad key sk-hidden", "type": "invalid_request_error"}}, 401)).decide(COMMUNICATION, "x")
    assert unauthorized.error["code"] == "provider_auth_failed"
    bad_request = _openai_provider(respond({"error": {"message": "unsupported parameter", "type": "invalid_request_error"}}, 400)).decide(COMMUNICATION, "x")
    assert bad_request.error["code"] == "provider_rejected_request" and "unsupported" in bad_request.error["message"]


def test_openai_plain_text_mode_parses_exact_labels_only():
    provider = _openai_provider(lambda request: httpx.Response(200, json=_completion('"Human Factors".')), structured=False)
    assert provider.decide(PRIMARY_PROBLEM, "x").option == "Human Factors"
    sloppy = _openai_provider(lambda request: httpx.Response(200, json=_completion("I think it is Human Factors")), structured=False)
    assert sloppy.decide(PRIMARY_PROBLEM, "x").error["code"] == "invalid_output"
    assert "response_format" not in _openai_provider(lambda r: httpx.Response(200, json=_completion("Aircraft")), structured=False)._request(PRIMARY_PROBLEM, "x")


def test_openai_temperature_only_sent_when_set():
    assert _openai_provider(lambda r: httpx.Response(200, json={}), temperature=0.0)._request(COMMUNICATION, "x")["temperature"] == 0.0


def test_openai_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAIProvider("test-model")


def test_drex_provider_sends_the_same_question_and_reads_usage(monkeypatch):
    monkeypatch.setenv("NACE_API_KEY", "nace_sk_test_only_not_real")
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, headers={"x-request-id": "req_t"}, json={
            "model": "drex-test", "request_id": "req_t", "evaluation_time_ms": 12.5,
            "usage": {"input_tokens": 321, "output_tokens": 0},
            "answers": {"decision": {"type": "choice", "choice": "no_breakdown", "confidence": 0.8,
                                     "probabilities": {"breakdown": 0.2, "no_breakdown": 0.8}}}})

    provider = DrexProvider("drex-latest", transport=httpx.MockTransport(handler))
    decision = provider.decide(COMMUNICATION, "Narrative text")
    provider.close()
    question = seen["body"]["questions"]["decision"]
    assert seen["body"]["state"] == "Narrative text"
    assert question["instructions"] == COMMUNICATION.instructions and question["criteria"] == COMMUNICATION.options
    assert (decision.option, decision.input_tokens, decision.server_time_ms, decision.model) == ("no_breakdown", 321, 12.5, "drex-test")
    assert decision.probabilities == {"breakdown": 0.2, "no_breakdown": 0.8}


# ------------------------------------------------------------------------------ runner


class FakeProvider:
    def __init__(self, name, answer, fail_on=(), latency=0.01):
        self.name, self.requested_model, self.answer, self.fail_on, self.latency = name, f"{name}-model", answer, set(fail_on), latency
        self.calls = []

    def decide(self, task, narrative):
        self.calls.append(narrative)
        if narrative in self.fail_on:
            return Decision(None, self.latency, error={"code": "provider_unavailable", "message": "boom"})
        label = self.answer(narrative, task)
        return Decision(label, self.latency, probabilities={o: (0.9 if o == label else 0.1 / (len(task.options) - 1)) for o in task.options},
                        input_tokens=100, output_tokens=2, model=f"{self.name}-actual")


def _cases(db, n=20):
    return {COMMUNICATION.name: sample_cases(db, COMMUNICATION, n, seed=3)}


def test_runner_records_every_call_resumes_and_retries_errors(db, tmp_path):
    cases = _cases(db, 10)
    bad = cases[COMMUNICATION.name][0].narrative
    drex = FakeProvider("drex", lambda n, t: "breakdown")
    other = FakeProvider("openai", lambda n, t: "no_breakdown", fail_on=[bad])
    counts = run.run_benchmark(cases, {"drex": drex, "openai": other}, tmp_path, workers=2, log=lambda m: None)
    assert counts == {"planned": 20, "done": 20, "errors": 1}
    rows = run.load_rows(tmp_path / "raw.jsonl")
    assert len(rows) == 20 and sum(1 for r in rows.values() if r["error"]) == 1
    failed = next(r for r in rows.values() if r["error"])
    assert failed["option"] is None and failed["error"]["code"] == "provider_unavailable"  # recorded, not dropped

    again = run.run_benchmark(cases, {"drex": drex, "openai": other}, tmp_path, log=lambda m: None)
    assert again["planned"] == 0 and len(drex.calls) == 10  # nothing is re-called on resume

    healthy = FakeProvider("openai", lambda n, t: "no_breakdown")
    retried = run.run_benchmark(cases, {"drex": drex, "openai": healthy}, tmp_path, retry_errors=True, log=lambda m: None)
    assert retried["planned"] == 1 and len(healthy.calls) == 1
    assert not any(r["error"] for r in run.load_rows(tmp_path / "raw.jsonl").values())  # the latest row wins


def test_runner_converts_a_provider_bug_into_an_error_row(db, tmp_path):
    class Broken:
        name, requested_model = "drex", "m"

        def decide(self, task, narrative):
            raise ZeroDivisionError

    counts = run.run_benchmark(_cases(db, 4), {"drex": Broken()}, tmp_path, log=lambda m: None)
    assert counts["errors"] == 4
    assert {r["error"]["code"] for r in run.load_rows(tmp_path / "raw.jsonl").values()} == {"internal_error"}


def test_main_refuses_to_spend_money_without_yes(tmp_path, capsys, monkeypatch):
    path = tmp_path / "t.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute("CREATE TABLE incidents (acn VARCHAR, narrative VARCHAR, communication_breakdown VARCHAR, primary_problem VARCHAR)")
        for i in range(60):
            connection.execute("INSERT INTO incidents VALUES (?, 'n', ?, ?)", [str(i), "x" if i % 2 else None, ["Human Factors", "Aircraft", "Procedure", "Ambiguous", "Weather"][i % 5]])
    monkeypatch.setattr(run, "RESULTS_ROOT", tmp_path / "results")
    base = ["--db", str(path), "--n", "20", "--openai-model", "m", "--name", "t"]
    assert run.main(base) == 2 and "--yes" in capsys.readouterr().out
    assert not (tmp_path / "results").exists()  # nothing created, nothing called
    assert run.main(base + ["--dry-run"]) == 0 and "no API calls were made" in capsys.readouterr().out
    assert run.main(["--db", str(path), "--n", "20", "--name", "t", "--yes"]) == 2  # openai model is mandatory


# ------------------------------------------------------------------------------ report


def test_report_computes_everything_from_raw_rows(db, tmp_path, monkeypatch):
    monkeypatch.setattr(run, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(report, "RESULTS_ROOT", tmp_path)
    cases = _cases(db, 40)
    truth = {c.case_id: c.label for c in cases[COMMUNICATION.name]}
    ids = sorted(truth)
    wrong = set(ids[:8])  # Drex wrong on 8, OpenAI wrong on 20 (different cases overlap partly)
    oai_wrong = set(ids[:20])
    drex = FakeProvider("drex", lambda n, t: None)
    by_narrative = {c.narrative: c.case_id for c in cases[COMMUNICATION.name]}

    def answer(wrong_ids):
        def f(narrative, task):
            cid = by_narrative[narrative]
            return ("no_breakdown" if truth[cid] == "breakdown" else "breakdown") if cid in wrong_ids else truth[cid]
        return f

    drex.answer, oai = answer(wrong), FakeProvider("openai", answer(oai_wrong), latency=0.5)
    out_dir = tmp_path / "t"
    out_dir.mkdir()
    run.run_benchmark(cases, {"drex": drex, "openai": oai}, out_dir, log=lambda m: None)
    (out_dir / "run_meta.json").write_text(json.dumps({
        "created_at": "t", "seed": 3, "n_requested": 40, "workers": 2, "providers": {"drex": "drex-model", "openai": "openai-model"},
        "openai_mode": "json_schema", "openai_temperature": None,
        "tasks": {COMMUNICATION.name: {"title": COMMUNICATION.title, "ground_truth_field": COMMUNICATION.ground_truth_field,
                                       "options": COMMUNICATION.options, "instructions": COMMUNICATION.instructions, "cases": truth}}}), encoding="utf-8")

    summary = report.build_summary("t", None, {"drex": (0.12, 0.0), "openai": (0.15, 0.6)})
    task = summary["tasks"][COMMUNICATION.name]
    assert task["providers"]["drex"]["accuracy"] == pytest.approx(32 / 40)
    assert task["providers"]["openai"]["accuracy"] == pytest.approx(20 / 40)
    assert task["providers"]["drex"]["cost_per_1000_usd"] == pytest.approx(100 * 0.12 / 1e6 * 1000)
    assert task["providers"]["openai"]["latency_seconds"]["median"] == pytest.approx(0.5)
    h = task["head_to_head"]
    assert h["only_drex_correct"] == 12 and h["only_openai_correct"] == 0 and h["both_correct"] == 20 and h["mcnemar_p"] < 0.01
    assert h["accuracy_diff_ci"][0] > 0 and any("Drex scored higher" in line for line in task["verdict"])
    assert task["baselines"][0]["accuracy"] == pytest.approx(0.5)  # majority class on a balanced sample
    markdown = report.render_markdown(summary)
    assert "Limitations" in markdown and "McNemar" in markdown
    assert "not computed" in report.render_markdown(report.build_summary("t", None, {}))  # no prices, no cost claim
