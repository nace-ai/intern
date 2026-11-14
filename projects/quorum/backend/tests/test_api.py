import json
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from backend.app import main

HASH = "ab" * 32


def _jsonl(path: Path, recs: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")


def _pdf(path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page()  # default A4: 595 x 842
    doc.save(path)
    doc.close()


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("QUORUM_DATA", str(tmp_path))
    return tmp_path


@pytest.fixture
def full(data: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    arena = data / "arena"
    (arena / "pdfs").mkdir(parents=True)
    _pdf(arena / "pdfs" / "a00.pdf")
    outside = tmp_path_factory.mktemp("outside") / "x.pdf"
    _pdf(outside)
    base = {"kind": "bank_swap", "note": "n", "style": "s", "decisions": {"guarded": "block"}}
    _jsonl(arena / "attacks.jsonl", [
        {**base, "round": 1, "id": "a00", "pdf": "arena/pdfs/a00.pdf", "doc_hash": f"sha256:{HASH}"},
        {**base, "round": 1, "id": "a01", "pdf": str(outside), "doc_hash": "sha256:" + "cd" * 32},
        {**base, "round": 2, "id": "a00", "pdf": "arena/pdfs/missing.pdf", "doc_hash": None},
    ])
    _jsonl(arena / "rounds.jsonl", [
        {"round": 1, "n_attacks": 2, "attacker": {"turns": 3, "input_tokens": 100, "output_tokens": 10}},
        {"round": 2, "n_attacks": 1, "attacker": {"turns": 2, "input_tokens": 50, "output_tokens": 5}},
    ])
    (arena / "report.json").write_text(json.dumps({"rounds": 2}))
    (data / "ndi_cache").mkdir()
    fields = {"iban": {"value": "GB00X", "status": "ok",
                       "citations": [{"page": 1, "polygons": [[0, 0, 1, 1]], "source_text": "GB00X"}]}}
    (data / "ndi_cache" / f"{HASH}.json").write_text(json.dumps({"parsed": {"fields": fields}}))
    _jsonl(data / "usage.json", [
        {"ts": 1, "drex_calls": 1, "drex_questions": 2, "drex_input_tokens": 3, "ndi_jobs": 4, "ndi_pages": 5},
        {"ts": 2, "drex_calls": 6, "drex_questions": 7, "drex_input_tokens": 8, "ndi_jobs": 9, "ndi_pages": 10},
    ])
    logs = data / "intern-logs"
    logs.mkdir()
    end = {"type": "run_end", "turns": 4, "usage": {"input_tokens": 100, "output_tokens": 20},
           "system1": {"asks": 7}}
    text = json.dumps({"type": "message"}) + "\n" + json.dumps(end) + "\n"
    (logs / "a.jsonl").write_bytes(text.encode("utf-8-sig"))
    (logs / "b.jsonl").write_bytes(text.encode("utf-16"))
    return data


def test_health(data: Path) -> None:
    assert TestClient(main.app).get("/api/health").json() == {"ok": True}


def test_rounds(full: Path) -> None:
    r = TestClient(main.app).get("/api/arena/rounds").json()
    assert [x["round"] for x in r] == [1, 2]


def test_attacks_list_and_filter(full: Path) -> None:
    c = TestClient(main.app)
    all_ = c.get("/api/arena/attacks").json()
    assert [a["key"] for a in all_] == ["1-a00", "1-a01", "2-a00"]
    r2 = c.get("/api/arena/attacks", params={"round": 2}).json()
    assert [a["key"] for a in r2] == ["2-a00"]


def test_attack_detail(full: Path) -> None:
    c = TestClient(main.app)
    a = c.get("/api/arena/attacks/1-a00").json()
    assert a["parsed"]["iban"]["citations"][0]["page"] == 1
    assert a["decisions"] == {"guarded": "block"}
    assert c.get("/api/arena/attacks/2-a00").json()["parsed"] is None
    assert c.get("/api/arena/attacks/1-a01").json()["parsed"] is None
    assert c.get("/api/arena/attacks/9-zz").status_code == 404


def test_page_png(full: Path) -> None:
    c = TestClient(main.app)
    r = c.get("/api/arena/attacks/1-a00/page.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert float(r.headers["X-Page-Width"]) == pytest.approx(595, abs=1)
    assert float(r.headers["X-Page-Height"]) == pytest.approx(842, abs=1)
    assert c.get("/api/arena/attacks/1-a01/page.png").status_code == 404  # outside data
    assert c.get("/api/arena/attacks/2-a00/page.png").status_code == 404  # missing file
    assert c.get("/api/arena/attacks/9-zz/page.png").status_code == 404


def test_report(data: Path) -> None:
    c = TestClient(main.app)
    assert c.get("/api/arena/report").json() == {}
    (data / "arena").mkdir()
    (data / "arena" / "report.json").write_text('{"rounds": 2}')
    assert c.get("/api/arena/report").json() == {"rounds": 2}


def test_usage_empty(data: Path) -> None:
    u = TestClient(main.app).get("/api/usage").json()
    assert u["nace"] == main.NACE_ZERO
    assert set(u["intern"].values()) == {0}
    assert set(u["arena"].values()) == {0}


def test_usage_full(full: Path) -> None:
    u = TestClient(main.app).get("/api/usage").json()
    # Fixture lines carry no run_id: one legacy run, its last line counts.
    assert u["nace"] == {"drex_calls": 6, "drex_questions": 7, "drex_input_tokens": 8,
                         "ndi_jobs": 9, "ndi_pages": 10, "search_calls": 0, "workspace_files": 0}
    assert u["intern"] == {"runs": 2, "turns": 8, "drex_asks": 14,
                           "input_tokens": 200, "output_tokens": 40}
    assert u["arena"] == {"rounds": 2, "attacks": 3, "attacker_input_tokens": 150,
                          "attacker_output_tokens": 15, "attacker_turns": 5}


def test_history_route(data: Path) -> None:
    (data / "history").mkdir()
    saved = {
        "supplier_id": "cobalt-wholesale-ltd", "mode": "agent", "wall_ms": 4321, "confidence": 0.8,
        "matches_dataset": True,
        "structured_answer": {"ibans": [{"iban": "gb18 1790", "bic": "B", "bank_name": "N", "documents_seen": 5}]},
        "evidences": [{"source_path": "invoices/cobalt/x.pdf", "page": 1, "quote": "IBAN: GB18 1790"}],
    }
    (data / "history" / "cobalt-wholesale-ltd.json").write_text(json.dumps(saved))
    r = TestClient(main.app).get("/api/history/cobalt-wholesale-ltd")
    assert r.status_code == 200
    body = r.json()
    assert body["wall_ms"] == 4321 and body["mode"] == "agent"
    assert body["ibans"][0]["iban"] == "GB181790"
    assert body["distinct_ibans"] == 1 and body["matches_dataset"] is True
    assert body["evidence"] == [{"file": "x.pdf", "page": 1, "quote": "IBAN: GB18 1790"}]


def test_history_route_missing(data: Path) -> None:
    assert TestClient(main.app).get("/api/history/cobalt-wholesale-ltd").status_code == 404


def test_history_route_bad_id(data: Path) -> None:
    assert TestClient(main.app).get("/api/history/Bad_ID").status_code == 404


def test_websocket_hello(data: Path) -> None:
    with TestClient(main.app) as c:
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json() == {"type": "hello"}


def test_decode_text_variants() -> None:
    assert main.decode_text("é".encode("utf-8-sig")) == "é"
    assert main.decode_text("é".encode("utf-16")) == "é"
    assert main.decode_text(b"plain") == "plain"
