"""Scoring helpers (standard library only). Every function is pure so it can be unit-tested."""

from __future__ import annotations

import math
import random
from collections import Counter
from typing import Sequence


def accuracy(truth: Sequence[str], predicted: Sequence[str]) -> float:
    return sum(t == p for t, p in zip(truth, predicted)) / len(truth) if truth else float("nan")


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def per_class(truth: Sequence[str], predicted: Sequence[str], labels: Sequence[str]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for label in labels:
        tp = sum(t == label and p == label for t, p in zip(truth, predicted))
        fp = sum(t != label and p == label for t, p in zip(truth, predicted))
        fn = sum(t == label and p != label for t, p in zip(truth, predicted))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        out[label] = {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}
    return out


def macro_f1(truth: Sequence[str], predicted: Sequence[str], labels: Sequence[str]) -> float:
    scores = per_class(truth, predicted, labels)
    return sum(v["f1"] for v in scores.values()) / len(labels) if labels else float("nan")


def confusion(truth: Sequence[str], predicted: Sequence[str], labels: Sequence[str]) -> dict[str, dict[str, int]]:
    counts = Counter(zip(truth, predicted))
    return {t: {p: counts[(t, p)] for p in labels} for t in labels}


def mcnemar_exact(only_a_correct: int, only_b_correct: int) -> float:
    """Two-sided exact McNemar p-value from the discordant pair counts."""
    n = only_a_correct + only_b_correct
    if n == 0:
        return 1.0
    k = min(only_a_correct, only_b_correct)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def paired_bootstrap_diff(correct_a: Sequence[bool], correct_b: Sequence[bool], iterations: int = 2000, seed: int = 0) -> tuple[float, float, float]:
    """(mean difference a-b in accuracy, 95% CI low, high), resampling cases with replacement."""
    n = len(correct_a)
    if n == 0 or n != len(correct_b):
        return (float("nan"),) * 3
    diffs = [int(a) - int(b) for a, b in zip(correct_a, correct_b)]
    rng = random.Random(seed)
    samples = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(iterations))
    return (sum(diffs) / n, samples[int(0.025 * iterations)], samples[int(0.975 * iterations) - 1])


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile, q in (0, 100]."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q / 100 * len(ordered)) - 1)]


def latency_summary(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {"mean": float("nan"), "median": float("nan"), "p95": float("nan")}
    return {"mean": sum(values) / len(values), "median": percentile(values, 50), "p95": percentile(values, 95)}


def brier(probabilities: Sequence[dict[str, float]], truth: Sequence[str], labels: Sequence[str]) -> float:
    """Multi-class Brier score (lower is better)."""
    if not truth:
        return float("nan")
    return sum(sum((p.get(l, 0.0) - (1.0 if l == t else 0.0)) ** 2 for l in labels) for p, t in zip(probabilities, truth)) / len(truth)


def expected_calibration_error(confidence: Sequence[float], correct: Sequence[bool], bins: int = 10) -> float:
    """ECE: bin-weighted gap between mean confidence and accuracy."""
    n = len(confidence)
    if n == 0:
        return float("nan")
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        members = [i for i, c in enumerate(confidence) if (lo <= c < hi) or (b == bins - 1 and c == 1.0)]
        if members:
            gap = abs(sum(confidence[i] for i in members) / len(members) - sum(correct[i] for i in members) / len(members))
            total += len(members) / n * gap
    return total


def cost_per_1000(input_tokens: Sequence[int | None], output_tokens: Sequence[int | None],
                  input_price_per_million: float | None, output_price_per_million: float | None = 0.0) -> float | None:
    """Mean cost of 1,000 decisions from recorded token usage; None when a price or usage is missing."""
    pairs = [(i, o or 0) for i, o in zip(input_tokens, output_tokens) if i is not None]
    if not pairs or input_price_per_million is None or output_price_per_million is None:
        return None
    mean_cost = sum(i * input_price_per_million + o * output_price_per_million for i, o in pairs) / len(pairs) / 1_000_000
    return mean_cost * 1000
