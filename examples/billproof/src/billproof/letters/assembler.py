"""Drex picks the letter. Code fills every fact.

`letter_questions` asks for the letter type, the recipient, one ground per
disputed line, and a yes/no on each candidate evidence citation.
`assemble_letter` turns those answers into a `Letter`. Slot values come only
from the case and the verdict. `generated_words` stays 0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from billproof.schema import Action, Answer, BillLine, Case, Citation, Letter, LetterClause, Question, Verdict

_CLAUSE_DIR = Path(__file__).resolve().parent / "clauses"
_SLOT = re.compile(r"\{([A-Za-z0-9_]+)\}")
_LINE_NO = re.compile(r"lines\.(\d+)")
_BLANK = re.compile(r"[ \t]{2,}")
_SPACE_PUNCT = re.compile(r" +([.,;:])")
_BLANK_LINES = re.compile(r"\n{3,}")
_LETTER_TYPES = ("dispute", "appeal", "itemized_request", "none")
_RECIPIENTS = ("provider_billing", "insurer")
_STRUCTURAL = frozenset({"opening", "closing", "hold_collections"})
_OPENING = {
    "dispute": "opening_dispute",
    "appeal": "opening_appeal",
    "itemized_request": "opening_itemized",
}
# These grounds are about the whole bill. The dollar figure is verdict.disputed_cents.
_CASE_CLAUSES = frozenset({"eob_mismatch", "nsa_balance_bill"})
# One paragraph each. Lines that share one of these are named in a single sentence.
_ONCE_IDS = _CASE_CLAUSES | frozenset({
    "opening_dispute",
    "opening_appeal",
    "opening_itemized",
    "hold_collections",
    "closing",
    "records_request",
})
_RECIPIENT_FOR = {
    "dispute": "provider_billing",
    "itemized_request": "provider_billing",
    "appeal": "insurer",
}

# Verdict reason / denial category -> clause file stem. Longest match wins.
_REASON_TO_CLAUSE = {
    "DUPLICATE_CLAIM": "duplicate_charge",
    "DUPLICATE": "duplicate_charge",
    "NCCI_UNBUNDLED": "ncci_unbundling",
    "MUE_UNITS_EXCEEDED": "mue_units",
    "OVER_FEE_SCHEDULE": "fee_schedule",
    "EOB_OR_BALANCE_BILLING_MISMATCH": "eob_mismatch",
    "EOB_BILL_MISMATCH": "eob_mismatch",
    "SERVICE_AFTER_DEATH": "after_death",
    "MISSING_DATA": "missing_documentation",
    "MISSING_INFO": "missing_documentation",
    "NO_SURPRISES_ACT": "nsa_balance_bill",
    "MEDICAL_NECESSITY": "medical_necessity",
    "PRIOR_AUTH": "prior_auth",
    "CODING_BUNDLING": "coding_bundling",
}

_LIBRARY: dict[str, Clause] | None = None


@dataclass(frozen=True)
class Clause:
    id: str
    title: str
    letter_types: tuple[str, ...]
    ground: str
    slots: tuple[str, ...]
    summary: str
    body: str


def load_clauses() -> dict[str, Clause]:
    """Clause library keyed by file stem. Cached for the process."""
    global _LIBRARY
    if _LIBRARY is None:
        _LIBRARY = _read_clauses()
    return _LIBRARY


def fmt_cents(cents: int) -> str:
    """Dollar string. Uses `rules.money.fmt` when that module imports."""
    try:
        from billproof.rules.money import fmt

        return str(fmt(int(cents)))
    except Exception:
        return _fmt_local(cents)


def letter_questions(case: Case, verdict: Verdict) -> list[Question]:
    """Questions that let Drex decide the letter. Names match CONTRACT.md §3a."""
    library = load_clauses()
    payer = case.eob.payer.strip() if case.eob and case.eob.payer else ""
    payer_note = f" Payer on file: {payer}." if payer else ""
    questions = [
        Question(
            name="letter_type",
            type="choice",
            family="letter_type",
            instructions=(
                f"The verdict action is {verdict.case_action}. Choose the letter to send. "
                "dispute asks the provider to correct the bill. appeal asks the insurer to "
                "reconsider a denial. itemized_request asks for an itemized bill and records "
                "before any balance is treated as due. none sends no letter. "
                "Use the verdict and the documents. Do not invent a ground."
            ),
            criteria={
                "dispute": "Ask the provider billing office to correct a charge on the bill.",
                "appeal": "Ask the insurer to reconsider a denial.",
                "itemized_request": "Ask for an itemized bill and the records behind the charges.",
                "none": "Do not send a letter.",
            },
        ),
        Question(
            name="letter_recipient",
            type="choice",
            family="letter_recipient",
            instructions=(
                "Who receives the letter? A dispute or an itemized-bill request goes to the provider "
                "billing office. An appeal goes to the insurer. "
                f"Provider on file: {case.provider.name}.{payer_note}"
            ),
            criteria={
                "provider_billing": "The provider billing office. Use for a dispute or a records request.",
                "insurer": "The health plan that processed or denied the claim. Use for an appeal.",
            },
        ),
    ]
    lines = _disputed_lines(verdict)
    if verdict.case_action == Action.OK and not lines and not _case_clause_ids(verdict, library):
        return questions
    asked_case = False
    for line in lines:
        options = _applicable(case, verdict, line, library)
        if line.line_no > 0:
            options = [clause_id for clause_id in options if clause_id not in _CASE_CLAUSES]
        else:
            options = [clause_id for clause_id in options if clause_id in _CASE_CLAUSES] or options
            asked_case = True
        if not options:
            continue
        bill_line = _bill_line(case, line.line_no)
        reasons = ", ".join(line.reasons)
        reason_note = f"Reasons: {reasons}. " if reasons else ""
        if line.line_no <= 0 or bill_line is None:
            instructions = (
                "Case-level finding, not a bill line. "
                f"{reason_note}"
                "Choose the clause that matches the verdict. "
                "Do not choose a ground the verdict does not support."
            )
            evidence: list[str] = []
        else:
            instructions = (
                f"Line {line.line_no} is {bill_line.code} ({bill_line.label}). "
                f"Verdict action {line.action}. {reason_note}"
                "Choose the clause that matches the verdict. "
                "Do not choose a ground the verdict does not support."
            )
            evidence = _keys_for_line(case, line.line_no)
        questions.append(
            Question(
                name=f"ground_L{line.line_no}",
                type="choice",
                family="letter_ground",
                line_no=line.line_no,
                instructions=instructions,
                criteria={cid: library[cid].summary for cid in options},
                evidence=evidence,
            )
        )
    case_ids = _case_clause_ids(verdict, library)
    if case_ids and not asked_case:
        questions.append(
            Question(
                name="ground_L0",
                type="choice",
                family="letter_ground",
                line_no=0,
                instructions=(
                    "Case-level finding on the whole bill. "
                    f"Reasons: {', '.join(case_ids)}. "
                    "Choose the clause that matches the verdict."
                ),
                criteria={cid: library[cid].summary for cid in case_ids},
            )
        )
    line_nos = [line.line_no for line in lines] or [item.line_no for item in case.bill.lines]
    for key in _candidate_keys(case, line_nos):
        citation = case.citations[key]
        snippet = (citation.text or "").strip()
        if len(snippet) > 140:
            snippet = snippet[:137] + "..."
        questions.append(
            Question(
                name=f"evidence_{key}",
                type="noul",
                family="letter_evidence",
                instructions=(
                    f"Include this evidence citation in the packet? {citation.doc} page {citation.page}."
                    + (f" Printed text: {snippet}." if snippet else "")
                ),
                evidence=[key],
            )
        )
    return questions


def assemble_letter(case: Case, verdict: Verdict, answers: dict[str, Answer],
                    support: list[dict] | None = None) -> Letter:
    """Build a letter from Drex's answers. Missing answers fall back to the verdict.

    `support` (from `search.chain.letter_support`): plan clauses found by Drex Search,
    each `{doc_label, section, page, quote, facts?, policy?, picked_by, probability}`.
    Quotes are document text, so `generated_words` stays 0.
    """
    library = load_clauses()
    answers = answers or {}
    letter_type = _letter_type(answers, verdict)
    recipient = _recipient(answers, letter_type)
    base = _base_slots(case, verdict)
    type_answer = _answer(answers, "letter_type")
    type_probability = _probability(type_answer)
    clauses: list[LetterClause] = []
    included = _included_keys(case, verdict, answers)

    if letter_type == "none":
        return Letter(
            letter_type="none",
            recipient=recipient,
            subject="No letter",
            clauses=[],
            amount_cents=verdict.disputed_cents,
            deadline=verdict.deadline,
            generated_words=0,
        )

    opening_id = _OPENING[letter_type]
    clauses.append(
        _render_clause(
            library[opening_id], base, "letter_type", type_probability,
            _citations(case, [key for key in included if not _line_numbers(key)]),
        )
    )

    for line in _disputed_lines(verdict):
        if line.line_no <= 0 or _bill_line(case, line.line_no) is None:
            continue
        chosen, from_answer = _chosen_ground(case, verdict, line, answers, library)
        if not chosen or chosen in _ONCE_IDS:
            continue
        bill_slots = _line_slots(base, case, verdict, line)
        picked_by = f"ground_L{line.line_no}" if from_answer else f"verdict_L{line.line_no}"
        probability = _probability(_answer(answers, f"ground_L{line.line_no}")) if from_answer else None
        clauses.append(
            _render_clause(
                library[chosen], bill_slots, picked_by, probability,
                _citations(case, [key for key in included if line.line_no in _line_numbers(key)]),
            )
        )

    seen = {item.clause_id for item in clauses}
    for clause_id in _case_clause_ids(verdict, library):
        if clause_id in seen or clause_id == opening_id:
            continue
        slots = _case_slots(base, verdict, clause_id)
        slots["lines"] = _lines_phrase(
            _covered_line_nos(case, verdict, clause_id, answers, library)
        )
        picked_by, probability = _who_picked(clause_id, answers, verdict)
        clauses.append(
            _render_clause(
                library[clause_id], slots, picked_by, probability,
                _citations(case, [key for key in included if not _line_numbers(key)]),
            )
        )

    if letter_type == "appeal":
        for item in support or []:
            clauses.append(_support_clause(library["plan_language"], base, item))

    if not any(item.clause_id == "records_request" for item in clauses):
        covered = _covered_line_nos(case, verdict, "records_request", answers, library)
        if covered or letter_type == "itemized_request":
            slots = dict(base)
            slots["lines"] = _lines_phrase(covered)
            picked_by, probability = _who_picked("records_request", answers, verdict)
            if picked_by == "verdict" and not covered:
                picked_by, probability = "letter_type", type_probability
            clauses.append(
                _render_clause(
                    library["records_request"], slots, picked_by, probability,
                    _citations(case, [key for key in included if _line_numbers(key) & set(covered)]),
                )
            )

    # Collections sit with the provider. An appeal addressed only to the insurer skips the hold.
    if letter_type != "appeal" or recipient == "provider_billing":
        clauses.append(_render_clause(library["hold_collections"], base, "letter_type", type_probability, []))

    clauses.append(_render_clause(library["closing"], base, "letter_type", type_probability, []))
    return Letter(
        letter_type=letter_type,  # type: ignore[arg-type]
        recipient=recipient,  # type: ignore[arg-type]
        subject=_subject(letter_type, base),
        clauses=clauses,
        amount_cents=verdict.disputed_cents,
        deadline=verdict.deadline or _raw_deadline(case),
        generated_words=0,
    )


def _read_clauses() -> dict[str, Clause]:
    found: dict[str, Clause] = {}
    for path in sorted(_CLAUSE_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta, body = _split_front_matter(text, path.name)
        clause = Clause(
            id=path.stem,
            title=str(meta.get("title") or path.stem),
            letter_types=tuple(meta.get("letter_types") or ()),
            ground=str(meta.get("ground") or ""),
            slots=tuple(meta.get("slots") or ()),
            summary=str(meta.get("summary") or meta.get("title") or path.stem),
            body=body.strip(),
        )
        found[clause.id] = clause
    if not found:
        raise FileNotFoundError(f"no clauses in {_CLAUSE_DIR}")
    return found


def _split_front_matter(text: str, name: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        raise ValueError(f"{name} is missing front matter")
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError(f"{name} front matter does not close")
    meta: dict[str, object] = {}
    for line in text[text.find("\n") + 1:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, raw = line.partition(":")
        value = raw.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            meta[key.strip()] = [part.strip().strip("'\"") for part in inner.split(",") if part.strip()]
        else:
            meta[key.strip()] = value.strip("'\"")
    return meta, text[end + 4:]


def _fmt_local(cents: int) -> str:
    sign = "-" if int(cents) < 0 else ""
    whole, fraction = divmod(abs(int(cents)), 100)
    return f"{sign}${whole:,}.{fraction:02d}"


def _disputed_lines(verdict: Verdict):
    return [line for line in verdict.lines if line.action != Action.OK or line.disputed_cents > 0]


def _bill_line(case: Case, line_no: int) -> BillLine | None:
    for line in case.bill.lines:
        if line.line_no == line_no:
            return line
    return None


def _line_numbers(key: str) -> set[int]:
    return {int(item) for item in _LINE_NO.findall(key)}


def _keys_for_line(case: Case, line_no: int) -> list[str]:
    return [key for key in case.citations if line_no in _line_numbers(key)]


def _candidate_keys(case: Case, line_nos: list[int]) -> list[str]:
    wanted = set(line_nos)
    on_line = [key for key in case.citations if _line_numbers(key) & wanted]
    case_level = [key for key in case.citations if not _line_numbers(key)]
    keys = on_line + case_level
    if not keys:
        keys = list(case.citations)
    return keys[:16]


def clause_for_reason(reason: str) -> str | None:
    token = re.sub(r"[^A-Z0-9]+", "_", reason.strip().upper()).strip("_")
    if not token:
        return None
    if token in _REASON_TO_CLAUSE:
        return _REASON_TO_CLAUSE[token]
    for key in sorted(_REASON_TO_CLAUSE, key=len, reverse=True):
        if key in token:
            return _REASON_TO_CLAUSE[key]
    return None


def _applicable(case: Case, verdict: Verdict, line, library: dict[str, Clause]) -> list[str]:
    found: list[str] = []

    def add(clause_id: str | None) -> None:
        if not clause_id or clause_id in found or clause_id not in library:
            return
        if library[clause_id].ground in _STRUCTURAL:
            return
        found.append(clause_id)

    for reason in line.reasons:
        add(clause_for_reason(reason))
    if line.action == Action.APPEAL and case.denial is not None and _denial_covers(case, line.line_no):
        add(clause_for_reason(case.denial.category.value))
    if not found and line.action == Action.INSUFFICIENT_EVIDENCE:
        add("missing_documentation")
    if not found and line.action == Action.ASK_PATIENT:
        add("records_request")
    return found


def _denial_covers(case: Case, line_no: int) -> bool:
    denial = case.denial
    if denial is None or not denial.service_code:
        return True
    line = _bill_line(case, line_no)
    return line is not None and line.code == denial.service_code


def _case_clause_ids(verdict: Verdict, library: dict[str, Clause]) -> list[str]:
    """EOB-mismatch and NSA clauses named on a case-level line or in the verdict reasons."""
    found: list[str] = []

    def add(text: str) -> None:
        clause_id = clause_for_reason(text)
        if clause_id in _CASE_CLAUSES and clause_id in library and clause_id not in found:
            found.append(clause_id)

    for line in verdict.lines:
        if line.line_no <= 0:
            for reason in line.reasons:
                add(reason)
    for line in verdict.lines:
        for reason in line.reasons:
            add(reason)
    for text in verdict.human_reasons:
        add(text)
    return found


def _case_slots(base: dict[str, str], verdict: Verdict, clause_id: str) -> dict[str, str]:
    """Case-level amounts always come from the verdict total, never from one line's charge."""
    slots = dict(base)
    slots["amount"] = fmt_cents(verdict.disputed_cents)
    slots["disputed"] = fmt_cents(verdict.disputed_cents)
    cites: list[str] = []
    for line in verdict.lines:
        reasons = [clause_for_reason(reason) for reason in line.reasons]
        if clause_id in reasons or line.line_no <= 0:
            for cite in line.rule_cites:
                if cite not in cites:
                    cites.append(cite)
    if cites:
        slots["rule_cite"] = "; ".join(cites)
    return slots


