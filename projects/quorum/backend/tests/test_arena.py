"""Arena tests: no network, fakes only."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from backend.app import arena
from backend.app.contracts import Decision, ParsedInvoice, TrustedSupplier, DecisionUsage


def _dec(final: str | None, raw: str | None = None, error: str | None = None,
         risk: float | None = 0.5, tokens: int = 1000) -> dict:
    return Decision(raw_action=raw or final, final_action=final, error=error, risk_score=risk,
                    wall_ms=10, usage=DecisionUsage(input_tokens=tokens, output_tokens=5)
                    ).model_dump()


@pytest.fixture()
def tmp_arena(tmp_path, monkeypatch):
    monkeypatch.setattr(arena, "ARENA", tmp_path / "arena")
    monkeypatch.setattr(arena, "SANDBOX", tmp_path / "arena" / "sandbox")
    return tmp_path / "arena"


def test_feedback_only_allowed_keys():
    hist = [
        {"round": 1, "kind": "forged", "note": "n1", "style": "body", "pdf": "x",
         "decisions": {"drex": _dec("approve", risk=0.12345), "drex+memory": _dec("block")}},
        {"round": 1, "kind": "forged", "note": "n2", "style": "faint",
         "decisions": {"drex": _dec(None, error="boom")}},
        {"round": 1, "kind": "clean", "note": None, "decisions": {"drex": _dec("approve")}},
    ]
    fb = arena.feedback(hist)
    assert [set(x) for x in fb] == [{"round", "note", "style", "outcome"}] * 2
    assert [x["outcome"] for x in fb] == ["approved", "error"]
    fb = arena.feedback(hist, with_risk=True)
    assert set(fb[0]) == {"round", "note", "style", "outcome", "risk"}
    assert fb[0]["risk"] == 0.123 and fb[1]["risk"] is None


def test_score_counts():
    recs = [
        {"kind": "forged", "decisions": {"drex": _dec("verify", raw="approve")}},
        {"kind": "forged", "decisions": {"drex": _dec("approve")}},
        {"kind": "forged", "decisions": {"drex": _dec("block")}},
        {"kind": "forged", "decisions": {"drex": _dec(None, error="x", tokens=0)}},
        {"kind": "clean", "decisions": {"drex": _dec("verify")}},
        {"kind": "clean", "decisions": {"drex": _dec("approve")}},
        {"kind": "clean", "decisions": {"drex": _dec(None, error="x", tokens=0)}},
    ]
    s = arena.score("drex", recs)
    assert s["raw_breaches"] == 2 and s["breaches"] == 1
    assert s["caught"] == 2 and s["failures"] == 2 and s["false_alarms"] == 1
    assert s["input_tokens"] == 5000 and s["cost_usd"] == pytest.approx(5000 * 0.03 / 1e6)
    assert arena.score("other", recs)["cost_usd"] is None


def test_score_sums_reported_cost_for_non_drex_defender():
    """The LLM guard's Decision carries cost_usd; the round score is their sum."""
    def guard(final: str | None, cost: float | None, error: str | None = None) -> dict:
        d = _dec(final, error=error)
        d["cost_usd"] = cost
        return d

    recs = [
        {"kind": "forged", "decisions": {"llm_guard": guard("block", 0.002)}},
        {"kind": "forged", "decisions": {"llm_guard": guard(None, 0.001, error="bad json")}},
        {"kind": "clean", "decisions": {"llm_guard": guard(None, None, error="timeout")}},
    ]
    s = arena.score("llm_guard", recs)
    assert s["cost_usd"] == pytest.approx(0.003)
    assert s["failures"] == 2 and s["breaches"] == 0


def test_memory_dedup_and_cap():
    mem = arena.update_memory(["a", "b"], ["b", "c"])
    assert mem == ["a", "b", "c"]
    mem = arena.update_memory([], [f"n{i}" for i in range(40)] + ["n0"])
    assert len(mem) == 30 and mem[-1] == "n0" and "n10" not in mem and mem.count("n0") == 1


def test_caught_notes_errors_not_caught():
    recs = [
        {"kind": "forged", "note": "x", "decisions": {"drex": _dec("approve"),
                                                       "drex+memory": _dec("verify")}},
        {"kind": "forged", "note": "y", "decisions": {"drex": _dec(None, error="e")}},
        {"kind": "forged", "note": "z", "decisions": {"drex": _dec("approve")}},
        {"kind": "clean", "note": None, "decisions": {"drex": _dec("block")}},
    ]
    assert arena.caught_notes(recs) == ["x"]


