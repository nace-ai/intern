"""Parse dates written in text: ISO dates, day-first and month-first dates, and month names."""

from __future__ import annotations

import re
from datetime import date, datetime

from .errors import AmbiguousDate, UnparsedDate
from .formats import FORMATS, MONTH_NAMES, SPECIAL

_NUMERIC = re.compile(r"^(\d{1,4})[-/.](\d{1,2})[-/.](\d{1,4})$")
_NAMED = re.compile(r"^(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})$")


def parse_date(text: str, *, day_first: bool = False) -> date:
    """The date `text` names, trying each of FORMATS in turn.

    A format in SPECIAL needs more than `strptime` and is skipped here; each has a branch of its own below.
    """
    text = text.strip()
    if not text:
        raise UnparsedDate(text, "empty")
    for fmt in FORMATS:
        if fmt in SPECIAL:
            continue
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    if named := _NAMED.match(text):
        return _named(*named.groups())
    if numeric := _NUMERIC.match(text):
        return _numeric(*numeric.groups(), day_first=day_first)
    raise UnparsedDate(text, "no format matches")


def _named(day: str, month: str, year: str) -> date:
    number = MONTH_NAMES.get(month.lower()[:3])
    if number is None:
        raise UnparsedDate(f"{day} {month} {year}", f"unknown month {month!r}")
    return date(int(year), number, int(day))


def _numeric(first: str, second: str, third: str, *, day_first: bool) -> date:
    if len(first) == 4:
        return date(int(first), int(second), int(third))
    a, b, year = int(first), int(second), int(third)
    if year < 100:
        year += 2000
    if a > 12 and b <= 12:
        return date(year, b, a)
    if b > 12 and a <= 12:
        return date(year, a, b)
    if a != b and not day_first:
        raise AmbiguousDate(f"{first}/{second}/{third}")
    return date(year, b, a) if day_first else date(year, a, b)


def parse_many(texts: list[str], *, day_first: bool = False) -> list[date | None]:
    """Each text's date, or None where it names none."""
    out: list[date | None] = []
    for text in texts:
        try:
            out.append(parse_date(text, day_first=day_first))
        except UnparsedDate:
            out.append(None)
    return out
