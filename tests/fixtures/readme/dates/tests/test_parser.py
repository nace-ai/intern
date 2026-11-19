from datetime import date

import pytest
from dates.errors import AmbiguousDate, UnparsedDate
from dates.parser import parse_date, parse_many


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-01-28", date(2026, 1, 28)),
        ("20260128", date(2026, 1, 28)),
        ("28 January 2026", date(2026, 1, 28)),
        ("January 28, 2026", date(2026, 1, 28)),
        ("28 Jan. 2026", date(2026, 1, 28)),
        ("28/01/2026", date(2026, 1, 28)),
        ("01/28/2026", date(2026, 1, 28)),
    ],
)
def test_formats(text, expected):
    assert parse_date(text) == expected


def test_iso_week():
    assert parse_date("2026-W05-3") == date(2026, 1, 28)


def test_iso_week_in_december():
    assert parse_date("2026-W01-1") == date(2025, 12, 29)


def test_day_first():
    assert parse_date("03/04/2026", day_first=True) == date(2026, 4, 3)


def test_ambiguous():
    with pytest.raises(AmbiguousDate):
        parse_date("03/04/2026")


def test_empty():
    with pytest.raises(UnparsedDate):
        parse_date("  ")


def test_many():
    assert parse_many(["2026-01-28", "soon"]) == [date(2026, 1, 28), None]