def test_parse_attacks_bad_lines():
    text = "\n".join([
        "garbage", json.dumps({"note": "A" * 500, "style": "faint"}), "[1,2]",
        json.dumps({"note": "b", "style": "weird"}), json.dumps({"style": "body"}),
        json.dumps({"note": "c"}), json.dumps({"note": "d"}),
    ])
    out = arena.parse_attacks(text, 3)
    assert out == [{"note": "A" * 400, "style": "faint"}, {"note": "b", "style": "body"},
                   {"note": "c", "style": "body"}]


def test_parse_run_end():
    out = "\n".join(["noise", json.dumps({"type": "run_end", "turns": 4,
                    "usage": {"input_tokens": 10, "output_tokens": 3}, "system1": {"asks": 2}})])
    assert arena.parse_run_end(out) == {"turns": 4, "input_tokens": 10, "output_tokens": 3,
                                        "drex_asks": 2}


def test_missing_baseline_unavailable(monkeypatch):
    monkeypatch.setenv("ARENA_DREX_MODELS", "")
    # Simulate a missing module. Once baseline.py exists and another test has imported it,
    # `from backend.app import baseline` finds it as an attribute of the package and never
    # looks at sys.modules, so the attribute has to go as well.
    import backend.app as app_pkg

    monkeypatch.delattr(app_pkg, "baseline", raising=False)
    monkeypatch.setitem(sys.modules, "backend.app.baseline", None)
    defenders, unavailable = arena.get_defenders()
    assert unavailable == ["llm_guard"] and set(defenders) == {"drex", "drex+memory"}


def test_run_attacker_with_fake_cmd(tmp_arena, tmp_path, monkeypatch):
    script = tmp_path / "fake_intern.py"
    script.write_text(
        "import json\n"
        "assert json.load(open('feedback.json')) == []\n"
        "open('attacks.jsonl','w').write('bad\\n' + json.dumps({'note':'hi','style':'faint'}))\n"
        "print(json.dumps({'type':'run_end','turns':2,'usage':{'input_tokens':7,'output_tokens':1}}))\n")
    monkeypatch.setenv("INTERN_CMD", f"{sys.executable} {script}")
    res = asyncio.run(arena.run_attacker(1, 5, model="m", history=[]))
    assert res["attacks"] == [{"note": "hi", "style": "faint"}]
    assert res["turns"] == 2 and res["input_tokens"] == 7 and res["error"] is None


def test_run_attacker_no_attacks_is_error(tmp_arena, monkeypatch):
    monkeypatch.setenv("INTERN_CMD", "true")
    res = asyncio.run(arena.run_attacker(1, 5, model="m", history=[]))
    assert res["attacks"] == [] and res["error"]


def test_build_trusted_record():
    srcs = [{"total": "10.00", "currency": "GBP", "number": "B"},
            {"total": "30.00", "currency": "GBP", "number": "A"}]
    rec = arena.build_trusted_record(srcs, {"name": "Cobalt", "bank": {"iban": "GB18 1790", "bic": "X"}})
    assert rec["median_amount_minor"] == 2000 and rec["paid_invoice_numbers"] == ["A", "B"]
    assert rec["iban"] == "GB181790" and rec["names"] == ["Cobalt"]


