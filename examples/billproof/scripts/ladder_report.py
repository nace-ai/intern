#!/usr/bin/env python3
"""Score ladder JSONL files and write metrics plus docs/LADDER.md.

Reads other threads' result files. Does not call a model.
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "src"))

from billproof.bench.metrics import wilson_ci  # noqa: E402

RUNS = ROOT / "data" / "runs"
METRICS = ROOT / "data" / "metrics"
DOC = ROOT / "docs" / "LADDER.md"

SOL_LIST_IN, SOL_LIST_OUT = 2.0, 10.0
SOL_CFG_IN, SOL_CFG_OUT = 2.5, 15.0


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_jsonl(path: Path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def last_rows(path: Path) -> list[dict]:
    last: dict[str, dict] = {}
    for row in load_jsonl(path):
        case_id = row.get("case_id")
        if case_id:
            last[case_id] = row
    return [row for row in last.values() if not row.get("retry") and not row.get("protocol_discard")]


def p50(values: list[float]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return None
    return float(statistics.median(clean))


def per_thousand(costs: list[float]) -> float | None:
    if not costs:
        return None
    return 1000.0 * sum(costs) / len(costs)


def ladder_spend() -> float:
    total = 0.0
    for path in RUNS.glob("ladder_*.jsonl"):
        for row in load_jsonl(path):
            # A second protocol_discard line with no request id repeats one attempt.
            if row.get("protocol_discard") and not row.get("request_id") and not row.get("ts"):
                continue
            total += float(row.get("cost_usd") or 0)
    return total


def output_cap_note() -> str:
    path = RUNS / "ladder_output_cap.json"
    if not path.exists():
        return "Output cap file is missing."
    document = json.loads(path.read_text())
    cap = int(document["output_tokens"])
    clipped = []
    largest = 0
    for run in sorted(RUNS.glob("ladder_hard_*.jsonl")):
        for row in load_jsonl(run):
            tokens = int(row.get("output_tokens") or 0)
            if not row.get("protocol_discard"):
                largest = max(largest, tokens)
            if row.get("protocol_discard") and tokens in {8192, 32768, cap}:
                clipped.append(
                    f"`{row.get('requested_model') or row.get('model')}` {row.get('case_id')} "
                    f"at {tokens} output tokens"
                )
    unique = []
    for item in clipped:
        if item not in unique:
            unique.append(item)
    scored_on_cap = []
    for run in sorted(RUNS.glob("ladder_*.jsonl")):
        last: dict[str, dict] = {}
        for row in load_jsonl(run):
            if row.get("case_id"):
                last[row["case_id"]] = row
        for row in last.values():
            if row.get("protocol_discard"):
                continue
            if int(row.get("output_tokens") or 0) == cap:
                scored_on_cap.append(f"{row.get('case_id')} ({run.name})")
    lines = [
        "## Output cap",
        "",
        f"Frozen file `data/runs/ladder_output_cap.json` sets one completion cap of {cap} tokens "
        "for every ladder model. The shim reads that file. "
        "`LADDER_MAX_TOKENS` is refused when it differs. "
        "The router default of 8192 tokens cut HARD-TEST-0012 for `anthropic/claude-haiku-5.5` "
        "to empty content (`finish_reason=length`, 8192 completion tokens). "
        "That attempt is `protocol_discard` and is not in the table. "
        "The case was called once more after the cap was raised. "
        "The scored row is that second call. "
        "A later completion that still stops at the frozen cap stays invalid and is not called again.",
        "",
    ]
    if unique:
        lines.append(
            "Discarded cuts below that cap, kept in the log and left out of the table: "
            + "; ".join(unique)
            + ". Each of those cases has one later scored call. "
            "The 8192 cut is the router default. The 32768 cut is a process that had loaded an older cap "
            "before this file existed. No row that reached the frozen cap was discarded or called again."
        )
        lines.append("")
    lines.append(
        f"The largest scored Hard completion stored here is {largest} output tokens, under the frozen cap. "
        + (
            "Scored rows sitting on the frozen cap: " + ", ".join(scored_on_cap) + "."
            if scored_on_cap
            else "No scored row has output_tokens equal to the frozen cap."
        )
    )
    lines.append("")
    return "\n".join(lines)


def fmt_pct(k: float, n: int) -> str:
    if n <= 0:
        return "—"
    lo, hi = wilson_ci(k, n)
    return f"{k / n:.3f} ({lo:.3f}–{hi:.3f})"


def fmt_money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}"


def fmt_ms(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.0f}"


def write_metric(name: str, value, n: int, source: str, simulated: bool = False) -> None:
    METRICS.mkdir(parents=True, exist_ok=True)
    payload = {
        "value": value,
        "n": n,
        "source_run": source,
        "simulated": simulated,
        "computed_at": now(),
    }
    (METRICS / name).write_text(json.dumps(payload, indent=2) + "\n")


def token_cost(row: dict, pin: float, pout: float) -> float | None:
    if row.get("input_tokens") is None or row.get("output_tokens") is None:
        return None
    return (int(row["input_tokens"]) * pin + int(row["output_tokens"]) * pout) / 1_000_000


def hard_from_rows(rows: list[dict], *, cost_key: str = "cost_usd") -> dict:
    n_q = sum(int(row.get("n_questions") or 0) for row in rows)
    n_c = sum(int(row.get("n_correct") or 0) for row in rows)
    n_inv = sum(int(row.get("n_invalid") or 0) for row in rows)
    n_cases = len(rows)
    n_case_ok = sum(1 for row in rows if row.get("case_correct"))
    costs = [float(row.get(cost_key) or 0) for row in rows]
    return {
        "n_cases": n_cases,
        "n_questions": n_q,
        "n_correct": n_c,
        "n_invalid": n_inv,
        "n_case_ok": n_case_ok,
        "p50_ms": p50([row.get("latency_ms") for row in rows]),
        "usd_per_1000": per_thousand(costs),
        "simulated": any(row.get("simulated") for row in rows),
    }


def load_hard_lane(path: Path, ids: set[str]) -> list[dict]:
    rows = []
    source = path
    if not source.exists():
        alt = source.parent / "full" / source.name
        if alt.exists():
            source = alt
    if source.exists():
        loaded = load_jsonl(source)
    else:
        from billproof.bench.hard.drex_archive import rows_for_missing

        loaded = rows_for_missing(path) or []
    for row in loaded:
        if row.get("case_id") in ids:
            rows.append(row)
    # last row per case
    last = {}
    for row in rows:
        last[row["case_id"]] = row
    return list(last.values())


def score_hard(lines: list[str]) -> tuple[list[str], list[dict]]:
    ids = json.loads((RUNS / "ladder_hard_ids.json").read_text())["run_order"]
    wanted = set(ids)
    table = []
    summaries = []
    for path in sorted(RUNS.glob("ladder_hard_*.jsonl")):
        rows = last_rows(path)
        rows = [row for row in rows if row.get("case_id") in wanted]
        if not rows:
            continue
        summary = hard_from_rows(rows)
        model = rows[-1].get("requested_model") or rows[-1].get("model")
        summary["model"] = model
        summary["case_ids"] = [row["case_id"] for row in rows]
        summary["source"] = str(path.relative_to(ROOT))
        summaries.append(summary)
        slug = path.stem.replace("ladder_hard_", "")
        source = summary["source"]
        acc = (summary["n_correct"] / summary["n_questions"]) if summary["n_questions"] else None
        write_metric(f"ladder_hard_{slug}_question_accuracy.json", acc, summary["n_questions"], source, summary["simulated"])
        write_metric(
            f"ladder_hard_{slug}_case_accuracy.json",
            (summary["n_case_ok"] / summary["n_cases"]) if summary["n_cases"] else None,
            summary["n_cases"], source, summary["simulated"],
        )
        write_metric(f"ladder_hard_{slug}_invalid_questions.json", summary["n_invalid"], summary["n_questions"], source, summary["simulated"])
        write_metric(f"ladder_hard_{slug}_p50_latency_ms.json", summary["p50_ms"], summary["n_cases"], source, summary["simulated"])
        write_metric(f"ladder_hard_{slug}_cost_per_1000_usd.json", summary["usd_per_1000"], summary["n_cases"], source, summary["simulated"])
        table.append(summary)

    gpt_rows: dict[str, dict] = {}
    gpt_from: dict[str, str] = {}
    for filename in ("hard_test_gpt.jsonl", "hard_test_gpt_subset.jsonl"):
        for row in load_hard_lane(RUNS / filename, wanted):
            gpt_rows[row["case_id"]] = row
            gpt_from[row["case_id"]] = filename
    reused = sorted(cid for cid, filename in gpt_from.items() if filename == "hard_test_gpt.jsonl")
    gpt_source = "data/runs/hard_test_gpt_subset.jsonl"
    if reused:
        gpt_source += " plus reused rows in hard_test_gpt.jsonl (" + ", ".join(reused) + ")"
    comparisons = [
        (
            "drex-max-v1.1",
            load_hard_lane(RUNS / "hard_test_drex-v1.jsonl", wanted),
            "data/runs/hard_test_drex-v1.jsonl",
            "stored Drex cost at $0.03 per million input tokens",
        ),
        (
            "drex-max-finance",
            load_hard_lane(RUNS / "hard_test_drex-finance.jsonl", wanted),
            "data/runs/hard_test_drex-finance.jsonl",
            "stored Drex cost at $0.03 per million input tokens",
        ),
        ("openai/gpt-6.1-sol", list(gpt_rows.values()), gpt_source, None),
        (
            "BillProof system: code + Drex",
            load_hard_lane(RUNS / "hard_test_billproof-system.jsonl", wanted),
            "data/runs/hard_test_billproof-system.jsonl",
            "stored Drex cost for the yes/no calls; code arithmetic is local and unbilled",
        ),
    ]
    for label, rows, source, note in comparisons:
        if not rows:
            continue
        summary = hard_from_rows(rows)
        summary["model"] = label
        summary["case_ids"] = [row["case_id"] for row in rows]
        summary["source"] = source
        summary["n_id_file"] = len(ids)
        if label.startswith("openai/"):
            listed = [token_cost(row, SOL_LIST_IN, SOL_LIST_OUT) for row in rows]
            listed = [c for c in listed if c is not None]
            if listed and len(listed) == len(rows):
                summary["usd_per_1000"] = per_thousand(listed)
            summary["cost_note"] = (
                "list $2/$10 per 1M, recomputed from stored tokens. "
                "The two reused rows were stored at $2.50/$15; this column does not use that rate."
            )
        else:
            summary["cost_note"] = note
        table.append(summary)
        summaries.append(summary)
    drex_by_id = {
        row["case_id"]: row
        for row in load_hard_lane(RUNS / "hard_test_drex-v1.jsonl", wanted)
    }
    lines.append(hard_markdown(ids, table, drex_by_id))
    return lines, table


def row_md(summary: dict) -> str:
    n_q = summary["n_questions"]
    acc = fmt_pct(summary["n_correct"], n_q) if n_q else "—"
    extra = ""
    if summary.get("usd_per_1000_list") is not None:
        extra = f" list {fmt_money(summary['usd_per_1000_list'])}"
    note = summary.get("cost_note") or "published list price"
    return (
        f"| {summary['model']} | {acc} | {summary['n_cases']} | {summary['n_invalid']} "
        f"| {fmt_ms(summary['p50_ms'])} | {fmt_money(summary['usd_per_1000'])}{extra} | {note} |"
    )


def hard_markdown(ids: list[str], table: list[dict], drex_by_id: dict) -> str:
    header = [
        "## BillProof-Hard",
        "",
        f"Frozen list `data/runs/ladder_hard_ids.json` ({len(ids)} of 300 test cases), "
        "rank `md5(\"hard-gpt100|20261114|{case_id}\")` ascending. "
        "This is the shared 100-case subset. GPT-6.1 Sol is packed rows on these ids "
        "(`hard_test_gpt_subset.jsonl`, with HARD-TEST-0002 and HARD-TEST-0004 reused from `hard_test_gpt.jsonl`). "
        "The BillProof system row is `hard_test_billproof-system.jsonl` (code computes the numbers; Drex answers the yes/no questions). "
        "One packed call per case, same `GPT_SYSTEM` and `questions_for` / `visible_state` as the packed runner. "
        "Gold is not in the state. Question accuracy is the primary number. "
        "Invalid answers stay in the denominator and count wrong. "
        "The 95% interval is a Wilson interval on questions. Questions in one case are not independent, "
        "so the interval is narrower than a case-clustered interval. "
        "DeepSeek's row is a prefix of `run_order`, not a sample chosen by score.",
        "",
        "| Model | Question accuracy (95% CI) | Cases | Invalid questions | p50 ms | $/1,000 cases | Cost basis |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    body = [row_md(row) for row in table]
    if not body:
        body = ["| — | — | 0 | — | — | — | no ladder rows yet |"]
    claims = hard_claims(table, drex_by_id)
    note = all_invalid_note()
    parts = header + body + ["", claims]
    if note:
        parts.extend(["", note])
    return "\n".join(parts + [""])


def _find(table: list[dict], name: str) -> dict | None:
    for row in table:
        if row.get("model") == name:
            return row
    return None


def _same_drex(case_ids: list[str], drex_by_id: dict) -> dict | None:
    rows = [drex_by_id[case_id] for case_id in case_ids if case_id in drex_by_id]
    if not rows:
        return None
    return hard_from_rows(rows)


PUBLISHED_HARD = {
    "drex-max-v1.1",
    "drex-max-finance",
    "openai/gpt-6.1-sol",
    "BillProof system: code + Drex",
}


def _fmt_ratio(ratio: float) -> str:
    if ratio < 10:
        return f"{ratio:.1f}"
    return f"{ratio:.0f}"


def _vs_drex(row: dict, drex_by_id: dict, *, full_list: int) -> str | None:
    same = _same_drex(row.get("case_ids") or [], drex_by_id)
    if not row["n_questions"] or same is None or not same["n_questions"]:
        return None
    rate = row["n_correct"] / row["n_questions"]
    d_rate = same["n_correct"] / same["n_questions"]
    cost = row["usd_per_1000"] or 0
    d_cost = same["usd_per_1000"] or 0
    ratio = (cost / d_cost) if d_cost else None
    scope = f"on the same {same['n_cases']} cases"
    if row["n_cases"] < full_list:
        kind = "a prefix" if row["model"] not in PUBLISHED_HARD else "an intersection"
        scope += f" ({kind} of {row['n_cases']} of {full_list})"
    name = f"`{row['model']}`"
    if ratio and ratio > 1.05 and rate > d_rate:
        return (
            f"- {name} is more accurate than Drex on the point estimate "
            f"({rate:.3f} vs {d_rate:.3f}) and costs {_fmt_ratio(ratio)}× more "
            f"(${cost:.2f} vs ${d_cost:.2f} per 1,000) {scope}."
        )
    if ratio and ratio > 1.05 and rate < d_rate:
        return (
            f"- Drex (`drex-max-v1.1`) is more accurate than {name} "
            f"({d_rate:.3f} vs {rate:.3f}) and {_fmt_ratio(ratio)}× cheaper "
            f"(${d_cost:.2f} vs ${cost:.2f} per 1,000 cases) {scope}."
        )
    return (
        f"- {name} question accuracy is {rate:.3f} and Drex is {d_rate:.3f} {scope}. "
        f"Cost per 1,000 is ${cost:.2f} vs ${d_cost:.2f}."
    )


def all_invalid_note() -> str:
    bits = []
    for path in sorted(RUNS.glob("ladder_hard_*.jsonl")):
        rows = last_rows(path)
        if not rows:
            continue
        whole = [
            row["case_id"]
            for row in rows
            if int(row.get("n_questions") or 0) > 0
            and int(row.get("n_invalid") or 0) == int(row.get("n_questions") or 0)
        ]
        partial = sum(
            int(row.get("n_invalid") or 0)
            for row in rows
            if row["case_id"] not in set(whole)
        )
        if not whole and not partial:
            continue
        model = rows[-1].get("requested_model") or rows[-1].get("model")
        if whole:
            bits.append(
                f"`{model}` all-invalid cases (every question counts wrong): {', '.join(sorted(whole))}"
                + (f"; {partial} further invalid questions are inside otherwise scored cases" if partial else "")
            )
        else:
            bits.append(f"`{model}` invalid questions are inside scored cases ({partial}), not a whole-case parse failure")
    if not bits:
        return ""
    return "Invalid answers stayed in the denominator. " + ". ".join(bits) + "."


def hard_claims(table: list[dict], drex_by_id: dict) -> str:
    drex = _find(table, "drex-max-v1.1")
    gpt = _find(table, "openai/gpt-6.1-sol")
    system = _find(table, "BillProof system: code + Drex")
    cheap = [row for row in table if row.get("model") not in PUBLISHED_HARD]
    if drex is None or not cheap:
        return "Claims wait until a cheap-model run and the Drex rows are both on this id list."
    full = drex["n_cases"]
    lines = [
        "Claims compare each model to Drex on the same case ids. "
        "The Drex row in the table is the full frozen list. "
        "A rate is higher only when the point estimate is higher. "
        "Question-level intervals are not used as a significance test.",
    ]
    for row in cheap:
        sentence = _vs_drex(row, drex_by_id, full_list=full)
        if sentence:
            lines.append(sentence)
    for row in (gpt, system):
        if not row:
            continue
        sentence = _vs_drex(row, drex_by_id, full_list=full)
        if sentence:
            lines.append(sentence)
    if system is None:
        lines.append("- BillProof system: no result file on these ids in this checkout. The row is pending.")
    return "\n".join(lines)


def score_pa(lines: list[str]) -> None:
    ids = json.loads((RUNS / "ladder_pa_ids.json").read_text())["run_order"]
    wanted = set(ids)
    lines.append("## Criteria-gym")
    lines.append("")
    lines.append(
        "Frozen list `data/runs/ladder_pa_ids.json`, 100 of 370 development panel, "
        "rank `md5(\"pa-ladder|0|{case_id}\")` ascending. Cheap models use `gpt_structured` "
        "(the dataset system and user messages, evidence checked). "
        "Drex's published column answers typed questions and its whole-case score does not require evidence, "
        "so whole-case is not the same definition. Decision accuracy is the comparable number. "
        "Wilson intervals treat cases as independent."
    )
    lines.append("")
    lines.append("| Model | Whole-case (95% CI) | Decision (95% CI) | Cases | Invalid | p50 ms | $/1,000 cases | Cost basis |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---|")
    any_row = False
    for path in sorted(RUNS.glob("ladder_pa_*.jsonl")):
        rows = [row for row in last_rows(path) if row.get("case_id") in wanted]
        if not rows:
            continue
        any_row = True
        lines.append(_pa_line(rows, rows[-1].get("requested_model") or rows[-1].get("model"), "published list price"))
        slug = path.stem.replace("ladder_pa_", "")
        source = str(path.relative_to(ROOT))
        n = len(rows)
        k = sum(1 for row in rows if row.get("whole_case"))
        d = sum(1 for row in rows if row.get("decision"))
        inv = sum(1 for row in rows if row.get("invalid"))
        sim = any(row.get("simulated") for row in rows)
        write_metric(f"ladder_pa_{slug}_whole_case.json", k / n, n, source, sim)
        write_metric(f"ladder_pa_{slug}_decision.json", d / n, n, source, sim)
        write_metric(f"ladder_pa_{slug}_invalid_cases.json", inv, n, source, sim)
        write_metric(f"ladder_pa_{slug}_p50_latency_ms.json", p50([row.get("latency_ms") for row in rows]), n, source, sim)
        write_metric(f"ladder_pa_{slug}_cost_per_1000_usd.json", per_thousand([float(row.get("cost_usd") or 0) for row in rows]), n, source, sim)
    for label, filename, basis in (
        ("drex-max-v1.1", "pa_drex_development.jsonl", "stored Drex cost; whole-case excludes evidence"),
        ("openai/gpt-6.1-sol", "pa_gpt_development.jsonl", "stored cost is configured $2.50/$15; see list note"),
    ):
        rows = []
        last = {}
        for row in load_jsonl(RUNS / filename):
            if row.get("case_id") in wanted:
                last[row["case_id"]] = row
        for row in last.values():
            score = row.get("score") or {}
            rows.append({
                "whole_case": bool(score.get("whole_case")),
                "decision": bool(score.get("decision")),
                "invalid": bool(row.get("error")),
                "latency_ms": row.get("latency_ms"),
                "cost_usd": row.get("cost_usd") or 0,
                "input_tokens": row.get("input_tokens"),
                "output_tokens": row.get("output_tokens"),
            })
        if not rows:
            continue
        any_row = True
        note = basis
        if label.startswith("openai/"):
            repriced = []
            for row in rows:
                cost = token_cost(row, SOL_LIST_IN, SOL_LIST_OUT)
                if cost is not None:
                    row["cost_usd"] = cost
                    repriced.append(cost)
            note = "list $2/$10 per 1M, recomputed from stored tokens" if repriced else note
        lines.append(_pa_line(rows, label, note))
    if not any_row:
        lines.append("| — | — | — | 0 | — | — | — | no rows yet |")
    lines.append("")
    lines.extend(pa_claims(ids))
    lines.append("")
    lines.append("BillProof system: no result file on these ids. The row is pending.")
    lines.append("")


def _pa_stats(rows: list[dict]) -> dict:
    n = len(rows)
    return {
        "n": n,
        "whole": sum(1 for row in rows if row.get("whole_case")),
        "decision": sum(1 for row in rows if row.get("decision")),
        "usd_per_1000": per_thousand([float(row.get("cost_usd") or 0) for row in rows]),
    }


def pa_claims(ids: list[str]) -> list[str]:
    wanted = set(ids)
    published = {}
    for label, filename in (
        ("drex-max-v1.1", "pa_drex_development.jsonl"),
        ("openai/gpt-6.1-sol", "pa_gpt_development.jsonl"),
    ):
        last = {}
        for row in load_jsonl(RUNS / filename):
            if row.get("case_id") in wanted:
                last[row["case_id"]] = row
        rows = []
        for row in last.values():
            score = row.get("score") or {}
            cost = row.get("cost_usd") or 0
            if label.startswith("openai/"):
                listed = token_cost(row, SOL_LIST_IN, SOL_LIST_OUT)
                if listed is not None:
                    cost = listed
            rows.append({
                "whole_case": bool(score.get("whole_case")),
                "decision": bool(score.get("decision")),
                "cost_usd": cost,
            })
        if rows:
            published[label] = _pa_stats(rows)
    lines = [
        "Decision accuracy is the comparable number. Drex whole-case does not require evidence, so whole-case is not compared in the claims.",
    ]
    drex = published.get("drex-max-v1.1")
    gpt = published.get("openai/gpt-6.1-sol")
    if drex is None:
        return lines
    d_rate = drex["decision"] / drex["n"] if drex["n"] else None
    d_cost = drex["usd_per_1000"] or 0
    for path in sorted(RUNS.glob("ladder_pa_*.jsonl")):
        rows = [row for row in last_rows(path) if row.get("case_id") in wanted]
        if not rows or d_rate is None:
            continue
        stats = _pa_stats(rows)
        model = rows[-1].get("requested_model") or rows[-1].get("model")
        rate = stats["decision"] / stats["n"]
        cost = stats["usd_per_1000"] or 0
        ratio = (cost / d_cost) if d_cost else None
        lo, hi = wilson_ci(stats["decision"], stats["n"])
        dlo, dhi = wilson_ci(drex["decision"], drex["n"])
        if abs(rate - d_rate) < 1e-12:
            overlap = ""
        elif lo <= dhi and dlo <= hi:
            overlap = " The 95% intervals overlap."
        else:
            overlap = " The 95% intervals do not overlap."
        if rate < d_rate and ratio and ratio > 1:
            lines.append(
                f"- On the decision, Drex is more accurate than `{model}` on the point estimate ({d_rate:.3f} vs {rate:.3f}) "
                f"and {ratio:.0f}× cheaper (${d_cost:.2f} vs ${cost:.2f} per 1,000) on these {stats['n']} cases.{overlap}"
            )
        elif rate > d_rate and ratio and ratio > 1:
            lines.append(
                f"- On the decision, `{model}` is more accurate than Drex on the point estimate "
                f"({rate:.3f} vs {d_rate:.3f}) and costs {ratio:.0f}× more "
                f"(${cost:.2f} vs ${d_cost:.2f} per 1,000) on these {stats['n']} cases.{overlap}"
            )
        elif abs(rate - d_rate) < 1e-12 and ratio and ratio > 1:
            lines.append(
                f"- On the decision, `{model}` matches Drex at {rate:.3f} and costs {ratio:.0f}× more "
                f"(${cost:.2f} vs ${d_cost:.2f} per 1,000) on these {stats['n']} cases."
            )
        else:
            lines.append(
                f"- On the decision, `{model}` is {rate:.3f} and Drex is {d_rate:.3f}. "
                f"Cost per 1,000 is ${cost:.2f} vs ${d_cost:.2f}."
            )
    if gpt and gpt["n"] and d_cost:
        g_rate = gpt["decision"] / gpt["n"]
        g_cost = gpt["usd_per_1000"] or 0
        mult = (g_cost / d_cost) if d_cost else None
        lines.append(
            f"- GPT-6.1 Sol decision accuracy on these {gpt['n']} ids is {g_rate:.3f}, Drex is {d_rate:.3f}. "
            + (f"List $2/$10 is ${g_cost:.2f} per 1,000, {mult:.0f}× Drex." if mult else "")
        )
    return lines


def _pa_line(rows: list[dict], model: str, note: str) -> str:
    n = len(rows)
    k = sum(1 for row in rows if row.get("whole_case"))
    d = sum(1 for row in rows if row.get("decision"))
    inv = sum(1 for row in rows if row.get("invalid"))
    return (
        f"| {model} | {fmt_pct(k, n)} | {fmt_pct(d, n)} | {n} | {inv} "
        f"| {fmt_ms(p50([row.get('latency_ms') for row in rows]))} "
        f"| {fmt_money(per_thousand([float(row.get('cost_usd') or 0) for row in rows]))} | {note} |"
    )


def score_persius(lines: list[str]) -> None:
    document = json.loads((RUNS / "ladder_persius_ids.json").read_text())
    items = document["run_order"]
    wanted = {item["case_id"] for item in items}
    lines.append("## Persius")
    lines.append("")
    lines.append(
        "Frozen list `data/runs/ladder_persius_ids.json`: first 100 `yes` and first 100 `no` "
        "by `rank_md5` inside frozen 400-case test sample. "
        "A cheap-model run would use the fair neutral prompt (`FairFrontier`). "
        "Case accuracy. Invalid answers stay in the denominator. "
        ""
        "No cheap model was called. DeepSeek's Hard rows are a prefix of run_order."
    )
    lines.append("")
    lines.append("| Model | Accuracy (95% CI) | Cases | Invalid | p50 ms | $/1,000 cases | Cost basis |")
    lines.append("|---|---:|---:|---:|---:|---:|---|")
    any_row = False
    for path in sorted(RUNS.glob("ladder_persius_*.jsonl")):
        rows = [row for row in last_rows(path) if row.get("case_id") in wanted]
        if not rows:
            continue
        any_row = True
        lines.append(_case_line(rows, rows[-1].get("requested_model") or rows[-1].get("model"), "published list price", key="correct"))
        slug = path.stem.replace("ladder_persius_", "")
        source = str(path.relative_to(ROOT))
        n = len(rows)
        k = sum(1 for row in rows if row.get("correct"))
        inv = sum(1 for row in rows if row.get("invalid"))
        sim = any(row.get("simulated") for row in rows)
        write_metric(f"ladder_persius_{slug}_accuracy.json", k / n if n else None, n, source, sim)
        write_metric(f"ladder_persius_{slug}_invalid_cases.json", inv, n, source, sim)
        write_metric(f"ladder_persius_{slug}_p50_latency_ms.json", p50([row.get("latency_ms") for row in rows]), n, source, sim)
        write_metric(f"ladder_persius_{slug}_cost_per_1000_usd.json", per_thousand([float(row.get("cost_usd") or 0) for row in rows]), n, source, sim)
    last = {}
    for row in load_jsonl(RUNS / "c_persius_test.jsonl"):
        if row.get("case_id") in wanted:
            last[(row.get("lane"), row.get("case_id"))] = row
    for lane, label, basis in (
        ("drex", "drex-max-v1.1", "stored Drex cost"),
        ("frontier", "openai/gpt-6.1-sol", "stored frontier cost; confirm prompt sha in the source row"),
    ):
        rows = [row for (got, _), row in last.items() if got == lane]
        if not rows:
            continue
        any_row = True
        if lane == "frontier":
            repriced = 0
            for row in rows:
                cost = token_cost(row, SOL_LIST_IN, SOL_LIST_OUT)
                if cost is not None:
                    row["cost_usd"] = cost
                    repriced += 1
            sha = rows[0].get("gpt_system_sha256")
            basis = "list $2/$10 per 1M, recomputed from stored tokens" if repriced else basis
            if sha:
                basis += f"; gpt_system_sha256 {sha[:12]}"
        lines.append(_case_line(rows, label, basis, key="correct"))
    if not any_row:
        lines.append("| — | — | 0 | — | — | — | no rows yet |")
    lines.append("")
    lines.append("BillProof system: no result file on these ids. The row is pending.")
    lines.append("")


def _case_line(rows: list[dict], model: str, note: str, *, key: str) -> str:
    n = len(rows)
    k = sum(1 for row in rows if row.get(key) and not row.get("invalid"))
    inv = sum(1 for row in rows if row.get("invalid") or row.get("error"))
    # An error with a scored correct flag still counts invalid if the flag is set.
    # Drex rows have error None. Don't double-count a correct row that has no error.
    if key == "correct":
        inv = sum(1 for row in rows if row.get("invalid") or (row.get("error") and not row.get("correct")))
    return (
        f"| {model} | {fmt_pct(k, n)} | {n} | {inv} "
        f"| {fmt_ms(p50([row.get('latency_ms') for row in rows]))} "
        f"| {fmt_money(per_thousand([float(row.get('cost_usd') or 0) for row in rows]))} | {note} |"
    )


WORDING = "Scored on frozen test sets with no tuning."
LADDER_MODELS = (
    "openai/gpt-5.4-mini",
    "anthropic/claude-haiku-5.5",
    "google/gemini-3.5-flash-lite",
    "deepseek/deepseek-v4.1-flash",
)
SUMMARY = METRICS / "ladder_summary.json"


def _last_scored(path: Path) -> dict[str, dict]:
    last: dict[str, dict] = {}
    for row in load_jsonl(path):
        case_id = row.get("case_id")
        if case_id:
            last[case_id] = row
    return {
        case_id: row
        for case_id, row in last.items()
        if not row.get("protocol_discard") and not row.get("retry")
    }


def _prefix(order: list[str], by_id: dict[str, dict]) -> list[dict]:
    """Stop at the first missing id. Later ids are not pulled in."""
    rows = []
    for case_id in order:
        row = by_id.get(case_id)
        if row is None:
            break
        rows.append(row)
    return rows


def _median_s(rows: list[dict]) -> float | None:
    values = [float(row["latency_ms"]) / 1000.0 for row in rows if row.get("latency_ms") is not None]
    if not values:
        return None
    return float(statistics.median(values))


def _cost_1k(costs: list[float]) -> float | None:
    if not costs:
        return None
    return 1000.0 * sum(costs) / len(costs)


def _hard_records(ids: list[str] | None = None) -> dict[str, dict]:
    from billproof.bench.hard.runner import load_cases

    wanted = None if ids is None else set(ids)
    return {
        row["case_id"]: row
        for row in load_cases(ROOT / "data" / "synthetic" / "hard" / "test.jsonl")
        if wanted is None or row["case_id"] in wanted
    }


HARD_METRIC = "line_verdict"
HARD_METRIC_DETAIL = (
    "Line verdict: a line is right only when all six line questions match gold. "
    "Same rule as billproof.bench.hard.outcomes.score_lane and the line_verdict field in "
    "data/metrics/hard_test_outcomes.json. Not question accuracy. Not OK-versus-dispute."
)
PA_METRIC = "decision"
PA_METRIC_DETAIL = (
    "Decision right on the whole request. Not a line rate and not question accuracy. "
    "whole_case is stored beside this number. Drex whole-case does not require evidence, "
    "so whole_case is not the plotted accuracy."
)


def _stamp_gold(rows: list[dict], records: dict[str, dict]) -> list[dict]:
    """Copy asked-question gold onto rows. Ladder JSONL does not store gold."""
    stamped = []
    for row in rows:
        record = records.get(row.get("case_id"))
        if record is None:
            continue
        item = dict(row)
        gold_all = record.get("gold") or {}
        asked = row.get("answers") or {}
        if row.get("gold"):
            item["gold"] = row["gold"]
        elif asked:
            item["gold"] = {name: gold_all[name] for name in asked if name in gold_all}
        else:
            item["gold"] = dict(gold_all)
        stamped.append(item)
    return stamped


def _require_published_line(rows: list[dict], records: dict[str, dict], lane: dict, label: str) -> None:
    """Refuse to publish a ladder rate until this stamp matches the metrics file."""
    from billproof.bench.hard.outcomes import score_lane

    expect_k = int(lane["line_verdict"]["k"])
    expect_n = int(lane["line_verdict"]["n"])
    native = score_lane(rows, records)
    stamped = score_lane(_stamp_gold(rows, records), records)
    if (native["line_k"], native["line_n"]) != (expect_k, expect_n):
        raise RuntimeError(f"{label} native {native['line_k']}/{native['line_n']} != {expect_k}/{expect_n}")
    if (stamped["line_k"], stamped["line_n"]) != (expect_k, expect_n):
        raise RuntimeError(f"{label} stamp {stamped['line_k']}/{stamped['line_n']} != {expect_k}/{expect_n}")
    published = float(lane["line_verdict"]["value"])
    if expect_n and abs(published - (expect_k / expect_n)) > 1e-12:
        raise RuntimeError(f"{label} file value {published} != {expect_k}/{expect_n}")


def _line_accuracy(rows: list[dict], records: dict[str, dict]) -> tuple[float, int, int]:
    from billproof.bench.hard.outcomes import score_lane

    scored = score_lane(_stamp_gold(rows, records), records)
    if not scored["line_n"]:
        raise RuntimeError("line verdict denominator is empty")
    return scored["line_k"] / scored["line_n"], scored["line_k"], scored["line_n"]


def _question_accuracy(rows: list[dict]) -> tuple[float | None, int, int]:
    n_q = sum(int(row.get("n_questions") or 0) for row in rows)
    n_c = sum(int(row.get("n_correct") or 0) for row in rows)
    if not n_q:
        return None, n_c, n_q
    return n_c / n_q, n_c, n_q


def _list_cost(rows: list[dict], *, gpt: bool) -> float:
    costs = []
    for row in rows:
        if gpt:
            listed = token_cost(row, SOL_LIST_IN, SOL_LIST_OUT)
            if listed is None:
                raise RuntimeError(f"missing tokens for {row.get('case_id')}")
            costs.append(listed)
        else:
            costs.append(float(row.get("cost_usd") or 0))
    cost = _cost_1k(costs)
    if cost is None or cost <= 0:
        raise RuntimeError("list cost per 1,000 is not positive")
    return cost


def _point(
    *,
    model: str,
    benchmark: str,
    n: int,
    n_planned: int,
    accuracy: float,
    accuracy_metric: str,
    accuracy_metric_detail: str,
    cost: float,
    source: str,
    cost_source: str,
    reference: bool,
    extra: dict,
) -> dict:
    if n <= 0 or accuracy is None or cost is None or cost <= 0:
        raise RuntimeError(f"refusing a point the chart would drop: {model} {benchmark}")
    partial = n < n_planned
    row = {
        "model": model,
        "benchmark": benchmark,
        "bench": benchmark,
        "test": benchmark,
        "cost_per_1000_list_usd": cost,
        "cost_per_1000_usd": cost,
        "cost_per_1k_list": cost,
        "cost_is_list_price": True,
        "accuracy": accuracy,
        "accuracy_metric": accuracy_metric,
        "accuracy_metric_detail": accuracy_metric_detail,
        "n": n,
        "n_planned": n_planned,
        "partial": partial,
        "source": source,
        "cost_source": cost_source,
        "source_file": source,
        "reference": reference,
        "result_label": f"partial, n={n}" if partial else f"n={n}",
        "computed_at": now(),
        "wording": WORDING,
        "simulated": False,
    }
    row.update(extra)
    return row


def _unique_rows(rows: list[dict]) -> list[dict]:
    last: dict[str, dict] = {}
    for row in rows:
        if row.get("case_id"):
            last[row["case_id"]] = row
    return list(last.values())


DREX_LIST_PER_MTOK = 0.03


def _whole_case(row: dict) -> bool:
    if "whole_case" in row:
        return bool(row["whole_case"])
    score = row.get("score") or {}
    if "whole_case" in score:
        return bool(score["whole_case"])
    raise RuntimeError(f"no whole_case on {row.get('case_id')}")


def _list_usd(row: dict, kind: str) -> float:
    """List price from stored token counts. GPT is $2/$10 per 1M, not $2.50/$15."""
    if kind == "gpt":
        cost = token_cost(row, SOL_LIST_IN, SOL_LIST_OUT)
        if cost is None:
            raise RuntimeError(f"missing tokens for {row.get('case_id')}")
        return cost
    if kind == "drex":
        if row.get("input_tokens") is None:
            raise RuntimeError(f"missing input tokens for {row.get('case_id')}")
        return int(row["input_tokens"]) * DREX_LIST_PER_MTOK / 1_000_000
    return float(row.get("cost_usd") or 0)


def _chart_point(model: str, benchmark: str, rows: list[dict], n_planned: int, source_run: str, kind: str) -> dict:
    n = len(rows)
    if benchmark == "hard":
        _accuracy, correct, questions = _question_accuracy(rows)
        if questions <= 0 or _accuracy is None:
            raise RuntimeError(f"{model} hard has no questions")
        accuracy = correct / questions
    elif benchmark == "pa":
        accuracy = sum(1 for row in rows if _whole_case(row)) / n
    else:
        raise RuntimeError(benchmark)
    cost = _cost_1k([_list_usd(row, kind) for row in rows])
    if n <= 0 or cost is None or cost <= 0:
        raise RuntimeError(f"refusing {model} {benchmark}")
    return {
        "model": model,
        "benchmark": benchmark,
        "accuracy": accuracy,
        "n": n,
        "cost_per_1000_list_usd": cost,
        "partial": n < n_planned,
        "source_run": source_run,
    }


def _pa_prefix(filename: str, ids: list[str]) -> list[dict]:
    wanted = set(ids)
    last: dict[str, dict] = {}
    for row in load_jsonl(RUNS / filename):
        if row.get("case_id") in wanted:
            last[row["case_id"]] = row
    return _prefix(ids, last)


def write_ladder_summary() -> Path:
    """Published ladder chart. Hard is question accuracy. PA is whole-case. No model calls."""
    from billproof.bench.hard.outcomes import frozen_subset_rows, gpt_subset_rows

    hard_ids = json.loads((RUNS / "ladder_hard_ids.json").read_text())["run_order"]
    pa_ids = json.loads((RUNS / "ladder_pa_ids.json").read_text())["run_order"]
    points: list[dict] = []

    hard_by_model: dict[str, tuple[str, list[dict]]] = {}
    for path in sorted(RUNS.glob("ladder_hard_*.jsonl")):
        scored = _last_scored(path)
        prefix = _prefix(hard_ids, scored)
        model = (prefix[-1].get("requested_model") if prefix else None) or path.stem
        hard_by_model[model] = (str(path.relative_to(ROOT)), prefix)
    for model in LADDER_MODELS:
        found = hard_by_model.get(model)
        if not found:
            continue
        source, prefix = found
        got = [row["case_id"] for row in prefix]
        if got != hard_ids[: len(got)]:
            raise RuntimeError(f"{model} hard rows are not a prefix of the frozen order")
        if model == "anthropic/claude-haiku-5.5":
            by_id = {row["case_id"]: row for row in prefix}
            for case_id in ("HARD-TEST-0018", "HARD-TEST-0107"):
                row = by_id.get(case_id)
                if row is None or int(row.get("n_correct") or 0) != 0 or int(row.get("n_invalid") or 0) <= 0:
                    raise RuntimeError(f"{case_id} must stay scored wrong")
        points.append(_chart_point(model, "hard", prefix, len(hard_ids), source, "ladder"))

    gpt_rows = gpt_subset_rows()
    correct = sum(int(row.get("n_correct") or 0) for row in gpt_rows)
    questions = sum(int(row.get("n_questions") or 0) for row in gpt_rows)
    if (correct, questions, len(gpt_rows)) != (18706, 18706, 100):
        raise RuntimeError(f"GPT-6.1 Sol packed hard is {correct}/{questions} on {len(gpt_rows)} cases")
    points.append(_chart_point(
        "openai/gpt-6.1-sol", "hard", gpt_rows, len(hard_ids),
        "data/runs/hard_test_gpt_subset.jsonl plus reused HARD-TEST-0002 and HARD-TEST-0004 "
        "from data/runs/hard_test_gpt.jsonl",
        "gpt",
    ))
    for model, lane in (("drex-max-v1.1", "drex-v1"), ("drex-max-finance", "drex-finance")):
        rows = frozen_subset_rows(lane)
        if len(rows) != len(hard_ids):
            raise RuntimeError(f"{lane} covers {len(rows)} of the frozen 100")
        points.append(_chart_point(
            model, "hard", rows, len(hard_ids),
            f"data/runs/full/hard_test_{lane}.jsonl",
            "drex",
        ))

    pa_by_model: dict[str, tuple[str, list[dict]]] = {}
    for path in sorted(RUNS.glob("ladder_pa_*.jsonl")):
        scored = _last_scored(path)
        prefix = _prefix(pa_ids, scored)
        model = (prefix[-1].get("requested_model") if prefix else None) or path.stem
        pa_by_model[model] = (str(path.relative_to(ROOT)), prefix)
    for model in LADDER_MODELS:
        found = pa_by_model.get(model)
        if not found:
            continue
        source, prefix = found
        got = [row["case_id"] for row in prefix]
        if got != pa_ids[: len(got)]:
            raise RuntimeError(f"{model} pa rows are not a prefix of the frozen order")
        points.append(_chart_point(model, "pa", prefix, len(pa_ids), source, "ladder"))

    for model, filename, kind in (
        ("openai/gpt-6.1-sol", "pa_gpt_development.jsonl", "gpt"),
        ("drex-max-v1.1", "pa_drex_development.jsonl", "drex"),
        ("drex-max-finance", "pa_drexfin_development.jsonl", "drex"),
    ):
        prefix = _pa_prefix(filename, pa_ids)
        if len(prefix) != len(pa_ids):
            continue
        points.append(_chart_point(
            model, "pa", prefix, len(pa_ids), f"data/runs/{filename}", kind,
        ))

    if any(point["benchmark"] not in {"hard", "pa"} for point in points):
        raise RuntimeError("only hard and pa were run")
    payload = {"points": points, "computed_at": now()}
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {SUMMARY} points={len(points)} spend={ladder_spend():.4f}")
    for point in points:
        print(
            f"{point['benchmark']:4} partial={str(point['partial']):5} n={point['n']:3} "
            f"{point['accuracy']:.6f} ${point['cost_per_1000_list_usd']:.4f} {point['model']}"
        )
    return SUMMARY


def main() -> None:
    calls = {}
    path = RUNS / "ladder_router_calls.json"
    if path.exists():
        calls = json.loads(path.read_text())
    lines = [
        "# Cost ladder",
        "",
        "Cheap, widely used models on the same frozen ids and the same question text as "
        "GPT-6.1 Sol and Drex. Prices are published list rates in `data/runs/ladder_prices.json`. "
        "GPT-6.1 Sol dollars are recomputed from stored token counts at $2/$10 per 1M. "
        f"Router calls counted by the ladder: {calls.get('calls', 0)} of {calls.get('cap', 1200)}. "
        f"List-price spend on ladder rows, including discarded attempts: ${ladder_spend():.2f} of $20. "
        f"Computed {now()}.",
        "",
        "A run that stops early is a prefix of `run_order`. Ids were not dropped because of the outcome.",
        "",
    ]
    score_hard(lines)
    score_pa(lines)
    score_persius(lines)
    lines.append(output_cap_note())
    lines.append("## What this does not say")
    lines.append("")
    lines.append(
        "Probe calls are not in the tables. "
        "`openai/gpt-5.4-nano` was dropped: every probe returned HTTP 403, a provider Terms of Service block. "
        "Its list price ($0.20/$1.25 per 1M) was not used. "
        "`google/gemini-3.5-flash` was dropped: the current pricing page gives it no price of its own "
        "and routes it to `gemini-3.6-flash`. It was not run. "
        "DeepSeek's rate is the weekend off-peak cache-miss rate. "
        "No Nace call was made for this ladder."
    )
    lines.append("")
    DOC.write_text("\n".join(lines))
    print(f"wrote {DOC}")
    write_ladder_summary()


if __name__ == "__main__":
    main()
