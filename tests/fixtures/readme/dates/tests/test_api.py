from datetime import date

from dates.api import find_dates, months_between, normalise


def test_find_dates_in_a_sentence():
    found = find_dates("Signed 28 January 2026, due 2026-03-01.")
    assert [f.value for f in found] == [date(2026, 1, 28), date(2026, 3, 1)]
    assert found[0].text == "28 January 2026"


def test_find_skips_what_is_not_a_date():
    assert find_dates("Room 12/40, call 2026-13-01") == []


def test_normalise():
    assert normalise("from 03/04/2026 to 4 May 2026", day_first=True) == "from 2026-04-03 to 2026-05-04"


def test_months_between():
    assert months_between(date(2026, 1, 31), date(2026, 2, 28)) == 0
    assert months_between(date(2026, 1, 31), date(2026, 3, 1)) == 1
    assert months_between(date(2025, 11, 15), date(2026, 2, 15)) == 3