def _lines_phrase(numbers: list[int]) -> str:
    """'line 2', 'lines 2 and 4', or 'lines 2, 4 and 5'."""
    nums = sorted({int(number) for number in numbers if int(number) > 0})
    if not nums:
        return ""
    if len(nums) == 1:
        return f"line {nums[0]}"
    if len(nums) == 2:
        return f"lines {nums[0]} and {nums[1]}"
    body = ", ".join(str(number) for number in nums[:-1])
    return f"lines {body} and {nums[-1]}"


def _covered_line_nos(case: Case, verdict: Verdict, clause_id: str, answers: dict[str, Answer],
                      library: dict[str, Clause]) -> list[int]:
    """Bill lines whose ground is this case-wide clause, in ascending order."""
    found: list[int] = []
    for line in _disputed_lines(verdict):
        if line.line_no <= 0 or _bill_line(case, line.line_no) is None:
            continue
        chosen, _from_answer = _chosen_ground(case, verdict, line, answers, library)
        mapped = [clause_for_reason(reason) for reason in line.reasons]
        if chosen == clause_id or clause_id in mapped:
            if line.line_no not in found:
                found.append(line.line_no)
    found.sort()
    return found


def _who_picked(clause_id: str, answers: dict[str, Answer], verdict: Verdict) -> tuple[str, float | None]:
    names = [f"ground_L{line.line_no}" for line in verdict.lines]
    if "ground_L0" not in names:
        names.append("ground_L0")
    for name in names:
        answer = _answer(answers, name)
        if answer is not None and answer.choice.strip() == clause_id:
            return name, _probability(answer)
    return "verdict", None


