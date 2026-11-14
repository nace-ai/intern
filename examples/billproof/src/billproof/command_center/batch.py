"""Pack many prior-auth questions into one Drex /v1/systemone call.

State shape matches the stress pilot that already ran: ``{"packets":
[{"packet_id", "state"}]}``. Each question's instructions name its packet.
Calls stay at or under 200 questions: a 507-question pack kept decisions
and lost facts. HTTP 400/413/422/timeouts split the batch. 429s are retried
inside ``DrexAdapter`` (retry-after-ms) and again here.
"""

from __future__ import annotations

import contextvars
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from billproof.adapters.drex import DrexAdapter, _to_answer
from billproof.bench.pa.cases import PaCase
from billproof.bench.pa.questions import build_questions
from billproof.command_center.common import (
    DREX_LOCK_PATH,
    DREX_MODEL,
    MAX_QUESTIONS,
    add_spend,
    can_spend,
    estimate_nace_usd,
    file_lock,
    scrub,
)
from billproof.command_center.rows import needs_accuracy_split
from billproof.config import settings
from billproof.schema import Question

PREFIX = (
    'This question belongs to packet_id "{packet_id}". '
    "Read only the packet with that packet_id. Ignore every other packet.\n"
)

# The full-scale run and the new-criterion endpoint use their own cap.
# Unset, the original ledger is used.
budget_ok: contextvars.ContextVar[Callable[[float], bool] | None] = contextvars.ContextVar(
    "cc_budget_ok", default=None,
)
budget_add: contextvars.ContextVar[Callable[[float], None] | None] = contextvars.ContextVar(
    "cc_budget_add", default=None,
)


def spend_ok(estimate: float) -> bool:
    fn = budget_ok.get()
    if fn is None:
        return can_spend("nace", estimate)
    return bool(fn(estimate))


def spend_add(amount: float) -> None:
    fn = budget_add.get()
    if fn is None:
        add_spend("nace", amount)
        return
    fn(amount)


class BudgetStop(RuntimeError):
    """Local Nace cap reached. The run is resumable.

    ``kept`` is the prefix of this pack that already returned, in order.
    Callers write that prefix before stopping. The unstarted tail is not
    an error row, so a cap does not burn the two-strike skip.
    """

    def __init__(self, message: str = "", *, kept: list | None = None):
        super().__init__(message)
        self.kept = list(kept or [])


class SplitBatch(RuntimeError):
    pass


def _is_fatal(exc: BaseException) -> bool:
    if type(exc).__name__ == "ModelMismatch":
        return True
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in (401, 403)


def _error_results(items: list[WorkItem], exc: BaseException) -> list[ItemResult]:
    message = scrub(str(exc)) or type(exc).__name__
    return [
        ItemResult(packet_id=item.packet_id, error=message, calls=0, cost_usd=0.0)
        for item in items
    ]


