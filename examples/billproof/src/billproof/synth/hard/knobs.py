"""Global difficulty knobs. Never retuned from which model got a case right."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Knobs:
    """One setting for a whole split. Line mix is the same shape on every case."""

    seed: int = 20261114
    dev_n: int = 60
    test_n: int = 300
    # Before fillers: NCCI col1 + ncci_col2, 3 dup triplets, 1 allowed pair,
    # 3 MUE triplets, one copay line, one other-provider distractor.
    # Singletons then fill to lines_min..lines_max.
    lines_min: int = 30
    lines_max: int = 32
    dup_triplets: int = 3
    allowed_pairs: int = 1
    mue_triplets: int = 3
    ncci_col2: int = 4
    near_threshold_share: float = 0.70
    fee_over_share: float = 0.48
    noise_rate: float = 0.06
    pr_wrong_share: float = 0.42
    eob_mismatch_share: float = 0.30
    secondary_share: float = 0.50
    oon_share: float = 0.40
    nsa_given_oon_share: float = 0.50
    denial_share: float = 0.12
    patient_liable_share: float = 0.10
    partial_pay_share: float = 0.55
    bill_wrong_share: float = 0.50
    tight_oop_share: float = 0.25
    contradictory_remark_rate: float = 0.50

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Knobs":
        known = {k: raw[k] for k in cls.__dataclass_fields__ if k in raw}
        return cls(**known)


DEFAULT_KNOBS = Knobs()
