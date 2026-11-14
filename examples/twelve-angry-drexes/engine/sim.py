"""Seeded fake probabilities for the simulated fallback. Ported from mocks/tad-sim.js.

Nothing here is a model output. Real runs never read the `Hints` class: it only fills the
`_sim` field of questions, which the Drex client uses when it has no API key (or --simulate).
"""
from __future__ import annotations

import math
from typing import Callable

FRAC = [0.35, 0.85, 0.6, 1.0]  # how much of the case weight each phase has revealed


def fnv(s: str) -> int:
    """32-bit FNV-1a hash of a string (same as hash() in tad-sim.js)."""
    h = 2166136261
    for ch in s:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h


def rng(seed: int) -> Callable[[], float]:
    """mulberry32 generator returning floats in [0, 1), identical to rng() in tad-sim.js."""
    state = seed & 0xFFFFFFFF

    def nxt() -> float:
        nonlocal state
        state = (state + 0x6D2B79F5) & 0xFFFFFFFF
        t = ((state ^ (state >> 15)) * (1 | state)) & 0xFFFFFFFF
        t = ((t + (((t ^ (t >> 7)) * (61 | t)) & 0xFFFFFFFF)) ^ t) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296

    return nxt


def sig(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def clamp(x: float, a: float, b: float) -> float:
    return max(a, min(b, x))


def dist(n: int, mu: float, r: Callable[[], float], spread: float) -> list[float]:
    """Discrete bell around `mu` on 0..n-1, normalized."""
    w = [math.exp(-((i - mu) ** 2) / (2 * spread * spread)) + r() * 0.03 for i in range(n)]
    s = sum(w)
    return [v / s for v in w]


def pick(n: int, top: float, r: Callable[[], float], win: int | None = None) -> list[float]:
    """Choice distribution where option `win` (random when None) gets probability `top`."""
    rest = [0.2 + r() for _ in range(n - 1)]
    s = sum(rest)
    spread = [v / s * (1 - top) for v in rest]
    win = int(r() * n) if win is None else win
    spread.insert(win, top)
    return spread


class Hints:
    """Per-decision fake probabilities for one case and one set of facts, deterministic in both."""

    DOC_TYPES = ["Lease or rental record", "Notice letter", "Court order or ruling", "Pay record", "Something else"]
    ROLES = ["Pro se", "Attorney", "Paralegal"]
    JURS = ["CA", "FL", "NY", "TX", "US"]

    def __init__(self, case: dict, facts: list[dict], jurors: list[dict]):
        self.case, self.facts, self.jurors = case, facts, jurors
        self.tiers = len(case["tiers"])
        r = rng(fnv(case["id"] + "|" + "".join(str(int(f["on"])) for f in facts)))
        self.total = case["base"] + sum(f["w"][1 if f["on"] else 0] for f in facts)
        self.noise = [[(r() - 0.5) * 0.5 for _ in FRAC] for _ in jurors]
        self.tails = [[r() for _ in range(8)] for _ in FRAC]  # spare draws per phase
        self.r = r

    def intake(self) -> dict[str, list[float]]:
        c, r = self.case, self.r
        want = 1 if c["upload"]["kind"] == "image" else 2 if c["id"] == "ca-wages" else 0
        return {
            "document_type": pick(5, 0.86, r, want),
            "asker_role": pick(3, 0.9, r, self.ROLES.index(c["asker"]["kind"])),
            "jurisdiction": pick(5, 0.94, r, self.JURS.index(c["jurisdiction"])),
        }

    def ruling(self) -> tuple[list[float], float]:
        """Who won in an uploaded ruling (3 options) and p(it is on point). Own seed, so no other draw moves."""
        r = rng(fnv(self.case["id"] + "|ruling"))
        return pick(3, 0.84, r, self.case["ruling"]["won"]), 0.74 + r() * 0.06

    def practice(self, group: int, index: int, sizes: list[int]) -> dict[str, list[float]]:
        """Role pick: the group question, then one area question per group (`sizes` options each). Own seed."""
        r = rng(fnv(self.case["id"] + "|practice"))
        out = {"practice_group": pick(len(sizes), 0.9, r, group)}
        for g, n in enumerate(sizes):
            out[f"area_{g}"] = pick(n, 0.86, r, index if g == group else None)
        return out

    def extra_statute(self, key: str) -> float:
        """A candidate the role-scoped search added: usually it does not apply. Own seed."""
        return 0.06 + rng(fnv(self.case["id"] + "|extra|" + key))() * 0.22

    def statute(self, k: int) -> float:
        return clamp(self.case["statutes"][k]["applies"] + (self.r() - 0.5) * 0.04, 0.01, 0.99)

    def next_argument(self) -> list[float]:
        return pick(3, 0.5 + self.r() * 0.3, self.r)

    def objection(self) -> float:
        sustained = "sustained" in self.case["speeches"][1][1][1]
        return 0.81 if sustained else 0.17

    def strength(self, k: int) -> list[float]:
        mu = clamp(2 + (-self.total if k == 2 else self.total) * 0.7, 0.2, 3.8)
        return dist(5, mu, self.r, 0.8)

    def juror(self, ji: int, k: int) -> tuple[float, list[float]]:
        """(probability the defendant is liable, award-level distribution) for juror `ji` after phase `k`."""
        c, j = self.case, self.jurors[ji]
        weight = sum(f["w"][1 if f["on"] else 0] * j[f["tag"]] for f in self.facts)
        p = clamp(sig(c["base"] * FRAC[k] + weight * FRAC[k] + j["lean"] + self.noise[ji][k]), 0.02, 0.98)
        n = self.tiers
        mu = clamp(p * (n - 1) * (0.75 + 0.35 * sig(self.total)), 0, n - 1)
        return p, dist(n, mu, self.r, 0.75)