def _halve(items: list[WorkItem], call) -> list[ItemResult]:
    """Run two halves. A half that already returned is kept if the other raises.

    A non-fatal failure becomes one error result per item in that half.
    BudgetStop and auth/model failures re-raise with ``kept`` set to the
    prefix that finished, including a successful sibling.
    """
    mid = max(1, len(items) // 2)
    left_items, right_items = items[:mid], items[mid:]

    def _kept(exc: BaseException) -> list[ItemResult]:
        return list(getattr(exc, "kept", []) or [])

    try:
        left = call(left_items)
    except BudgetStop as exc:
        raise BudgetStop(str(exc), kept=_kept(exc)) from exc
    except Exception as exc:
        if _is_fatal(exc):
            exc.kept = _kept(exc)
            raise
        print(f"[cc] half failed {scrub(str(exc))}", flush=True)
        left = _error_results(left_items, exc)
    try:
        right = call(right_items)
    except BudgetStop as exc:
        raise BudgetStop(str(exc), kept=left + _kept(exc)) from exc
    except Exception as exc:
        if _is_fatal(exc):
            exc.kept = left + _kept(exc)
            raise
        print(f"[cc] half failed {scrub(str(exc))}", flush=True)
        right = _error_results(right_items, exc)
    return left + right


def consume_pack(items: list[WorkItem], call) -> tuple[list[tuple[WorkItem, ItemResult]], BaseException | None]:
    """Run ``call`` over ``items``.

    Returns ``(pairs, stop)``. ``pairs`` is the prefix to write. ``stop`` is
    a BudgetStop or a fatal error when the caller must not continue. A
    non-fatal escape writes one error result for each item that has no
    result yet, and does not mark the kept prefix as failed.
    """
    try:
        got = list(call())
        return list(zip(items, got)), None
    except BudgetStop as exc:
        return list(zip(items, list(exc.kept))), exc
    except Exception as exc:
        got = list(getattr(exc, "kept", []) or [])
        pairs = list(zip(items, got))
        if _is_fatal(exc):
            return pairs, exc
        message = scrub(str(exc)) or type(exc).__name__
        for item in items[len(got):]:
            pairs.append((item, ItemResult(
                packet_id=item.packet_id, error=message, calls=0, cost_usd=0.0,
            )))
        return pairs, None


@dataclass
class WorkItem:
    packet_id: str
    case: PaCase
    state: dict
    questions: list[Question]
    kind: str = "baseline"  # baseline | ablation
    drop_record: str | None = None


@dataclass
class ItemResult:
    packet_id: str
    choices: dict[str, str] = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    batch_latency_ms: float = 0.0
    batch_cost_usd: float = 0.0
    batch_id: str = ""
    batch_questions: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 1
    model: str = DREX_MODEL
    simulated: bool = False
    error: str | None = None
    resampled: bool = False


def assert_live(allow_sim: bool) -> bool:
    """Return True when the run is simulated. Refuse a silent mock."""
    cfg = settings()
    if cfg.drex_model != DREX_MODEL:
        print(f"[cc] env DREX_MODEL={cfg.drex_model}; pinning {DREX_MODEL}", flush=True)
    simulated = not cfg.use_real("drex")
    if simulated and not allow_sim:
        raise SystemExit(
            "[cc] Drex is not live (no key or MOCK_MODE). "
            "Refusing to write an unlabelled queue. Pass --allow-sim to override."
        )
    return simulated


def make_adapter() -> tuple[Any, bool]:
    cfg = settings()
    if cfg.use_real("drex"):
        adapter = DrexAdapter(
            cfg.nace_api_key, cfg.nace_base_url, DREX_MODEL,
            cfg.drex_price_per_mtok or 0.03,
            timeout=180.0, max_retries=6,
        )
        return adapter, False
    from billproof.adapters.drex import SimulatedDrexAdapter
    return SimulatedDrexAdapter(DREX_MODEL), True


def baseline_item(case: PaCase, *, include_scope: bool = True) -> WorkItem:
    return WorkItem(
        packet_id=case.case_id,
        case=case,
        state=case.state(),
        questions=build_questions(case, include_scope=include_scope),
        kind="baseline",
    )


def ablation_item(case: PaCase, record_id: str) -> WorkItem:
    return WorkItem(
        packet_id=f"{case.case_id}~{record_id}",
        case=case,
        state=case.state(drop_records={record_id}),
        questions=build_questions(case, include_scope=False),
        kind="ablation",
        drop_record=record_id,
    )


def _namespaced(item: WorkItem) -> list[Question]:
    prefix = PREFIX.format(packet_id=item.packet_id)
    out = []
    for q in item.questions:
        out.append(q.model_copy(update={
            "name": f"{item.packet_id}::{q.name}",
            "instructions": prefix + q.instructions,
        }))
    return out


def pack(items: list[WorkItem], *, max_questions: int = MAX_QUESTIONS) -> list[list[WorkItem]]:
    """Greedy packs. One item that itself exceeds the cap is a pack of one
    (the caller will still send it; Drex then errors or answers)."""
    packs: list[list[WorkItem]] = []
    cur: list[WorkItem] = []
    n = 0
    for item in items:
        qn = len(item.questions)
        if cur and n + qn > max_questions:
            packs.append(cur)
            cur, n = [], 0
        cur.append(item)
        n += qn
    if cur:
        packs.append(cur)
    return packs


def _allocate(total: float, weights: list[int]) -> list[float]:
    denom = sum(weights) or 1
    return [total * w / denom for w in weights]


def _call_live(adapter: DrexAdapter, items: list[WorkItem]) -> list[ItemResult]:
    questions: list[Question] = []
    owners: list[tuple[WorkItem, Question]] = []
    packets = []
    seen: set[str] = set()
    for item in items:
        named = _namespaced(item)
        questions.extend(named)
        owners.extend((item, q) for q in named)
        if item.packet_id not in seen:
            packets.append({"packet_id": item.packet_id, "state": item.state})
            seen.add(item.packet_id)
    state = {"packets": packets}
    n_q = len(questions)
    estimate = estimate_nace_usd(state, n_q)
    if not spend_ok(estimate):
        raise BudgetStop(f"nace cap; estimate ${estimate:.4f} for {n_q} questions")
    specs = {q.name: {"type": q.type, "instructions": q.instructions,
                      **({"criteria": q.criteria} if q.criteria is not None else {})}
             for q in questions}
    t0 = time.monotonic()
    with file_lock(DREX_LOCK_PATH):
        try:
            data = adapter._call(state, specs)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (400, 413, 422, 500, 502, 503, 529) and len(items) > 1:
                raise SplitBatch(scrub(f"HTTP {status}")) from exc
            if status == 429 and len(items) > 1:
                raise SplitBatch("429") from exc
            raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if len(items) > 1:
                raise SplitBatch(scrub(type(exc).__name__)) from exc
            raise
    elapsed = (time.monotonic() - t0) * 1000.0
    usage = data.get("usage") or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cost = input_tokens * adapter.price_per_mtok / 1e6
    spend_add(cost)
    wire = data.get("answers") or {}
    batch_id = str(data.get("request_id") or uuid.uuid4())
    by_packet: dict[str, ItemResult] = {}
    weights = [len(item.questions) for item in items]
    cost_shares = _allocate(cost, weights)
    lat_shares = _allocate(elapsed, weights)
    tok_shares = _allocate(float(input_tokens), weights)
    for item, c_share, l_share, t_share in zip(items, cost_shares, lat_shares, tok_shares):
        by_packet[item.packet_id] = ItemResult(
            packet_id=item.packet_id,
            cost_usd=c_share, latency_ms=l_share, batch_latency_ms=elapsed,
            batch_cost_usd=cost, batch_id=batch_id, batch_questions=n_q,
            input_tokens=int(round(t_share)), output_tokens=0,
            model=DREX_MODEL, simulated=False, calls=1,
        )
    missing: dict[str, list[str]] = {item.packet_id: [] for item in items}
    for item, q in owners:
        original = q.name.split("::", 1)[1]
        # packet_id itself contains no '::'; the remainder is the original name,
        # which may contain '::' (fact::clause).
        raw = wire.get(q.name)
        slot = by_packet[item.packet_id]
        if not isinstance(raw, dict) or not (
            raw.get("probabilities") or "noul" in raw or raw.get("choice")
        ):
            missing[item.packet_id].append(original)
            continue
        answer = _to_answer(q, raw)
        # q.name is namespaced; options come from the copied question.
        if answer.choice not in q.options():
            missing[item.packet_id].append(original)
            continue
        slot.choices[original] = answer.choice
        slot.confidence[original] = float(answer.confidence)
    for item in items:
        slot = by_packet[item.packet_id]
        if missing[item.packet_id]:
            slot.error = "missing answers: " + ",".join(missing[item.packet_id][:6])
        elif len(slot.choices) != len(item.questions):
            slot.error = f"answer count {len(slot.choices)} != {len(item.questions)}"
    return [by_packet[item.packet_id] for item in items]


def _call_sim(adapter: Any, items: list[WorkItem]) -> list[ItemResult]:
    batch_id = "sim-" + uuid.uuid4().hex[:12]
    out: list[ItemResult] = []
    t0 = time.monotonic()
    for item in items:
        result = adapter.decide(item.state, item.questions, case_id=item.packet_id)
        choices = {q.name: result.answers[q.name].choice for q in item.questions}
        conf = {q.name: float(result.answers[q.name].confidence) for q in item.questions}
        out.append(ItemResult(
            packet_id=item.packet_id, choices=choices, confidence=conf,
            cost_usd=result.cost_usd, latency_ms=result.latency_ms,
            batch_latency_ms=0.0, batch_cost_usd=0.0, batch_id=batch_id,
            batch_questions=len(item.questions), input_tokens=result.input_tokens,
            output_tokens=result.output_tokens, model=result.model,
            simulated=True, calls=result.calls,
        ))
    elapsed = (time.monotonic() - t0) * 1000.0
    total_cost = sum(r.cost_usd for r in out)
    for r in out:
        r.batch_latency_ms = elapsed
        r.batch_cost_usd = total_cost
    return out


def run_pack(items: list[WorkItem], *, adapter: Any, simulated: bool,
             retries: int = 4) -> list[ItemResult]:
    """One pack. Splits on transport/HTTP errors. Raises BudgetStop.

    A split keeps whichever half already returned when the other half fails.
    """
    if not items:
        return []
    if simulated:
        return _call_sim(adapter, items)
    delay = 5.0
    for attempt in range(retries):
        try:
            return _call_live(adapter, items)
        except SplitBatch as exc:
            if len(items) <= 1:
                raise
            print(f"[cc] split {len(items)} packets ({scrub(str(exc))})", flush=True)

            def _side(subset: list[WorkItem]) -> list[ItemResult]:
                return run_pack(subset, adapter=adapter, simulated=simulated, retries=retries)

            return _halve(items, _side)
        except BudgetStop:
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 and attempt + 1 < retries:
                print(f"[cc] 429 after adapter retries; sleep {delay:.0f}s", flush=True)
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
                continue
            raise
    raise RuntimeError("drex pack failed")  # pragma: no cover


def run_items(items: list[WorkItem], *, adapter: Any, simulated: bool,
              max_questions: int = MAX_QUESTIONS,
              gold_flags: Any = None) -> list[ItemResult]:
    """gold_flags(item, result) -> bool | None.

    When fewer than 95% of cases in a pack got every fact right, the pack is
    re-asked in halves. A single case cannot split. The rejected call was
    already billed and is not written to the JSONL. Re-asked rows are marked
    ``resampled`` so the replay is not confused with the first packed call.
    """
    results: list[ItemResult] = []
    try:
        for group in pack(items, max_questions=max_questions):
            results.extend(_run_guarded(group, adapter=adapter, simulated=simulated,
                                        gold_flags=gold_flags))
    except BudgetStop as exc:
        raise BudgetStop(str(exc), kept=results + list(exc.kept)) from exc
    except Exception as exc:
        exc.kept = results + list(getattr(exc, "kept", []) or [])
        raise
    return results


def _run_guarded(items: list[WorkItem], *, adapter: Any, simulated: bool,
                 gold_flags: Any) -> list[ItemResult]:
    got = run_pack(items, adapter=adapter, simulated=simulated)
    if gold_flags is None or len(items) <= 1:
        return got
    flags: list[bool] = []
    for item, res in zip(items, got):
        if res.error:
            continue
        flag = gold_flags(item, res)
        if flag is not None:
            flags.append(bool(flag))
    if needs_accuracy_split(flags, len(items)):
        rate = sum(1 for flag in flags if flag) / len(flags)
        print(f"[cc] batch accuracy {rate:.2f} on {len(items)} packets; re-ask halves",
              flush=True)

        def _side(subset: list[WorkItem]) -> list[ItemResult]:
            out = _run_guarded(subset, adapter=adapter, simulated=simulated,
                               gold_flags=gold_flags)
            for res in out:
                res.resampled = True
            return out

        return _halve(items, _side)
    return got
