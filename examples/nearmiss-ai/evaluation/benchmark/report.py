"""Build the benchmark report from the raw results. Reads files only; makes no API calls.

  python -m evaluation.benchmark.report --name main --db data/asrs.duckdb \
      --drex-input-price <USD per million tokens> --openai-input-price <USD/M> --openai-output-price <USD/M>

Prices are never assumed: without them the report shows token counts and says cost was not computed.
Writes results/<name>/report.md and results/<name>/summary.json.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

from evaluation.benchmark import metrics
from evaluation.benchmark.run import RESULTS_ROOT, load_rows

# Fixed before any results were seen; deliberately simple, never tuned on the sample.
COMMUNICATION_KEYWORDS = (
    "readback", "read back", "misunderstood", "misunderstanding", "miscommunication", "miscommunicat",
    "communication", "did not hear", "didn't hear", "unclear", "confusion", "confused", "clarif",
    "did not tell", "didn't tell", "no response", "not informed", "failed to inform", "was not aware",
)

LIMITATIONS = """\
- **Labels are NASA analyst codes, not verified truth.** ASRS reports are voluntary and unverified. For the
  communication task a blank "Communication Breakdown" cell means *none was coded*, not that none occurred, and
  the field covers Person 1 only, so some "no_breakdown" cases may genuinely describe a breakdown.
- **Option descriptions are ours.** They are written for this benchmark and are not NASA's official definitions;
  both providers see identical text.
- **Class-balanced samples, not natural prevalence.** Accuracy here is not the rate on the raw ASRS stream.
- **One export, one time window.** July 2023 to June 2024, seeded sample; results will vary with other samples.
- **Single prompt per task, zero examples, no tuning for either provider.** Better prompts could move either side.
- **Latency is wall-clock per call including each SDK's default retries,** measured from this machine with the
  recorded parallelism; it is not a controlled network benchmark.