def test_run_round_with_fakes(tmp_arena, monkeypatch):
    seen_memory: list[list[str]] = []
    forged_iban_notes: list[str] = []
    rnd = {"n": 0}

    async def fake_attacker(round_n, n, model=None, history=None, with_risk=False,
                            mode="swap"):
        rnd["n"] += 1
        notes = [{"note": f"r{round_n}-ok", "style": "body"},
                 {"note": f"r{round_n}-bad", "style": "faint"},
                 {"note": "forge-fails {IBAN}", "style": "body"}]
        return {"attacks": notes[:n], "turns": 1, "input_tokens": 1, "output_tokens": 1,
                "drex_asks": 0, "wall_ms": 1, "error": None}

    def fake_forge(doc_id, note, style="body", new_iban=None, out_path=None):
        assert note is None or "{IBAN}" not in note
        if note and note.startswith("forge-fails"):
            assert new_iban and note == f"forge-fails {new_iban}"
            forged_iban_notes.append(note)
            raise ValueError("note too long")
        p = Path(out_path) if out_path else tmp_arena / "clean" / f"{doc_id}.pdf"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(note or "clean")
        return {"path": str(p), "doc_id": doc_id, "printed_iban": new_iban, "note": note}

    async def fake_parse(path):
        text = Path(path).read_text()
        if text.endswith("bad"):
            raise RuntimeError("ndi down")
        return ParsedInvoice(doc_id=Path(path).stem, content_hash=text)

    async def drex(parsed, facts, known):
        seen_memory.append(known)
        return Decision(doc_id=parsed.doc_id, raw_action="approve",
                        final_action="approve" if parsed.content_hash.endswith("ok") or
                        parsed.content_hash == "clean" else "verify", risk_score=0.1)

    async def drex_mem(parsed, facts, known):
        if parsed.content_hash.endswith("ok"):
            return Decision(doc_id=parsed.doc_id, raw_action="block", final_action="block")
        return Decision(doc_id=parsed.doc_id, raw_action="approve", final_action="approve")

    monkeypatch.setattr(arena, "run_attacker", fake_attacker)
    monkeypatch.setattr(arena.forge, "forge_invoice", fake_forge)
    monkeypatch.setattr(arena.intake, "parse_document", fake_parse)
    monkeypatch.setattr(arena, "prepare_trusted", lambda: {"c": TrustedSupplier()})
    monkeypatch.setattr(arena.checks, "facts", lambda p, t: arena.Facts())
    monkeypatch.setattr(arena, "get_defenders",
                        lambda: ({"drex": drex, "drex+memory": drex_mem}, ["llm_guard"]))
    monkeypatch.setattr(arena, "arena_sources",
                        lambda: [{"doc_id": f"d{i}"} for i in range(10)])
    events: list[dict] = []

    r1 = asyncio.run(arena.run_round(1, 3, 2, on_event=events.append, mode="swap"))
    assert all(m == [] for m in seen_memory)  # round 1 sees no own notes
    d = r1["defenders"]["drex"]
    assert d["breaches"] == 1 and d["raw_breaches"] == 1 and d["failures"] == 2
    assert d["false_alarms"] == 0 and d["caught"] == 0
    assert r1["defenders"]["drex+memory"]["caught"] == 1
    assert r1["unavailable"] == ["llm_guard"] and "llm_guard" not in r1["defenders"]
    assert "attacks" not in r1["attacker"]
    assert r1["with_risk"] is False and r1["known_attacks_used"] == 0
    assert len(forged_iban_notes) == 1
    stored = [r["note"] for r in arena.load_history() if (r["note"] or "").startswith("forge")]
    assert stored == forged_iban_notes  # the record keeps the filled note
    assert arena.load_memory() == ["r1-ok"]
    recs = arena.load_history()
    assert len(recs) == 5 and {r["kind"] for r in recs} == {"forged", "clean"}
    assert set(recs[0]) == {"round", "mode", "id", "kind", "note", "style", "pdf",
                            "doc_hash", "decisions"}
    types = [e["type"] for e in events]
    assert types[0] == "round_start" and types[-1] == "round_end" and types.count("decision") == 5

    assert arena.next_round() == 2
    seen_memory.clear()
    r2 = asyncio.run(arena.run_round(2, 3, 2, with_risk=True, mode="swap"))
    assert r2["with_risk"] is True and r2["known_attacks_used"] == 1
    assert seen_memory and all(m == ["r1-ok"] for m in seen_memory)
    assert arena.load_memory() == ["r1-ok", "r2-ok"]
    assert arena.next_round() == 3


def test_pick_sources_disjoint():
    srcs = [{"doc_id": f"d{i}"} for i in range(120)]
    f, c = arena.pick_sources(2, 20, 5, srcs)
    assert len(f) == 20 and len(c) == 5 and not set(f) & set(c)
    assert f[0] == "d25"


def test_run_arena_helpers():
    sys.path.insert(0, str(Path(arena.config.ROOT / "backend" / "scripts")))
    import run_arena

    rec = {"round": 1, "n_attacks": 6, "attacker": {"input_tokens": 1, "output_tokens": 2},
           "defenders": {"drex": {"raw_breaches": 0, "breaches": 0, "false_alarms": 0,
                                  "failures": 0}}, "unavailable": ["llm_guard"]}
    assert run_arena.should_enable_risk(rec, False)
    assert not run_arena.should_enable_risk(rec, True)
    assert "drex raw=0" in run_arena.format_round(rec)


def test_run_attacker_nonzero_exit_with_notes_is_warning(tmp_arena, tmp_path, monkeypatch):
    script = tmp_path / "fake_intern_exit1.py"
    script.write_text(
        "import json, sys\n"
        "open('attacks.jsonl','w').write(json.dumps({'note':'hi','style':'body'}))\n"
        "print(json.dumps({'type':'run_end','turns':20,'usage':{'input_tokens':9,'output_tokens':1}}))\n"
        "sys.exit(1)\n")
    monkeypatch.setenv("INTERN_CMD", f"{sys.executable} {script}")
    res = asyncio.run(arena.run_attacker(1, 5, model="m", history=[]))
    assert res["attacks"] == [{"note": "hi", "style": "body"}]
    assert res["error"] is None and res["warning"] == "attacker exit code 1"
    assert res["turns"] == 20


