"""Confidence routing and non-negotiable human-review caps.

Bands resolve per (bench, drex_model): data/runs/bands.json may carry a
"scopes" map keyed "synthetic|drex-max-v1.1" / "imr|drex-max-finance" etc.
Without a scope the flat {keep, verify} (+ optional "families") applies to
every bench/model, exactly as before scoped tuning existed.

Band decisions compare `answer.confidence` — the API-reported scalar the
adapters record — to the thresholds. That scalar is NOT the chosen-class
probability that metrics.question_units uses for the standalone automation
cutoff (dev audit dc2c028); routing stays on the scalar by design.
"""

from __future__ import annotations

import json

from billproof.config import DATA_DIR, settings
from billproof.schema import Answer, Case, Question, Route, RuleFlag

BANDS = {"keep": 0.90, "verify": 0.60}
# Optional DEV-selected overrides; flags alone cannot establish correctness because
# candidate rules intentionally fire on legitimate exceptions (notably NCCI/PFS).
FAMILY_BANDS: dict[str, dict[str, float]] = {}
# (bench, drex_model) -> {"keep","verify","families": {fam: {"keep","verify"}}}
SCOPED_BANDS: dict[tuple[str, str], dict] = {}
_SCOPE_META: dict[tuple[str, str], dict] = {}  # full scope entries (provenance)
_FLAT_META: dict = {}  # flat bands.json fields minus "scopes" (provenance)
_bands_stamp = None


def _bands_path():
    return DATA_DIR / "runs" / "bands.json"


def _valid_bands(keep, verify) -> bool:
    return 0 <= verify <= keep <= 1


def _parse_families(raw) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for family, bands in (raw.items() if isinstance(raw, dict) else []):
        try:
            fk, fv = float(bands["keep"]), float(bands["verify"])
            if _valid_bands(fk, fv):
                out[family] = {"keep": fk, "verify": fv}
        except (TypeError, KeyError, ValueError):
            continue
    return out


def _load_bands() -> None:
    global _bands_stamp, _FLAT_META
    path = _bands_path()
    try:
        stamp = (path.stat().st_mtime_ns, path.stat().st_size)
        if stamp == _bands_stamp:
            return
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(loaded, dict):
        return
    # Flat defaults (backwards compatible: written by pre-scope tuners).
    try:
        keep, verify = float(loaded["keep"]), float(loaded["verify"])
    except (KeyError, TypeError, ValueError):
        keep = verify = None
    if keep is not None and _valid_bands(keep, verify):
        BANDS.update(keep=keep, verify=verify)
        FAMILY_BANDS.clear()
        FAMILY_BANDS.update(_parse_families(loaded.get("families", {})))
    # Scoped overrides; independent of whether the flat block parsed.
    SCOPED_BANDS.clear()
    _SCOPE_META.clear()
    for key, scope in (loaded.get("scopes") or {}).items():
        if not isinstance(scope, dict) or "|" not in str(key):
            continue
        bench, _, model = str(key).partition("|")
        try:
            sk, sv = float(scope["keep"]), float(scope["verify"])
        except (KeyError, TypeError, ValueError):
            continue
        if not _valid_bands(sk, sv):
            continue
        entry = {"keep": sk, "verify": sv, "families": _parse_families(scope.get("families"))}
        SCOPED_BANDS[(bench, model)] = entry
        _SCOPE_META[(bench, model)] = scope
    _FLAT_META = {k: v for k, v in loaded.items() if k != "scopes"}
    _bands_stamp = stamp


def reload_bands() -> None:
    """Force re-read after this process rewrote bands.json."""
    global _bands_stamp
    _bands_stamp = None
    _load_bands()


_load_bands()


def _model(model: str | None) -> str:
    if model:
        return model
    try:
        return settings().drex_model
    except Exception:
        return "unknown"


def _bench_of(case) -> str:
    # Synthetic lanes pass a schema.Case; IMR items are plain dicts.
    return "synthetic" if isinstance(case, Case) else "imr"


def bands_for(bench: str, family: str | None = None, model: str | None = None) -> dict:
    """Effective {keep, verify} for (bench, drex model[, question family])."""
    scope = SCOPED_BANDS.get((bench, _model(model)))
    if scope is not None:
        if family and family in scope["families"]:
            return scope["families"][family]
        return scope
    if family and family in FAMILY_BANDS:
        return FAMILY_BANDS[family]
    return BANDS


def active_bands(bench: str, model: str | None = None) -> dict:
    """Bands in effect for (bench, model) right now + provenance; stored in run meta."""
    _load_bands()
    m = _model(model)
    scope = SCOPED_BANDS.get((bench, m))
    out: dict = {"bench": bench, "drex_model": m,
                 "confidence_basis": "api_scalar_not_chosen_class_probability"}
    _PROV_KEYS = ("source_run", "frontier_source_run", "tuned_at", "n_cases",
                  "n_questions", "selection", "selection_basis", "margin",
                  "accuracy", "frontier_accuracy", "cost_basis", "bench", "drex_model")
    if scope is not None:
        out.update({"keep": scope["keep"], "verify": scope["verify"], "scope": f"{bench}|{m}"})
        if scope["families"]:
            out["families"] = scope["families"]
        meta = _SCOPE_META.get((bench, m)) or {}
        out["provenance"] = {k: meta[k] for k in _PROV_KEYS if k in meta} or {"file": "bands.json scopes"}
    elif _FLAT_META:
        out.update({"keep": BANDS["keep"], "verify": BANDS["verify"], "scope": "global"})
        out["provenance"] = {k: _FLAT_META[k] for k in _PROV_KEYS if k in _FLAT_META}
        if FAMILY_BANDS:
            out["families"] = dict(FAMILY_BANDS)
    else:
        out.update({"keep": BANDS["keep"], "verify": BANDS["verify"], "scope": "default"})
    return out


def decide_band(q: Question, a: Answer, flags: list[RuleFlag], keep: float, verify: float) -> str:
    """Band for a Drex answer under explicit thresholds: keep|verify|human|hard_cap.

    `a.confidence` is the API scalar (routing semantics), not a recomputed
    chosen-class probability. `a.raw["flip"]` demotes one step when present.
    """
    name = q.name.lower()
    is_balance_billing = name == "nsa" or "balance_billing" in name
    fired_nsa = any(
        (flag.get("fired", False) and str(flag.get("check", "")).upper().endswith("NO_SURPRISES_ACT"))
        if isinstance(flag, dict)
        else (flag.fired and str(flag.check).upper().endswith("NO_SURPRISES_ACT"))
        for flag in flags
    )
    if is_balance_billing and (a.choice.lower() == "yes" or fired_nsa):
        return "hard_cap"
    if name == "received" and a.choice.lower() == "no":
        return "hard_cap"

    confidence = max(0.0, min(1.0, float(a.confidence)))
    if confidence >= keep:
        band = "keep"
    elif confidence >= verify:
        band = "verify"
    else:
        band = "human"

    if a.raw.get("flip"):
        band = {"keep": "verify", "verify": "human", "human": "human"}[band]
    return band


def route(q: Question, a: Answer, case: Case, flags: list[RuleFlag]) -> Route:
    """Route a Drex answer to keep, verification, or a human reviewer."""
    _load_bands()
    bands = bands_for(_bench_of(case), q.family)
    band = decide_band(q, a, flags, bands["keep"], bands["verify"])
    return {"keep": "drex_kept", "verify": "frontier_verified",
            "human": "human", "hard_cap": "hard_cap"}[band]
