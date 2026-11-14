import json
from pathlib import Path

from backend.app import history


def _save(data: Path, h: dict, name: str = "cobalt-wholesale-ltd") -> None:
    (data / "history").mkdir(exist_ok=True)
    (data / "history" / f"{name}.json").write_text(json.dumps(h), encoding="utf-8")


SAVED = {
    "supplier_id": "cobalt-wholesale-ltd",
    "mode": "agent",
    "confidence": 0.9,
    "wall_ms": 1000,
    "matches_dataset": True,
    "structured_answer": {"ibans": [
        {"iban": "GB18 1790 8734", "bic": "ABCDGB2L", "bank_name": "Bank", "documents_seen": 40},
        {"iban": "gb1817908734", "bic": "ABCDGB2L", "bank_name": "Bank", "documents_seen": "2"},
        {"iban": "", "bic": None},
    ]},
    "evidences": [
        {"source_path": "invoices/cobalt-wholesale-ltd/a.pdf", "page": 1, "quote": "IBAN: GB18", "relevance": 0.9},
        {"source_path": "invoices/cobalt-wholesale-ltd/b.pdf", "page": 1, "quote": "", "relevance": 0.5},
        {"source_path": "invoices/cobalt-wholesale-ltd/c.pdf", "page": 1, "quote": "q2"},
        {"source_path": "d.pdf", "page": 2, "quote": "q3"},
        {"source_path": "e.pdf", "page": 2, "quote": "q4"},
    ],
}


def test_summary_from_saved_file(tmp_path: Path) -> None:
    _save(tmp_path, SAVED)
    h = history.load_history("cobalt-wholesale-ltd", tmp_path)
    assert h is not None
    s = history.history_summary(h)
    assert s["ibans"] == [{"iban": "GB1817908734", "bic": "ABCDGB2L", "bank_name": "Bank", "documents_seen": 42}]
    assert s["distinct_ibans"] == 1
    assert s["documents_seen"] == 42
    assert s["confidence"] == 0.9
    assert s["matches_dataset"] is True
    assert s["status"] == "done"
    assert s["evidence"] == [
        {"file": "a.pdf", "page": 1, "quote": "IBAN: GB18"},
        {"file": "c.pdf", "page": 1, "quote": "q2"},
        {"file": "d.pdf", "page": 2, "quote": "q3"},
    ]
    assert "invoices/" not in json.dumps(s)


def test_missing_file(tmp_path: Path) -> None:
    assert history.load_history("cobalt-wholesale-ltd", tmp_path) is None


def test_invalid_id_and_bad_json(tmp_path: Path) -> None:
    assert history.load_history("../etc", tmp_path) is None
    assert history.history_path("Bad_ID", tmp_path) is None
    (tmp_path / "history").mkdir()
    (tmp_path / "history" / "x.json").write_text("{not json")
    assert history.load_history("x", tmp_path) is None


def test_mismatch_flagged(tmp_path: Path) -> None:
    _save(tmp_path, {**SAVED, "matches_dataset": False, "structured_answer": {"ibans": [
        {"iban": "DE89 3704 0044 0532 0130 00", "documents_seen": 3},
        {"iban": "GB18 1790", "documents_seen": None},
    ]}})
    s = history.history_summary(history.load_history("cobalt-wholesale-ltd", tmp_path) or {})
    assert s["matches_dataset"] is False
    assert s["distinct_ibans"] == 2
    assert s["documents_seen"] == 3


def test_ingesting_status_without_answer() -> None:
    s = history.history_summary({"status": "ingesting"})
    assert s["status"] == "ingesting"
    assert s["ibans"] == [] and s["distinct_ibans"] == 0 and s["documents_seen"] is None
    assert s["evidence"] == []
