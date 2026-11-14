"""Run one of the shared-state decision lanes."""

from __future__ import annotations

from billproof.decisions.escalation import route
from billproof.schema import Answer, Case, DecisionResult, GoldItem, Question


def run_lane(
    lane: str,
    case_or_item: Case | dict,
    questions: list[Question],
    state: dict,
    *,
    gold: dict[str, GoldItem] | None = None,
) -> DecisionResult:
    from billproof.adapters.drex import get_drex

    case_id = case_or_item.case_id if isinstance(case_or_item, Case) else str(case_or_item.get("case_id", "unknown"))
    if lane == "frontier":
        from billproof.adapters.frontier import get_frontier

        adapter = get_frontier()
        return adapter.decide(state, questions, case_id=case_id, gold=gold if adapter.simulated else None)
    if lane == "drex":
        adapter = get_drex()
        return adapter.decide(state, questions, case_id=case_id, gold=gold if adapter.simulated else None)
    if lane != "hybrid":
        raise ValueError(f"unknown decision lane: {lane}")

    adapter = get_drex()
    drex = adapter.decide(state, questions, case_id=case_id, gold=gold if adapter.simulated else None)
    flags = state.get("flags", [])
    verify_questions: list[Question] = []
    answers: dict[str, Answer] = {}
    for question in questions:
        answer = drex.answers[question.name]
        selected = route(question, answer, case_or_item, flags)  # type: ignore[arg-type]
        if selected == "frontier_verified":
            verify_questions.append(question)
        else:
            answer.route = selected
            answers[question.name] = answer

    frontier_result = None
    if verify_questions:
        from billproof.adapters.frontier import get_frontier

        drafts = {q.name: drex.answers[q.name] for q in verify_questions}
        # Document geometry and unrelated source text never enter verification.
        verify_state = state
        if isinstance(case_or_item, Case):
            keys = {key for q in verify_questions for key in q.evidence}
            verify_state = {**state, "evidence": {
                key: case_or_item.citations[key].model_dump(include={"doc", "page", "text"})
                for key in keys if key in case_or_item.citations
            }}
        verifier = get_frontier()
        frontier_result = verifier.verify(
            verify_state, verify_questions, drafts, case_id=case_id,
            gold=gold if verifier.simulated else None,
        )
        for question in verify_questions:
            answer = frontier_result.answers[question.name]
            answer.route = "human" if frontier_result.error or answer.raw.get("error") else "frontier_verified"
            answers[question.name] = answer

    results = [drex] + ([frontier_result] if frontier_result is not None else [])
    assert all(result is not None for result in results)
    return DecisionResult(
        lane="hybrid",
        model=f"{drex.model}+{frontier_result.model if frontier_result else get_frontier_model()}",
        answers=answers,
        latency_ms=sum(r.latency_ms for r in results if r is not None),
        input_tokens=sum(r.input_tokens for r in results if r is not None),
        output_tokens=sum(r.output_tokens for r in results if r is not None),
        cost_usd=sum(r.cost_usd for r in results if r is not None),
        simulated=any(r.simulated for r in results if r is not None),
        replayed=any(r.replayed for r in results if r is not None),
        calls=sum(r.calls for r in results),
        error="; ".join(r.error for r in results if r.error) or None,
    )


def get_frontier_model() -> str:
    from billproof.config import settings

    cfg = settings()
    return cfg.frontier_model or "frontier-sim"
