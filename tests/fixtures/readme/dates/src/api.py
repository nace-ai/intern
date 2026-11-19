"""The library's public functions: find dates in text, normalise them, and say how far apart they are."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .calendar_utils import add_months
from .errors import DateError
from .parser import parse_date

_CANDIDATE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|\d{4}-W\d{2}-\d|\d{1,2}[/.]\d{1,2}[/.]\d{2,4}|\d{1,2} [A-Z][a-z]+\.? \d{4}|[A-Z][a-z]+ \d{1,2}, \d{4})\b"
)


@dataclass(frozen=True)
class Found:
    text: str
    start: int
    end: int
    value: date


def find_dates(text: str, *, day_first: bool = False) -> list[Found]:
    """Every date written in `text`, in order, with where it is."""
    found = []
    for match in _CANDIDATE.finditer(text):
        try:
            value = parse_date(match.group(), day_first=day_first)
        except DateError:
            continue
        found.append(Found(match.group(), match.start(), match.end(), value))
    return found


def normalise(text: str, *, day_first: bool = False) -> str:
    """`text` with each date it names rewritten as an ISO date."""
    out, last = [], 0
    for item in find_dates(text, day_first=day_first):
        out += [text[last : item.start], item.value.isoformat()]
        last = item.end
    return "".join(out) + text[last:]


def months_between(start: date, end: date) -> int:
    """Whole months from `start` to `end`."""
    months = (end.year - start.year) * 12 + end.month - start.month
    if add_months(start, months) > end:
        months -= 1
    return months
