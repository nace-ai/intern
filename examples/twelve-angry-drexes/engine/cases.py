"""Load the three invented cases and the twelve juror personas from cases/cases.json."""
from __future__ import annotations

import json
import re
from functools import lru_cache

from . import config


@lru_cache(maxsize=1)
def _load() -> dict:
    with open(config.CASES_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def all_cases() -> list[dict]:
    return _load()["cases"]


def jurors() -> list[dict]:
    return _load()["jurors"]


def phases() -> list[str]:
    return _load()["phases"]


def get_case(case_id: str) -> dict:
    """Return the case with this id, or raise ValueError listing the known ids."""
    for c in all_cases():
        if c["id"] == case_id:
            return c
    raise ValueError(f"unknown case {case_id!r}; known: {', '.join(c['id'] for c in all_cases())}")


def match(text: str = "", filename: str = "") -> str:
    """Pick a case id from a Slack message or file name: an explicit id, else the upload name, else the case
    whose own text shares the most words with the message (at least 2), else the first case."""
    low = (text or "").lower()
    for c in all_cases():
        if c["id"] in low or c["upload"]["name"].lower() == (filename or "").lower():
            return c["id"]
    words = set(re.findall(r"[a-z]{4,}", low))

    def shared(c: dict) -> int:
        return len(words & set(re.findall(r"[a-z]{4,}", f"{c['short']} {c['claim']} {c['ask']} {c['title']}".lower())))

    best = max(all_cases(), key=shared)
    return best["id"] if shared(best) >= 2 else all_cases()[0]["id"]
