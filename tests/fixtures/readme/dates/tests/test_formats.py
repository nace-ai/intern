from dates.calendar_utils import iso_week, ordinal_day, weeks_in_year
from dates.formats import FORMATS, SPECIAL, describe


def test_special_formats_are_listed():
    assert SPECIAL <= set(FORMATS)


def test_describe():
    assert describe("%G-W%V-%u") == "ISO week date"
    assert describe("%d %B %Y") == "written date"


def test_iso_week():
    assert iso_week(2026, 5, 3).isoformat() == "2026-01-28"
    assert iso_week(2026, 1).isoformat() == "2025-12-29"


def test_weeks_in_year():
    assert weeks_in_year(2026) == 53
    assert weeks_in_year(2025) == 52


def test_ordinal_day():
    assert ordinal_day(2026, 28).isoformat() == "2026-01-28"
