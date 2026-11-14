"""Deterministic what-if decisions from cached Drex facts.

No model calls. ``logic.decide`` is rule. An edit changes the
dependency map, the scope window, the payer check, or the meaning of a
missing fact. Every result is a hypothetical edit, not a new published list.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from billproof.bench.pa.logic import decide
from billproof.bench.pa.questions import DATE_Q, PAYER_Q, scope_from_answers
from billproof.command_center.rows import colour_of, flip_name

MISSING_OK = ("missing", "not_met")


@dataclass
class CaseCard:
    case_id: str
    list_id: str
    policy_id: str
    family: str
    state: str
    payer: str
    request_payer: str
    service_date: str
    window_from: str
    window_through: str
    connective: str
    split: str
    stage: str
    title: str
    drug: str | None
    deps: dict[str, list[str]]
    criteria_text: dict[str, str]
    fact_text: dict[str, str]
    facts: dict[str, Any]
    scope_payer: str
    scope_date: str
    gold: str | None
    cached: bool
    base_decision: str = ""
    base_label: str = ""
    base_colour: str = ""
    base_scope: str = ""
    extra_text: dict[str, str] = field(default_factory=dict)


def baseline_of(card: CaseCard) -> dict[str, Any]:
    scope = scope_from_answers({PAYER_Q: card.scope_payer, DATE_Q: card.scope_date})
    derived = decide(card.facts, card.deps, scope)
    label, colour = colour_of(derived["decision"])
    return {"scope": scope, "label": label, "colour": colour, **derived}


def stamp_baseline(card: CaseCard) -> None:
    if not card.cached:
        return
    base = baseline_of(card)
    card.base_decision = base["decision"]
    card.base_label = base["label"]
    card.base_colour = base["colour"]
    card.base_scope = base["scope"]


def _shift_iso(value: str, years: int) -> str:
    year, month, day = (int(part) for part in value.split("-"))
    try:
        return date(year + years, month, day).isoformat()
    except ValueError:
        return date(year + years, month, 28).isoformat()


def _facts_in(deps: dict[str, list[str]]) -> list[str]:
    out: list[str] = []
    for facts in deps.values():
        for fact in facts:
            if fact not in out:
                out.append(fact)
    return out


def _retie(deps: dict[str, list[str]], target: str) -> dict[str, list[str]]:
    facts = _facts_in(deps)
    if target == "AND":
        return {f"required::{fact}": [fact] for fact in facts}
    if target == "OR":
        return {"any_listed_alternative": facts} if facts else {}
    raise ValueError(f"connective must be AND or OR, got {target}")


def _force(deps: dict[str, list[str]], fact: str) -> None:
    for key in list(deps):
        if fact in deps[key] and deps[key] != [fact]:
            kept = [item for item in deps[key] if item != fact]
            if kept:
                deps[key] = kept
            else:
                del deps[key]
    if not any(facts == [fact] for facts in deps.values()):
        deps[f"required::{fact}"] = [fact]


def _known_facts(card: CaseCard) -> set[str]:
    known = set(card.facts)
    known.update(card.fact_text)
    known.update(_facts_in(card.deps))
    return known


def edited_deps(card: CaseCard, edit: dict[str, Any]) -> tuple[dict[str, list[str]], list[str]]:
    """Return the dependency map after the edit, and names that matched nothing."""
    deps = {key: list(facts) for key, facts in card.deps.items()}
    unmatched: list[str] = []
    known = _known_facts(card)
    drop = [str(name) for name in (edit.get("drop_criteria") or [])]
    for name in drop:
        if name in deps:
            del deps[name]
            continue
        hit = False
        for key in list(deps):
            if name in deps[key]:
                hit = True
                kept = [fact for fact in deps[key] if fact != name]
                if kept:
                    deps[key] = kept
                else:
                    del deps[key]
        if not hit:
            unmatched.append(name)
    target = edit.get("connective")
    if target and str(target) != card.connective:
        deps = _retie(deps, str(target))
    require = [str(name) for name in (edit.get("require_criteria") or [])]
    extra = edit.get("extra_facts") or {}
    for name in extra:
        known.add(str(name))
        require.append(str(name))
    for name in require:
        if name in deps and len(deps[name]) == 1:
            _force(deps, deps[name][0])
            continue
        if name in deps:
            for fact in list(deps[name]):
                _force(deps, fact)
            continue
        fact = name.split("required::", 1)[-1]
        if fact in known or fact.startswith("added_"):
            _force(deps, fact)
            continue
        unmatched.append(name)
    return deps, unmatched


def edited_facts(card: CaseCard, edit: dict[str, Any]) -> dict[str, Any]:
    facts = dict(card.facts)
    for name, value in (edit.get("extra_facts") or {}).items():
        facts[str(name)] = value
    if edit.get("missing_as") == "not_met":
        facts = {key: (False if value is None else value) for key, value in facts.items()}
    return facts


def edited_scope(card: CaseCard, edit: dict[str, Any]) -> tuple[str, str, str]:
    """Return scope, the window from, and the window through that were used.

    The published Drex payer and date answers stay unless this edit changes
    that part of scope. A window edit is the calendar check 
    ``compute_scope`` uses. A payer restriction is string equality on the
    request payer.
    """
    payer_ok = card.scope_payer != "no"
    date_ok = card.scope_date != "no"
    start, end = card.window_from, card.window_through
    if edit.get("restrict_payer"):
        payer_ok = card.request_payer == edit["restrict_payer"]
    shift = edit.get("window_shift_years")
    window = edit.get("window")
    if window:
        start = str(window.get("from") or "")
        end = str(window.get("through") or "")
        date_ok = bool(start and end and card.service_date and start <= card.service_date <= end)
    elif shift:
        start = _shift_iso(card.window_from, int(shift)) if card.window_from else ""
        end = _shift_iso(card.window_through, int(shift)) if card.window_through else ""
        date_ok = bool(start and end and card.service_date and start <= card.service_date <= end)
    if not payer_ok:
        return "wrong_payer", start, end
    if not date_ok:
        return "wrong_date", start, end
    return "applicable", start, end


def _reason(card: CaseCard, edit: dict[str, Any], old: dict[str, Any],
            new: dict[str, Any], deps: dict[str, list[str]],
            window: tuple[str, str]) -> str:
    bits: list[str] = []
    target = edit.get("connective")
    if target and str(target) != card.connective:
        bits.append(f"connective {card.connective}→{target}")
    if edit.get("drop_criteria"):
        bits.append("dropped " + ", ".join(str(n) for n in edit["drop_criteria"]))
    if edit.get("require_criteria"):
        bits.append("required " + ", ".join(str(n) for n in edit["require_criteria"]))
    extra = edit.get("extra_facts") or {}
    if extra:
        label = edit.get("extra_label") or "added criterion"
        bits.append(f"added required criterion ({label})")
    if edit.get("missing_as") == "not_met":
        used = _facts_in(deps)
        n_null = sum(1 for fact in used if card.facts.get(fact) is None and fact not in extra)
        if n_null:
            bits.append(f"not-documented counted as not-met ({n_null} facts)")
    if old["scope"] != new["scope"]:
        if new["scope"] == "wrong_date":
            bits.append(
                f"service date {card.service_date or 'missing'} is outside "
                f"{window[0]}–{window[1]}"
            )
        elif new["scope"] == "wrong_payer":
            bits.append(
                f"request payer {card.request_payer!r} is not {edit.get('restrict_payer')!r}"
            )
        elif new["scope"] == "applicable":
            bits.append("edited scope is applicable")
    changed = []
    for name, state in new["criteria"].items():
        if old["criteria"].get(name) == state or state == "met":
            continue
        fact = deps.get(name, [name])[0]
        text = (card.criteria_text.get(name) or card.fact_text.get(fact)
                or card.extra_text.get(fact) or name)
        changed.append(f"{text[:80]} is {state}")
    if changed:
        bits.append("; ".join(changed[:2]))
    text = "; ".join(bits) if bits else "edited rule changed the decision"
    return text[:300]


def matches(card: CaseCard, apply_to: dict[str, Any] | None) -> bool:
    if not apply_to:
        return False
    list_ids = apply_to.get("list_id") or apply_to.get("list_ids")
    if isinstance(list_ids, str):
        list_ids = [list_ids]
    if list_ids and card.list_id not in list_ids:
        return False
    policy_ids = apply_to.get("policy_id") or apply_to.get("policy_ids")
    if isinstance(policy_ids, str):
        policy_ids = [policy_ids]
    if policy_ids and card.policy_id not in policy_ids and card.list_id not in policy_ids:
        return False
    if apply_to.get("family") and card.family != apply_to["family"]:
        return False
    if apply_to.get("state") and card.state != apply_to["state"]:
        return False
    if apply_to.get("payer") and card.payer != apply_to["payer"]:
        return False
    return True


def run_edits(cards: list[CaseCard], *, edit: dict[str, Any],
              apply_to: dict[str, Any],
              extras: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Re-decide every cached case the edit applies to. Hypothetical.

    ``extras`` maps case_id to extra fact values (a newly asked criterion).
    Cases missing from that map are counted as unanswered, not decided.
    """
    started = time.perf_counter()
    affected = [card for card in cards if matches(card, apply_to)]
    flips: list[dict[str, Any]] = []
    unmatched: set[str] = set()
    from_labels: Counter[str] = Counter()
    to_labels: Counter[str] = Counter()
    n_decided = 0
    n_uncached = 0
    n_unanswered = 0
    for card in affected:
        if not card.cached:
            n_uncached += 1
            continue
        local = edit
        if extras is not None:
            extra = extras.get(card.case_id)
            if not extra:
                n_unanswered += 1
                continue
            local = dict(edit)
            local["extra_facts"] = extra
        n_decided += 1
        if not card.base_decision:
            stamp_baseline(card)
        old = {
            "decision": card.base_decision,
            "scope": card.base_scope,
            "criteria": decide(card.facts, card.deps, card.base_scope)["criteria"],
            "label": card.base_label,
            "colour": card.base_colour,
        }
        deps, missed = edited_deps(card, local)
        unmatched.update(missed)
        facts = edited_facts(card, local)
        scope, start, end = edited_scope(card, local)
        new = decide(facts, deps, scope)
        new_label, new_colour = colour_of(new["decision"])
        from_labels[old["label"]] += 1
        to_labels[new_label] += 1
        if old["label"] == new_label and old["decision"] == new["decision"]:
            continue
        new_view = {**new, "label": new_label, "colour": new_colour}
        flips.append({
            "case_id": card.case_id,
            "list_id": card.list_id,
            "policy_id": card.policy_id,
            "state": card.state,
            "family": card.family,
            "title": card.title,
            "drug": card.drug,
            "from_label": old["label"],
            "to_label": new_label,
            "from_decision": old["decision"],
            "to_decision": new["decision"],
            "from_colour": old["colour"],
            "to_colour": new_colour,
            "reason": _reason(card, local, old, new_view, deps, (start, end)),
        })
    counts: Counter[str] = Counter()
    for flip in flips:
        name = flip_name(flip["from_label"], flip["to_label"])
        if name:
            counts[name] += 1
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return {
        "hypothetical": True,
        "simulated": False,
        "kind": "what-if",
        "note": (
            "Hypothetical what-if. Criterion facts are cached Drex answers. "
            "The decision is deterministic code on the edited rule. "
            "This is not a published policy change."
        ),
        "edit": edit,
        "apply_to": apply_to,
        "n_affected": len(affected),
        "n_decided": n_decided,
        "n_uncached": n_uncached,
        "n_unanswered": n_unanswered,
        "n_unchanged": n_decided - len(flips),
        "n_flips": len(flips),
        "flip_counts": dict(counts),
        "from_labels": dict(from_labels),
        "to_labels": dict(to_labels),
        "unmatched": sorted(unmatched),
        "elapsed_ms": round(elapsed_ms, 3),
        "flips": flips,
    }


