"""Per-family escalation cutoffs, chosen from the published dev Drex accuracies.

Rule, fixed before any family-hybrid call: a family whose dev question
accuracy is below 0.90 escalates every question (tau 1.0). Every other family
escalates only when Drex confidence is below 0.95, the cutoff already frozen
for the single-threshold hybrid. The map is not refit after GPT answers arrive.
"""
from __future__ import annotations

import json
from pathlib import Path

from billproof.config import DATA_DIR, RUNS_DIR

ALWAYS_BELOW = 0.90
ELSE_TAU = 0.95
FAMILIES = ("dup", "mue", "ncci", "fee", "eob", "pr", "bill_balance")


def choose_from_dev_metrics(metrics_dir: Path | None = None) -> dict:
    metrics_dir = metrics_dir or (DATA_DIR / "metrics")
    taus = {}
    source = {}
    for family in FAMILIES:
        path = metrics_dir / f"hard_dev_drex-v1_family_{family}.json"
        payload = json.loads(path.read_text())
        accuracy = float(payload["value"])
        tau = 1.0 if accuracy < ALWAYS_BELOW else ELSE_TAU
        taus[family] = tau
        source[family] = {"dev_accuracy": accuracy, "n": payload.get("n"), "k": payload.get("k"), "tau": tau}
    return {
        "rule": "tau 1.0 when hard_dev_drex-v1 family accuracy < 0.90, else 0.95",
        "always_below": ALWAYS_BELOW,
        "else_tau": ELSE_TAU,
        "source": "data/metrics/hard_dev_drex-v1_family_*.json",
        "taus": taus,
        "families": source,
        "frozen_before_family_hybrid_calls": True,
    }


def tau_path() -> Path:
    return RUNS_DIR / "hard_family_tau.json"


def write_tau_file() -> dict:
    payload = choose_from_dev_metrics()
    path = tau_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def load_taus() -> dict[str, float]:
    payload = json.loads(tau_path().read_text())
    return {family: float(tau) for family, tau in payload["taus"].items()}


def escalates(family: str, confidence: float | None, taus: dict[str, float]) -> bool:
    """True when this question goes to a fresh GPT call."""
    tau = float(taus[family])
    if tau >= 1.0 or confidence is None:
        return True
    return float(confidence) < tau