def _answer(answers: dict[str, Answer], name: str) -> Answer | None:
    found = answers.get(name)
    return found if isinstance(found, Answer) else None


def _probability(answer: Answer | None) -> float | None:
    if answer is None:
        return None
    probs = answer.probabilities or {}
    if answer.choice in probs:
        try:
            return float(probs[answer.choice])
        except (TypeError, ValueError):
            pass
    try:
        return float(answer.confidence)
    except (TypeError, ValueError):
        return None


def _letter_type(answers: dict[str, Answer], verdict: Verdict) -> str:
    choice = _answer(answers, "letter_type")
    if choice is not None and choice.choice.strip() in _LETTER_TYPES:
        return choice.choice.strip()
    if verdict.case_action == Action.APPEAL:
        return "appeal"
    if verdict.case_action == Action.DISPUTE:
        return "dispute"
    if verdict.case_action in (Action.ASK_PATIENT, Action.INSUFFICIENT_EVIDENCE):
        return "itemized_request"
    return "none"


def _recipient(answers: dict[str, Answer], letter_type: str) -> str:
    """Dispute and records requests go to the provider. Appeals go to the insurer.

    A contradicting answer is kept on that answer's `raw` and is not used.
    """
    required = _RECIPIENT_FOR.get(letter_type)
    choice = _answer(answers, "letter_recipient")
    picked = choice.choice.strip() if choice is not None and choice.choice.strip() in _RECIPIENTS else None
    if required is None:
        return picked or "provider_billing"
    if picked and picked != required and choice is not None:
        raw = dict(choice.raw or {})
        raw["recipient_override"] = {"choice": picked, "used": required, "letter_type": letter_type}
        choice.raw = raw
    return required