def _or_drug_policy(policies: list[dict[str, Any]]) -> dict[str, Any] | None:
    chosen = [
        policy for policy in policies
        if policy.get("connective") == "OR"
        and ("pharmacy" in (policy.get("family") or "") or "drug" in (policy.get("family") or ""))
    ]
    if not chosen:
        return None
    chosen.sort(key=lambda policy: (-int(policy.get("n_cases") or 0), policy["id"]))
    return chosen[0]


def _year_policy(cards: list[CaseCard]) -> tuple[CaseCard | None, int]:
    best: CaseCard | None = None
    best_n = -1
    best_id = ""
    by_list: dict[str, list[CaseCard]] = {}
    for card in cards:
        by_list.setdefault(card.list_id, []).append(card)
    for list_id, group in by_list.items():
        if len(group) < 16 or not group[0].window_from or not group[0].window_through:
            continue
        start = _shift_iso(group[0].window_from, 1)
        end = _shift_iso(group[0].window_through, 1)
        changed = 0
        for card in group:
            if not card.service_date:
                continue
            old_in = card.window_from <= card.service_date <= card.window_through
            new_in = start <= card.service_date <= end
            if old_in != new_in:
                changed += 1
        if changed > best_n or (changed == best_n and (best_id == "" or list_id < best_id)):
            best = group[0]
            best_n = changed
            best_id = list_id
    if best is None or best_n < 0:
        return None, 0
    return best, best_n


