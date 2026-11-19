"""The formats `parse_date` knows, in the order it tries them, and the names of the months."""

FORMATS = (
    "%Y-%m-%d",  # 2026-01-28
    "%Y%m%d",  # 20260128
    "%Y-%m-%dT%H:%M:%S",  # 2026-01-28T09:30:00
    "%d %B %Y",  # 28 January 2026
    "%B %d, %Y",  # January 28, 2026
    "%d %b %Y",  # 28 Jan 2026
    "%b %d, %Y",  # Jan 28, 2026
    "%Y-%j",  # 2026-028, the ordinal date
    "%G-W%V-%u",  # 2026-W05-3, the ISO week date
)

SPECIAL = frozenset({"%Y-%j", "%G-W%V-%u"})
"""Formats `strptime` cannot read on its own: an ordinal date and an ISO week date need the year first."""

MONTH_NAMES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def describe(fmt: str) -> str:
    """A format as the docs name it."""
    names = {
        "%Y-%m-%d": "ISO date",
        "%Y%m%d": "basic ISO date",
        "%Y-%m-%dT%H:%M:%S": "ISO date and time",
        "%Y-%j": "ordinal date",
        "%G-W%V-%u": "ISO week date",
    }
    return names.get(fmt, "written date")
