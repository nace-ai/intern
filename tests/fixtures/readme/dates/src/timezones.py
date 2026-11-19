"""Offsets written after a time: `Z`, `+05:30`, `-0800`, and the few zone names the API accepts."""

from __future__ import annotations

import re
from datetime import timedelta, timezone

from .errors import UnparsedDate

_OFFSET = re.compile(r"^([+-])(\d{2}):?(\d{2})$")

NAMED = {
    "UTC": timezone.utc,
    "GMT": timezone.utc,
    "CET": timezone(timedelta(hours=1), "CET"),
    "EET": timezone(timedelta(hours=2), "EET"),
    "IST": timezone(timedelta(hours=5, minutes=30), "IST"),
    "JST": timezone(timedelta(hours=9), "JST"),
    "EST": timezone(timedelta(hours=-5), "EST"),
    "PST": timezone(timedelta(hours=-8), "PST"),
}


def parse_offset(text: str) -> timezone:
    """The zone an offset or a name stands for."""
    text = text.strip()
    if text in ("Z", "z"):
        return timezone.utc
    if text.upper() in NAMED:
        return NAMED[text.upper()]
    match = _OFFSET.match(text)
    if match is None:
        raise UnparsedDate(text, "not an offset")
    sign, hours, minutes = match.groups()
    delta = timedelta(hours=int(hours), minutes=int(minutes))
    if delta >= timedelta(hours=24):
        raise UnparsedDate(text, "offset of a day or more")
    return timezone(-delta if sign == "-" else delta)


def format_offset(zone: timezone) -> str:
    """`+05:30`, or `Z` for UTC."""
    delta = zone.utcoffset(None)
    if not delta:
        return "Z"
    sign = "-" if delta < timedelta(0) else "+"
    minutes = abs(int(delta.total_seconds())) // 60
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def split_zone(text: str) -> tuple[str, timezone | None]:
    """A date-time's text and the zone at its end, if it has one."""
    for name, zone in NAMED.items():
        if text.endswith(" " + name):
            return text[: -len(name) - 1], zone
    if text.endswith(("Z", "z")):
        return text[:-1], timezone.utc
    match = re.search(r"[+-]\d{2}:?\d{2}$", text)
    if match:
        return text[: match.start()], parse_offset(match.group())
    return text, None