def preset_specs() -> list[dict[str, Any]]:
    return [
        {
            "id": "arkansas_not_documented_as_not_met",
            "title": "Arkansas tightens: not-documented counts as not-met",
            "edit": {"missing_as": "not_met"},
            "apply_to": {"state": "AR"},
        },
        {
            "id": "drug_policy_or_to_and",
            "title": "A drug policy switches OR → AND",
            "resolve": "or_drug_policy",
            "edit": {"connective": "AND"},
        },
        {
            "id": "policy_year_rollover",
            "title": "Policy year rolls over (date window moves)",
            "resolve": "year_rollover",
            "edit": {"window_shift_years": 1},
        },
    ]


def resolve_preset(spec: dict[str, Any], policies: list[dict[str, Any]],
                   cards: list[CaseCard]) -> dict[str, Any]:
    """Fill in the policy a selector picks. Raises ValueError if it cannot."""
    edit = dict(spec.get("edit") or {})
    apply_to = dict(spec.get("apply_to") or {})
    resolved: dict[str, Any] = {"id": spec["id"], "title": spec["title"]}
    kind = spec.get("resolve")
    if kind == "or_drug_policy":
        policy = _or_drug_policy(policies)
        if policy is None:
            raise ValueError("no OR policy in a pharmacy or drug family")
        apply_to = {"list_id": policy["id"]}
        resolved["policy"] = {
            "id": policy["id"],
            "policy_id": policy.get("policy_id"),
            "title": policy.get("title"),
            "drug": policy.get("drug"),
            "payer": policy.get("payer"),
            "state": policy.get("state"),
            "family": policy.get("family"),
            "connective": policy.get("connective"),
            "n_cases": policy.get("n_cases"),
            "selection": (
                "OR list in a pharmacy or drug family with the most cases; "
                "list id breaks ties"
            ),
        }
    elif kind == "year_rollover":
        card, n_scope = _year_policy(cards)
        if card is None:
            raise ValueError("no policy with a date window")
        apply_to = {"list_id": card.list_id}
        resolved["policy"] = {
            "id": card.list_id,
            "policy_id": card.policy_id,
            "title": card.title,
            "drug": card.drug,
            "payer": card.payer,
            "state": card.state,
            "family": card.family,
            "window": {"from": card.window_from, "through": card.window_through},
            "shifted_window": {
                "from": _shift_iso(card.window_from, 1),
                "through": _shift_iso(card.window_through, 1),
            },
            "n_scope_changed": n_scope,
            "selection": (
                "Policy whose published window, shifted forward one year, "
                "changes whether the most requests fall inside it "
                "(at least 16 cases). This scope count is the calendar, "
                "not a model."
            ),
        }
    resolved["edit"] = edit
    resolved["apply_to"] = apply_to
    return resolved