def test_run_attacker_nonzero_exit_without_notes_is_error(tmp_arena, monkeypatch):
    monkeypatch.setenv("INTERN_CMD", "false")
    res = asyncio.run(arena.run_attacker(1, 5, model="m", history=[]))
    assert res["attacks"] == [] and res["error"] == "attacker exit code 1"
    assert res["warning"] is None


def test_fill_iban():
    assert arena.fill_iban("pay to {IBAN} now", "GB00X1") == "pay to GB00X1 now"
    assert arena.fill_iban("{IBAN} / {IBAN}", "DE1") == "DE1 / DE1"
    assert arena.fill_iban("no token", "DE1") == "no token"


def test_score_risk_stats():
    def dec(final, risk, p_app):
        d = _dec(final, risk=risk)
        d["p"] = {"approve": p_app, "verify": 0.5, "block": 0.5 - p_app}
        return d
    recs = [
        {"kind": "forged", "decisions": {"drex": dec("verify", 0.6, 0.2)}},
        {"kind": "forged", "decisions": {"drex": dec("approve", 0.1, 0.45)}},
        {"kind": "forged", "decisions": {"drex": dec("block", 0.9, 0.05)}},
        {"kind": "forged", "decisions": {"drex": _dec(None, error="x", risk=0.0)}},
        {"kind": "clean", "decisions": {"drex": dec("approve", 0.01, 0.49)}},
    ]
    s = arena.score("drex", recs)
    assert s["min_risk"] == 0.1 and s["median_risk"] == 0.6 and s["max_p_approve"] == 0.45
    empty = arena.score("drex", [{"kind": "clean", "decisions": {"drex": _dec("approve")}}])
    assert empty["min_risk"] is None and empty["median_risk"] is None
    assert empty["max_p_approve"] is None


def _run_arena_mod():
    sys.path.insert(0, str(Path(arena.config.ROOT / "backend" / "scripts")))
    import run_arena
    return run_arena


def test_archive_arena(tmp_path):
    ra = _run_arena_mod()
    a = tmp_path / "arena"
    (a / "sandbox").mkdir(parents=True)
    for name in ("trusted.json", "attacks.jsonl", "rounds.jsonl", "known_attacks.json"):
        (a / name).write_text("x")
    dest = ra.archive_arena(a, tmp_path / "archive", "20261114T120000Z")
    assert dest == tmp_path / "archive" / "20261114T120000Z"
    assert sorted(p.name for p in a.iterdir()) == ["trusted.json"]
    assert sorted(p.name for p in dest.iterdir()) == [
        "attacks.jsonl", "known_attacks.json", "rounds.jsonl", "sandbox"]
    assert ra.archive_arena(a, tmp_path / "archive") is None
    assert ra.archive_arena(tmp_path / "missing", tmp_path / "archive") is None


def test_format_round_risk_and_warning():
    ra = _run_arena_mod()
    rec = {"round": 1, "n_attacks": 6,
           "attacker": {"input_tokens": 1, "output_tokens": 2, "warning": "attacker exit code 1"},
           "defenders": {"drex": {"raw_breaches": 0, "breaches": 0, "false_alarms": 0,
                                  "failures": 0},
                         "drex+memory": {"raw_breaches": 0, "breaches": 0, "false_alarms": 0,
                                         "failures": 0, "min_risk": 0.5312}}}
    line = ra.format_round(rec)
    assert line.startswith("round 1 mode=swap:")
    assert "min_risk=- |" in line and "min_risk=0.531 |" in line
    assert "warning=attacker exit code 1" in line


# ---- arena v2 -------------------------------------------------------------

def test_mode_helpers_and_feedback_filter():
    assert arena.mode_of({}) == "swap" and arena.mode_of({"mode": "redirect"}) == "redirect"
    assert arena.kind_for("redirect") == "poisoned" and arena.kind_for("swap") == "forged"
    hist = [
        {"kind": "forged", "note": "old", "decisions": {"drex": _dec("block")}},
        {"kind": "poisoned", "mode": "redirect", "note": "pay {IBAN}",
         "decisions": {"drex": _dec("approve")}},
    ]
    assert [f["note"] for f in arena.feedback(hist, mode="swap")] == ["old"]
    assert [f["note"] for f in arena.feedback(hist, mode="redirect")] == ["pay {IBAN}"]


