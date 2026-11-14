from billproof.search.ask import grade
from billproof.search.chain import build_letter, load_case, recorded_chain
from billproof.search.client import answer_of
from billproof.search.corpus import locate_quote


def test_answer_of_fact_and_agent_shapes():
    fact = {"result": {"routing": {"final": "fact"}, "output": {"answer": "180 days", "structured_answer": {"days": 180},
                                                                "confidence": "high", "evidences": [{"quote": "x"}]}}}
    agent = {"result": {"routing": {"final": "agent"}, "output": {"answer": {"answer": "yes", "structured_answer": {"ok": True},
                                                                             "confidence": "certain", "evidences": [{}, {}]}}}}
    assert answer_of(fact)["structured_answer"] == {"days": 180} and len(answer_of(fact)["evidences"]) == 1
    assert answer_of(agent)["structured_answer"] == {"ok": True} and len(answer_of(agent)["evidences"]) == 2


def test_quote_check_rejects_paraphrase():
    real = "Prior authorization is not required for imaging performed during an emergency department visit."
    assert locate_quote("SYN-D08", "SYN-D08/SYN-D08_coverage.pdf", real) == {"verbatim": True, "page": 2}
    assert not locate_quote("SYN-D08", "SYN-D08/SYN-D08_coverage.pdf", "Emergency CT never needs approval.")["verbatim"]


def test_grade():
    assert grade({"days": 180, "ok": False, "dx": "adenitis"}, {"days": 180, "ok": False, "dx": "Mesenteric adenitis"})
    assert not grade({"ok": False}, {"ok": None})


def test_letter_cites_recorded_clause():
    chain = recorded_chain("SYN-D08")
    letter = build_letter(load_case("SYN-D08"), chain["letter_support"])
    clause = next(c for c in letter["clauses"] if c["clause_id"] == "plan_language")
    assert chain["letter_support"][0]["quote"] in clause["text"] and "page 2" in clause["text"]
    assert letter["generated_words"] == 0