def run_preset(spec: dict[str, Any], policies: list[dict[str, Any]],
               cards: list[CaseCard], *, example_limit: int = 12) -> dict[str, Any]:
    resolved = resolve_preset(spec, policies, cards)
    result = run_edits(cards, edit=resolved["edit"], apply_to=resolved["apply_to"])
    n_cached = sum(1 for card in cards if card.cached)
    summary = {
        "id": resolved["id"],
        "title": resolved["title"],
        "hypothetical": True,
        "simulated": False,
        "edit": resolved["edit"],
        "apply_to": resolved["apply_to"],
        "policy": resolved.get("policy"),
        "n_affected": result["n_affected"],
        "n_decided": result["n_decided"],
        "n_uncached": result["n_uncached"],
        "n_flips": result["n_flips"],
        "flip_counts": result["flip_counts"],
        "from_labels": result["from_labels"],
        "to_labels": result["to_labels"],
        "elapsed_ms": result["elapsed_ms"],
        "partial": n_cached != len(cards),
        "n_cached_queue": n_cached,
        "example_flips": result["flips"][:example_limit],
        "note": result["note"],
    }
    return summary


def validate_edit(edit: dict[str, Any]) -> dict[str, Any]:
    """Return a cleaned edit or raise ValueError."""
    if not isinstance(edit, dict):
        raise ValueError("edit must be an object")
    cleaned: dict[str, Any] = {}
    connective = edit.get("connective")
    if connective is not None:
        connective = str(connective).upper()
        if connective not in ("AND", "OR"):
            raise ValueError("connective must be AND or OR")
        cleaned["connective"] = connective
    for key in ("drop_criteria", "require_criteria"):
        if edit.get(key) is None:
            continue
        values = edit[key]
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list) or not all(isinstance(item, str) and item for item in values):
            raise ValueError(f"{key} must be a list of names")
        cleaned[key] = values
    if edit.get("window") is not None:
        window = edit["window"]
        if not isinstance(window, dict):
            raise ValueError("window must be {from, through}")
        start, end = str(window.get("from") or ""), str(window.get("through") or "")
        _shift_iso(start, 0)
        _shift_iso(end, 0)
        if start > end:
            raise ValueError("window from is after through")
        cleaned["window"] = {"from": start, "through": end}
    if edit.get("window_shift_years") is not None:
        years = int(edit["window_shift_years"])
        if years == 0:
            raise ValueError("window_shift_years is 0")
        cleaned["window_shift_years"] = years
    if edit.get("restrict_payer"):
        cleaned["restrict_payer"] = str(edit["restrict_payer"])
    if edit.get("missing_as") is not None:
        mode = str(edit["missing_as"])
        if mode not in MISSING_OK:
            raise ValueError("missing_as must be 'missing' or 'not_met'")
        cleaned["missing_as"] = mode
    if edit.get("extra_facts") is not None:
        extra = edit["extra_facts"]
        if not isinstance(extra, dict):
            raise ValueError("extra_facts must be an object")
        cleaned["extra_facts"] = extra
        if edit.get("extra_label"):
            cleaned["extra_label"] = str(edit["extra_label"])[:160]
    if not cleaned:
        raise ValueError("edit is empty")
    return cleaned


def validate_apply_to(apply_to: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(apply_to, dict) or not apply_to:
        raise ValueError("apply_to must name a policy, family, state, or payer")
    allowed = {"list_id", "list_ids", "policy_id", "policy_ids", "family", "state", "payer"}
    unknown = set(apply_to) - allowed
    if unknown:
        raise ValueError("unknown apply_to field: " + ", ".join(sorted(unknown)))
    return apply_to