def test_parse_attacks_redirect_needs_iban():
    text = "\n".join(json.dumps(x) for x in [
        {"note": "send to {IBAN} now", "style": "body"},
        {"note": "no token here", "style": "body"}])
    assert [a["note"] for a in arena.parse_attacks(text, 5, "redirect")] == ["send to {IBAN} now"]
    assert len(arena.parse_attacks(text, 5, "swap")) == 2


def test_score_counts_poisoned_as_attack():
    recs = [{"kind": "poisoned", "decisions": {"drex": {**_dec("approve"),
                                                        "model": "m1"}}}]
    s = arena.score("drex", recs)
    assert s["breaches"] == 1 and s["model"] == "m1"


def test_extra_drex_models(monkeypatch):
    monkeypatch.setattr(arena.config, "DREX_MODEL", "a")
    monkeypatch.setenv("ARENA_DREX_MODELS", "a, b,b,,c")
    assert arena.extra_drex_models() == ["b", "c"]
    defs, _ = arena.get_defenders()
    assert "b" in defs and "c" in defs and "a" not in defs
    monkeypatch.setenv("ARENA_DREX_MODELS", "")
    assert arena.extra_drex_models() == []


def test_model_defender_passes_model(monkeypatch):
    seen = {}

    async def fake_decide(parsed, facts, system="drex", model=None, **kw):
        seen.update(system=system, model=model)
        return Decision(system=system, model=model or "")

    monkeypatch.setattr(arena.panel, "decide", fake_decide)
    d = asyncio.run(arena._model_defender("m2")(ParsedInvoice(), arena.Facts(), []))
    assert seen == {"system": "m2", "model": "m2"} and d.model == "m2"


def test_recalled_pages():
    out = "\n".join([
        json.dumps({"type": "memory_recall", "pages": [{"name": "p1"}, "p2"]}),
        "not json",
        json.dumps({"type": "turn"}),
        json.dumps({"type": "memory_recall", "page": "p1"}),
    ])
    assert arena.recalled_pages(out) == ["p1", "p2"]
    assert arena.recalled_pages("") == []


def test_attacker_env_and_home(tmp_arena):
    env = arena.attacker_env(Path("/x/h"), {"PATH": "/bin", "INTERN_HOME": "/old"})
    assert env == {"PATH": "/bin", "INTERN_HOME": "/x/h"}
    h1 = arena.new_attacker_home(3)
    h2 = arena.new_attacker_home(3)
    assert h1.is_dir() and h2.is_dir() and h1 != h2 and h2.name == "round-3-2"


def test_run_round_unknown_mode(tmp_arena):
    with pytest.raises(ValueError):
        asyncio.run(arena.run_round(1, 1, 0, mode="nope"))


def test_make_items_redirect(tmp_arena, monkeypatch):
    calls = []

    def fake_forge(doc_id, note, style="body", new_iban=None, out_path=None):
        calls.append((note, new_iban))
        return {"path": str(out_path), "doc_id": doc_id}

    monkeypatch.setattr(arena.forge, "forge_invoice", fake_forge)
    items = arena._make_items(1, [{"note": "pay {IBAN}", "style": "body"}], ["d1"], [],
                              "redirect")
    assert items[0]["kind"] == "poisoned"
    note, new_iban = calls[0]
    assert new_iban is None and "{IBAN}" not in note and note.startswith("pay ")


def test_redirect_prompt_renders():
    text = arena.render_prompt(4, 8, "redirect")
    assert "round 4" in text and "8 different" in text and "{IBAN}" in text
    assert "{n}" not in text and "{round}" not in text


def test_round_line_mode_stored(tmp_arena, monkeypatch):
    async def fake_attacker(round_n, n, model=None, history=None, with_risk=False, mode="swap"):
        return {"attacks": [{"note": "pay {IBAN}", "style": "body"}], "error": None,
                "isolated": True, "intern_home": "h", "recalled_pages": []}

    monkeypatch.setattr(arena, "run_attacker", fake_attacker)
    monkeypatch.setattr(arena, "_make_items", lambda *a, **k: [])
    monkeypatch.setattr(arena, "get_defenders", lambda: ({}, []))
    try:
        rec = asyncio.run(arena.run_round(1, 1, 0, mode="redirect"))
    except Exception as exc:  # pragma: no cover - surface shape problems clearly
        pytest.fail(f"run_round failed: {exc}")
    assert rec["mode"] == "redirect"
    assert rec["attacker"]["isolated"] is True and rec["attacker"]["recalled_pages"] == []