def _chosen_ground(case, verdict, line, answers, library) -> tuple[str | None, bool]:
    options = _applicable(case, verdict, line, library)
    if not options:
        return None, False
    answer = _answer(answers, f"ground_L{line.line_no}")
    if answer is not None and answer.choice.strip() in options:
        return answer.choice.strip(), True
    return options[0], False


def _raw_deadline(case: Case) -> str | None:
    if case.denial is not None:
        return case.denial.appeal_deadline
    return None


def _deadline_text(case: Case, verdict: Verdict) -> str:
    return _clean(verdict.deadline or _raw_deadline(case))


def _clean(value: object) -> str:
    """A slot value, or empty when the fact is absent. Never a placeholder."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() == "not on file":
        return ""
    return text


def _base_slots(case: Case, verdict: Verdict) -> dict[str, str]:
    cites: list[str] = []
    for line in verdict.lines:
        for cite in line.rule_cites:
            if cite not in cites:
                cites.append(cite)
    eob = case.eob
    denial = case.denial
    return {
        "line_no": "",
        "code": "",
        "label": "",
        "dos": "",
        "amount": fmt_cents(verdict.disputed_cents),
        "rule_cite": "; ".join(cites),
        "deadline": _deadline_text(case, verdict),
        "account_no": _clean(case.bill.account_no),
        "claim_id": _clean(eob.claim_id) if eob else "",
        "payer": _clean(eob.payer) if eob else "",
        "provider": _clean(case.provider.name),
        "date_of_death": _clean(case.patient.date_of_death),
        "member_id": _clean(case.patient.member_id),
        "patient_id": _clean(case.patient.patient_id),
        "billed": fmt_cents(verdict.billed_balance_cents),
        "owed": fmt_cents(verdict.owed_cents),
        "disputed": fmt_cents(verdict.disputed_cents),
        "carc": _clean(denial.carc) if denial else "",
        "units": "",
    }


def _line_slots(base: dict[str, str], case: Case, verdict: Verdict, line) -> dict[str, str]:
    slots = dict(base)
    bill_line = _bill_line(case, line.line_no)
    amount = line.disputed_cents if line.disputed_cents else verdict.disputed_cents
    slots.update({
        "line_no": str(line.line_no),
        "code": _clean(bill_line.code) if bill_line else "",
        "label": _clean(bill_line.label) if bill_line else "",
        "dos": _clean(bill_line.dos) if bill_line else "",
        "amount": fmt_cents(amount),
        "rule_cite": "; ".join(line.rule_cites),
        "units": str(bill_line.units) if bill_line is not None else "",
    })
    if case.eob is not None:
        for eob_line in case.eob.lines:
            if eob_line.line_no == line.line_no and eob_line.carc:
                slots["carc"] = ", ".join(eob_line.carc)
                break
    return slots


def _fill(body: str, slots: dict[str, str]) -> str:
    """Fill `{name}` slots. `{name? ...}` is kept only when that slot and every bare slot in it are present.

    The clause library chooses the wording. A missing fact drops its sentence.
    Nothing here invents a deadline or prints a placeholder.
    """
    rendered = _optional_blocks(body, slots)

    def replace(match: re.Match[str]) -> str:
        return _clean(slots.get(match.group(1)))

    rendered = _SLOT.sub(replace, rendered)
    rendered = _BLANK.sub(" ", rendered)
    rendered = _SPACE_PUNCT.sub(r"\1", rendered)
    rendered = re.sub(r"[ \t]+\n", "\n", rendered)
    rendered = _BLANK_LINES.sub("\n\n", rendered)
    return rendered.strip()


def _slot_value(slots: dict[str, str], key: str) -> str:
    return _clean(slots.get(key))


def _optional_blocks(text: str, slots: dict[str, str]) -> str:
    out: list[str] = []
    index = 0
    while index < len(text):
        if text[index] == "{":
            gate = _gate_at(text, index)
            if gate is not None:
                name, inner_at, close = gate
                inner = text[inner_at:close]
                if _block_ready(name, inner, slots):
                    out.append(_optional_blocks(inner, slots))
                index = close + 1
                continue
        out.append(text[index])
        index += 1
    return "".join(out)


def _gate_at(text: str, open_at: int) -> tuple[str, int, int] | None:
    """Return (name, inner start, closing brace) when `{name?` opens here."""
    cursor = open_at + 1
    if cursor >= len(text) or not (text[cursor].isalpha() or text[cursor] == "_"):
        return None
    while cursor < len(text) and (text[cursor].isalnum() or text[cursor] == "_"):
        cursor += 1
    if cursor >= len(text) or text[cursor] != "?":
        return None
    close = _matching_brace(text, open_at)
    if close is None:
        return None
    return text[open_at + 1:cursor], cursor + 1, close


def _matching_brace(text: str, open_at: int) -> int | None:
    depth = 0
    for cursor in range(open_at, len(text)):
        char = text[cursor]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return cursor
    return None


def _block_ready(gate: str, inner: str, slots: dict[str, str]) -> bool:
    if not _slot_value(slots, gate):
        return False
    return all(_slot_value(slots, name) for name in _bare_slots(inner))


def _bare_slots(text: str) -> list[str]:
    """Slot names at this level. Nested `{name? ...}` blocks are not bare slots."""
    names: list[str] = []
    index = 0
    while index < len(text):
        if text[index] == "{":
            gate = _gate_at(text, index)
            if gate is not None:
                index = gate[2] + 1
                continue
            match = _SLOT.match(text, index)
            if match:
                names.append(match.group(1))
                index = match.end()
                continue
        index += 1
    return names


def _included_keys(case: Case, verdict: Verdict, answers: dict[str, Answer]) -> list[str]:
    lines = _disputed_lines(verdict)
    line_nos = [line.line_no for line in lines] or [item.line_no for item in case.bill.lines]
    if verdict.case_action == Action.OK and not lines:
        return []
    included: list[str] = []
    for key in _candidate_keys(case, line_nos):
        answer = _answer(answers, f"evidence_{key}")
        if answer is None or answer.choice.strip().lower() == "yes":
            included.append(key)
    return included


def _citations(case: Case, keys: list[str]) -> list[Citation]:
    return [case.citations[key].model_copy() for key in keys if key in case.citations]


def _render_clause(clause: Clause, slots: dict[str, str], picked_by: str, probability: float | None,
                   evidence: list[Citation]) -> LetterClause:
    return LetterClause(
        clause_id=clause.id,
        title=clause.title,
        text=_fill(clause.body, slots),
        picked_by=picked_by,
        probability=probability,
        evidence=evidence,
    )


def _support_clause(clause: Clause, base: dict[str, str], item: dict) -> LetterClause:
    facts = "; ".join(f'"{f["quote"]}" ({f["doc_label"]}, page {f["page"]})' for f in item.get("facts") or [])
    policy = item.get("policy") or {}
    slots = {**base, "doc_label": item.get("doc_label"), "section": item.get("section"),
             "page": item.get("page"), "quote": item.get("quote"), "facts": facts,
             "policy_title": policy.get("title"), "policy_quote": policy.get("quote"),
             "policy_study": policy.get("study"), "policy_url": policy.get("url")}
    evidence = [Citation(doc="plan", page=int(item.get("page") or 1), text=item.get("quote"))]
    return _render_clause(clause, {k: _clean(v) for k, v in slots.items()},
                          item.get("picked_by") or "cite_clause", item.get("probability"), evidence)


def _subject(letter_type: str, slots: dict[str, str]) -> str:
    patterns = {
        "dispute": "Billing dispute for account {account_no}",
        "appeal": "Appeal{claim_id? of claim {claim_id}}",
        "itemized_request": "Request for an itemized bill for account {account_no}",
    }
    return _fill(patterns[letter_type], slots)
