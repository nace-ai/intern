import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from backend.app import config, nace


class _M(BaseModel):
    type: str
    noul: float | None = None
    choice: str | None = None
    probabilities: dict[str, float] | None = None


class _U(BaseModel):
    input_tokens: int = 7
    output_tokens: int = 1


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "USAGE_FILE", tmp_path / "usage.json")
    monkeypatch.setattr(nace, "USAGE", {})


def test_bump_appends(tmp_path):
    nace.bump(drex_calls=1)
    nace.bump(drex_calls=2, ndi_jobs=0)
    nace.bump(ndi_jobs=0)  # no change, no line
    lines = (tmp_path / "usage.json").read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[-1])["drex_calls"] == 3


def test_drex_with_fake_client(monkeypatch):
    seen = {}

    async def system_one(state, questions, model=None):
        seen["questions"] = questions
        return SimpleNamespace(
            model=model, usage=_U(), evaluation_time_ms=55.0, request_id="req",
            answers={"a": _M(type="noul", noul=0.8),
                     "action": _M(type="choice", choice="verify",
                                  probabilities={"approve": 0.1, "verify": 0.8, "block": 0.1})},
        )

    monkeypatch.setattr(nace, "_client", SimpleNamespace(system_one=system_one))
    qs = {"a": {"type": "noul", "instructions": "x", "criteria": {"true": "t", "false": "f"}},
          "action": {"type": "choice", "instructions": "y",
                     "criteria": {"approve": "a", "verify": "v", "block": "b"}}}
    r = asyncio.run(nace.drex({"s": 1}, qs, model="m"))
    assert r["answers"]["a"]["noul"] == 0.8
    assert r["answers"]["action"]["choice"] == "verify"
    assert r["evaluation_time_ms"] == 55.0 and r["request_id"] == "req"
    assert r["usage"]["input_tokens"] == 7 and isinstance(r["wall_ms"], int)
    assert nace.USAGE["drex_calls"] == 1 and nace.USAGE["drex_questions"] == 2
    from nace_sdk import Choice, Noul
    assert isinstance(seen["questions"]["a"], Noul)
    assert isinstance(seen["questions"]["action"], Choice)


def test_drex_failure_raises_and_counts(monkeypatch):
    async def system_one(state, questions, model=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(nace, "_client", SimpleNamespace(system_one=system_one))
    with pytest.raises(RuntimeError):
        asyncio.run(nace.drex({}, {}, model="m"))
    assert nace.USAGE["drex_calls"] == 1


AGENT_RESULT = {
    "routing": {"final": "agent"},
    "output": {"answer": {
        "answer": "One IBAN.",
        "structured_answer": {"ibans": [{"iban": "GB18", "bic": "B", "bank_name": "N", "documents_seen": 3}]},
        "confidence": None,
        "evidences": [{"source_path": "invoices/a.pdf", "page": 1, "quote": "IBAN: GB18", "relevance": 0.9,
                       "extra": "x"}],
    }},
}


def test_parse_search_result_agent_and_fact():
    r = nace.parse_search_result(AGENT_RESULT)
    assert r["route"] == "agent" and r["answer"] == "One IBAN." and r["confidence"] is None
    assert r["evidences"] == [{"source_path": "invoices/a.pdf", "page": 1, "quote": "IBAN: GB18", "relevance": 0.9}]
    fact = {"output": {"answer": "A", "confidence": 0.7, "structured_answer": {"ibans": []}, "evidences": []}}
    f = nace.parse_search_result(fact)
    assert f["answer"] == "A" and f["confidence"] == 0.7 and f["structured_answer"] == {"ibans": []}
    assert nace.parse_search_result(None)["evidences"] == []


def test_search_success_counts(monkeypatch):
    async def automatic(ws, **kw):
        assert kw["path_prefix"] == "invoices/" and kw["mode"] == "agent"
        return SimpleNamespace(job_id="j1", status="succeeded", result=AGENT_RESULT, error=None)

    monkeypatch.setattr(nace, "_client", SimpleNamespace(search=SimpleNamespace(automatic=automatic)))
    r = asyncio.run(nace.search("ws", "q", path_prefix="invoices/"))
    assert r["status"] == "succeeded" and r["error"] is None and r["job_id"] == "j1"
    assert r["structured_answer"]["ibans"][0]["iban"] == "GB18"
    assert isinstance(r["wall_ms"], int)
    assert nace.USAGE["search_calls"] == 1


def test_search_failure_sets_error(monkeypatch):
    async def automatic(ws, **kw):
        raise RuntimeError("down")

    monkeypatch.setattr(nace, "_client", SimpleNamespace(search=SimpleNamespace(automatic=automatic)))
    r = asyncio.run(nace.search("ws", "q"))
    assert r["status"] == "failed" and "down" in r["error"]
    assert r["structured_answer"] is None and r["evidences"] == []
    assert nace.USAGE["search_calls"] == 1


def test_search_failed_job(monkeypatch):
    async def automatic(ws, **kw):
        return SimpleNamespace(job_id="j2", status="running", result=None, error=None)

    async def wait(job_id, timeout, raise_on_failure):
        return SimpleNamespace(job_id=job_id, status="timed_out", result=None, error=None)

    monkeypatch.setattr(nace, "_client", SimpleNamespace(search=SimpleNamespace(automatic=automatic),
                                                         jobs=SimpleNamespace(wait=wait)))
    r = asyncio.run(nace.search("ws", "q"))
    assert r["status"] == "timed_out" and r["error"] == "job timed_out" and r["job_id"] == "j2"


def test_upload_and_list_files(monkeypatch):
    seen = {}

    async def upload(content, **kw):
        seen.update(kw)
        return SimpleNamespace(workspace_id="ws", file_id="f1", path=kw["path"])

    pages = [SimpleNamespace(items=[SimpleNamespace(path="invoices/a.pdf", file_id="f1", ingestion_status="ingested")],
                             next_cursor="c"),
             SimpleNamespace(items=[SimpleNamespace(path="invoices/b.pdf", file_id="f2", ingestion_status="pending")],
                             next_cursor=None)]

    async def flist(ws, path_prefix=None, limit=100, cursor=None):
        return pages[0 if cursor is None else 1]

    monkeypatch.setattr(nace, "_client", SimpleNamespace(
        documents=SimpleNamespace(upload=upload),
        workspaces=SimpleNamespace(files=SimpleNamespace(list=flist))))
    assert asyncio.run(nace.upload_file("ws", b"%PDF", "invoices/x/a.pdf")) == "f1"
    assert seen["on_conflict"] == "reject" and seen["file_name"] == "a.pdf" and seen["workspace_id"] == "ws"
    assert nace.USAGE["workspace_files"] == 1
    files = asyncio.run(nace.list_files("ws", "invoices/"))
    assert set(files) == {"invoices/a.pdf", "invoices/b.pdf"}
    assert files["invoices/b.pdf"]["ingestion_status"] == "pending"


def test_usage_totals_sums_search_counters(tmp_path):
    p = tmp_path / "u.json"
    p.write_text(json.dumps({"run_id": "a", "search_calls": 2, "workspace_files": 5}) + "\n"
                 + json.dumps({"run_id": "b", "search_calls": 1, "workspace_files": 0}) + "\n")
    t = nace.usage_totals(p)
    assert t["search_calls"] == 3 and t["workspace_files"] == 5