- **No document evidence (NDI) is used.** These tasks classify the narrative alone, unlike the 4-agent swarm.
- **Costs use the prices you supplied** and the token counts each API returned; no price was assumed."""


def _fmt(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None or (isinstance(value, float) and math.isnan(value)) else f"{value:.{digits}f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None or (isinstance(value, float) and math.isnan(value)) else f"{100 * value:.1f}%"


def _clean(value):
    """Make NaN JSON-safe."""
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _is_valid(row: dict) -> bool:
    return row.get("error") is None and row.get("option") is not None


def provider_stats(rows: list[dict], truth: dict[str, str], labels: list[str], prices: dict[str, tuple[float | None, float | None]], provider: str) -> dict:
    valid = [r for r in rows if _is_valid(r)]
    ok = [r for r in rows if r.get("error") is None]
    y = [truth[r["case_id"]] for r in valid]
    p = [r["option"] for r in valid]
    correct = sum(a == b for a, b in zip(y, p))
    low, high = metrics.wilson_interval(correct, len(valid))
    model_counts = Counter(r.get("model") for r in rows if r.get("model"))
    in_price, out_price = prices.get(provider, (None, None))
    stats = {
        "attempted": len(rows),
        "valid": len(valid),
        "errors": dict(Counter((r["error"] or {}).get("code") for r in rows if r.get("error"))),
        "model": model_counts.most_common(1)[0][0] if model_counts else None,
        "accuracy": metrics.accuracy(y, p),
        "accuracy_ci": [low, high],
        "strict_accuracy": correct / len(rows) if rows else float("nan"),  # errors and invalid outputs count as wrong
        "macro_f1": metrics.macro_f1(y, p, labels) if valid else float("nan"),
        "per_class": metrics.per_class(y, p, labels) if valid else {},
        "confusion": metrics.confusion(y, p, labels) if valid else {},
        "latency_seconds": metrics.latency_summary([r["latency_seconds"] for r in ok]),
        "mean_input_tokens": (sum(r["input_tokens"] for r in ok if r.get("input_tokens") is not None) / max(1, sum(r.get("input_tokens") is not None for r in ok))) if ok else None,
        "cost_per_1000_usd": metrics.cost_per_1000([r.get("input_tokens") for r in ok], [r.get("output_tokens") for r in ok], in_price, out_price),
    }
    probabilistic = [r for r in valid if r.get("probabilities")]
    if probabilistic:
        conf = [max(r["probabilities"].values()) for r in probabilistic]
        right = [r["option"] == truth[r["case_id"]] for r in probabilistic]
        stats["calibration"] = {
            "mean_confidence": sum(conf) / len(conf),
            "ece": metrics.expected_calibration_error(conf, right),
            "brier": metrics.brier([r["probabilities"] for r in probabilistic], [truth[r["case_id"]] for r in probabilistic], labels),
        }
    return stats


def baseline_stats(name: str, truth: dict[str, str], predictions: dict[str, str], labels: list[str]) -> dict:
    ids = sorted(truth)
    y = [truth[i] for i in ids]
    p = [predictions[i] for i in ids]
    correct = sum(a == b for a, b in zip(y, p))
    low, high = metrics.wilson_interval(correct, len(ids))
    return {"name": name, "accuracy": metrics.accuracy(y, p), "accuracy_ci": [low, high], "macro_f1": metrics.macro_f1(y, p, labels)}


def head_to_head(drex_rows: dict[str, dict], openai_rows: dict[str, dict], truth: dict[str, str], seed: int) -> dict | None:
    both = sorted(i for i in truth if i in drex_rows and i in openai_rows and _is_valid(drex_rows[i]) and _is_valid(openai_rows[i]))
    if not both:
        return None
    d = [drex_rows[i]["option"] == truth[i] for i in both]
    o = [openai_rows[i]["option"] == truth[i] for i in both]
    only_d = sum(a and not b for a, b in zip(d, o))
    only_o = sum(b and not a for a, b in zip(d, o))
    diff, low, high = metrics.paired_bootstrap_diff(d, o, seed=seed)
    return {
        "paired_cases": len(both),
        "both_correct": sum(a and b for a, b in zip(d, o)),
        "only_drex_correct": only_d,
        "only_openai_correct": only_o,
        "neither_correct": sum(not a and not b for a, b in zip(d, o)),
        "agreement": sum(drex_rows[i]["option"] == openai_rows[i]["option"] for i in both) / len(both),
        "accuracy_diff_drex_minus_openai": diff,
        "accuracy_diff_ci": [low, high],
        "mcnemar_p": metrics.mcnemar_exact(only_d, only_o),
    }


def _verdict(h2h: dict | None, drex: dict, oai: dict) -> list[str]:
    lines = []
    if h2h:
        low, high = h2h["accuracy_diff_ci"]
        if low > 0:
            lines.append("Accuracy: Drex scored higher; the 95% interval of the paired difference excludes 0.")
        elif high < 0:
            lines.append("Accuracy: OpenAI scored higher; the 95% interval of the paired difference excludes 0.")
        else:
            lines.append("Accuracy: no statistically clear difference (the 95% interval of the paired difference includes 0).")
    dl, ol = drex["latency_seconds"]["median"], oai["latency_seconds"]["median"]
    if not (math.isnan(dl) or math.isnan(ol)) and dl > 0 and ol > 0:
        faster, ratio = ("Drex", ol / dl) if dl < ol else ("OpenAI", dl / ol)
        lines.append(f"Latency: {faster} was faster at the median ({ratio:.1f}x).")
    dc, oc = drex["cost_per_1000_usd"], oai["cost_per_1000_usd"]
    if dc is not None and oc is not None and dc > 0 and oc > 0:
        cheaper, ratio = ("Drex", oc / dc) if dc < oc else ("OpenAI", dc / oc)
        lines.append(f"Cost: {cheaper} was cheaper per 1,000 decisions ({ratio:.1f}x) at the supplied prices.")
    else:
        lines.append("Cost: not compared (supply prices for both providers).")
    return lines


def build_summary(name: str, db_path: Path | None, prices: dict[str, tuple[float | None, float | None]]) -> dict:
    directory = RESULTS_ROOT / name
    meta = json.loads((directory / "run_meta.json").read_text(encoding="utf-8"))
    rows = load_rows(directory / "raw.jsonl")
    narratives: dict[str, str] = {}
    if db_path and db_path.exists():
        import duckdb

        ids = sorted({cid for info in meta["tasks"].values() for cid in info["cases"]})
        with duckdb.connect(str(db_path), read_only=True) as db:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                for acn, text in db.execute(f"SELECT acn, narrative FROM incidents WHERE acn IN ({','.join('?' * len(chunk))})", chunk).fetchall():
                    narratives[acn] = text

    summary: dict = {"name": name, "meta": {k: meta[k] for k in ("created_at", "seed", "n_requested", "workers", "providers", "openai_mode", "openai_temperature")}, "tasks": {}}
    for task_name, info in meta["tasks"].items():
        labels, truth = list(info["options"]), info["cases"]
        by_provider = {}
        for provider in meta["providers"]:
            by_provider[provider] = {r["case_id"]: r for (t, _, p), r in rows.items() if t == task_name and p == provider}
        task = {"title": info["title"], "ground_truth_field": info["ground_truth_field"], "labels": labels, "n_cases": len(truth), "providers": {}, "baselines": []}
        for provider, by_case in by_provider.items():
            task["providers"][provider] = provider_stats(list(by_case.values()), truth, labels, prices, provider)
        majority = Counter(truth.values()).most_common(1)[0][0]
        task["baselines"].append(baseline_stats(f"Always '{majority}' (majority class)", truth, {i: majority for i in truth}, labels))
        if task_name == "communication_breakdown" and all(i in narratives for i in truth):
            guess = {i: ("breakdown" if any(k in narratives[i].lower() for k in COMMUNICATION_KEYWORDS) else "no_breakdown") for i in truth}
            task["baselines"].append(baseline_stats(f"Keyword rule ({len(COMMUNICATION_KEYWORDS)} fixed terms)", truth, guess, labels))
        if "drex" in by_provider and "openai" in by_provider:
            task["head_to_head"] = head_to_head(by_provider["drex"], by_provider["openai"], truth, meta["seed"])
            if task["providers"]["drex"]["valid"] and task["providers"]["openai"]["valid"]:
                task["verdict"] = _verdict(task["head_to_head"], task["providers"]["drex"], task["providers"]["openai"])
        summary["tasks"][task_name] = task
    return summary


def render_markdown(summary: dict) -> str:
    m = summary["meta"]
    out = [f"# Drex vs OpenAI benchmark: `{summary['name']}`", "",
           f"Run created {m['created_at']}; seed {m['seed']}; {m['n_requested']} cases requested per task; {m['workers']} parallel workers; "
           f"OpenAI mode `{m['openai_mode']}`. Models requested: " + ", ".join(f"{k} = `{v}`" for k, v in m["providers"].items()) + ".", ""]
    for task_name, task in summary["tasks"].items():
        out += [f"## {task['title']} (`{task_name}`)", "",
                f"Ground truth: {task['ground_truth_field']}. Classes: {', '.join(task['labels'])}. Cases: {task['n_cases']}.", "",
                "| Provider (model answering) | Valid / attempted | Accuracy (95% CI) | Macro-F1 | Errors | Median latency | p95 latency | Mean input tokens | Cost / 1,000 decisions |",
                "|---|---|---|---|---|---|---|---|---|"]
        for provider, s in task["providers"].items():
            lat = s["latency_seconds"]
            errors = ", ".join(f"{k}: {v}" for k, v in s["errors"].items()) or "none"
            cost = "not computed (no price supplied)" if s["cost_per_1000_usd"] is None else f"${s['cost_per_1000_usd']:.4f}"
            out.append(f"| {provider} (`{s['model']}`) | {s['valid']} / {s['attempted']} | {_pct(s['accuracy'])} ({_pct(s['accuracy_ci'][0])} to {_pct(s['accuracy_ci'][1])}) | "
                       f"{_fmt(s['macro_f1'])} | {errors} | {_fmt(lat['median'])} s | {_fmt(lat['p95'])} s | {_fmt(s['mean_input_tokens'], 0)} | {cost} |")
        for b in task["baselines"]:
            out.append(f"| baseline: {b['name']} | {task['n_cases']} / {task['n_cases']} | {_pct(b['accuracy'])} ({_pct(b['accuracy_ci'][0])} to {_pct(b['accuracy_ci'][1])}) | {_fmt(b['macro_f1'])} | n/a | n/a | n/a | n/a | $0 |")
        out.append("")
        h = task.get("head_to_head")
        if h:
            lo, hi = h["accuracy_diff_ci"]
            out += ["**Head to head** (cases where both returned a valid answer):", "",
                    f"- {h['paired_cases']} paired cases: both correct {h['both_correct']}, only Drex correct {h['only_drex_correct']}, "
                    f"only OpenAI correct {h['only_openai_correct']}, neither {h['neither_correct']}.",
                    f"- Accuracy difference (Drex minus OpenAI): {100 * h['accuracy_diff_drex_minus_openai']:+.1f} points, 95% bootstrap CI {100 * lo:+.1f} to {100 * hi:+.1f}; exact McNemar p = {h['mcnemar_p']:.4f}.",
                    f"- The two providers gave the same answer on {_pct(h['agreement'])} of paired cases.", ""]
        if task.get("verdict"):
            out += ["**Measured outcome:**", ""] + [f"- {line}" for line in task["verdict"]] + [""]
        drex = task["providers"].get("drex")
        if drex and drex.get("calibration"):
            c = drex["calibration"]
            out += [f"Drex scores as probabilities: mean confidence {_fmt(c['mean_confidence'])}, ECE {_fmt(c['ece'])}, Brier {_fmt(c['brier'])} "
                    "(lower is better for the last two; Drex scores are decision scores, not guaranteed probabilities).", ""]
        for provider, s in task["providers"].items():
            if s["per_class"]:
                out += [f"Per-class recall, {provider}: " + ", ".join(f"{k} {_pct(v['recall'])} (n={v['support']})" for k, v in s["per_class"].items()), ""]
    out += ["## Limitations", "", LIMITATIONS, ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default="benchmark")
    parser.add_argument("--db", type=Path, default=None, help="ASRS DuckDB (needed for the keyword baseline)")
    parser.add_argument("--drex-input-price", type=float, help="USD per million Drex input tokens")
    parser.add_argument("--openai-input-price", type=float, help="USD per million OpenAI input tokens")
    parser.add_argument("--openai-output-price", type=float, help="USD per million OpenAI output tokens")
    args = parser.parse_args(argv)
    if not (RESULTS_ROOT / args.name / "run_meta.json").exists():
        print(f"No results found for --name {args.name}", file=sys.stderr)
        return 2
    prices = {"drex": (args.drex_input_price, 0.0 if args.drex_input_price is not None else None),  # Drex bills input tokens only
              "openai": (args.openai_input_price, args.openai_output_price)}
    summary = build_summary(args.name, args.db, prices)
    directory = RESULTS_ROOT / args.name
    (directory / "summary.json").write_text(json.dumps(_clean(summary), indent=2, ensure_ascii=False), encoding="utf-8")
    markdown = render_markdown(summary)
    (directory / "report.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
