"""Create the swarm of voter agents (Service 1: the individualizer)."""

from __future__ import annotations

import numpy as np

from .models import Agent

GRID_SIZE = 40
GENDERS: tuple[str, ...] = ("male", "female")

WEALTH_MEAN = 50_000.0
WEALTH_SD = 20_000.0
AGE_MIN, AGE_MAX = 18, 90
HEALTH_MIN, HEALTH_MAX = 0.3, 1.0


def _zscore(values: np.ndarray) -> np.ndarray:
    """Standardize a sample to zero mean and unit variance."""
    std = values.std()
    if std == 0:
        return np.zeros_like(values)
    return (values - values.mean()) / std


def make_agents(n: int, seed: int) -> list[Agent]:
    """Create `n` agents with a deterministic draw order for `seed`."""
    rng = np.random.default_rng(seed)

    wealth = np.clip(rng.normal(WEALTH_MEAN, WEALTH_SD, n), 0, None)
    age = rng.uniform(AGE_MIN, AGE_MAX, n)
    gender = rng.integers(0, len(GENDERS), n)
    health = rng.uniform(HEALTH_MIN, HEALTH_MAX, n)
    x = rng.uniform(0, GRID_SIZE, n)
    y = rng.uniform(0, GRID_SIZE, n)
    noise = rng.normal(0.0, 0.3, n)

    z_wealth = _zscore(wealth)
    z_age = _zscore(age)
    lean = np.tanh(0.8 * z_wealth + 0.3 * z_age + noise)

    return [
        Agent(
            id=f"a{i}",
            wealth=float(wealth[i]),
            age=float(age[i]),
            gender=GENDERS[int(gender[i])],
            health=float(health[i]),
            x=float(x[i]),
            y=float(y[i]),
            lean=float(lean[i]),
            history=[],
        )
        for i in range(n)
    ]
