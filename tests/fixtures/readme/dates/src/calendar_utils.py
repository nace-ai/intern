"""Calendar arithmetic the parser and the API share: ISO weeks, ordinal days, month lengths."""

from __future__ import annotations

from datetime import date, timedelta


def is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def month_length(year: int, month: int) -> int:
    if month == 2:
        return 29 if is_leap(year) else 28
    return 30 if month in (4, 6, 9, 11) else 31


def iso_week(year: int, week: int, weekday: int = 1) -> date:
    """The date of `weekday` (1 is Monday) in ISO week `week` of ISO year `year`.

    Week 1 is the week with the year's first Thursday in it, so it may start in December.
    """
    if not 1 <= weekday <= 7:
        raise ValueError(f"weekday {weekday} is not 1-7")
    if not 1 <= week <= weeks_in_year(year):
        raise ValueError(f"week {week} is not in ISO year {year}")
    fourth = date(year, 1, 4)
    monday = fourth - timedelta(days=fourth.isoweekday() - 1)
    return monday + timedelta(weeks=week - 1, days=weekday - 1)


def weeks_in_year(year: int) -> int:
    """52 or 53: a year has 53 ISO weeks when it starts on a Thursday, or on a Wednesday in a leap year."""
    first = date(year, 1, 1).isoweekday()
    return 53 if first == 4 or (first == 3 and is_leap(year)) else 52


def ordinal_day(year: int, day: int) -> date:
    """The `day`th day of `year`, 1 for January 1."""
    last = 366 if is_leap(year) else 365
    if not 1 <= day <= last:
        raise ValueError(f"day {day} is not in {year}")
    return date(year, 1, 1) + timedelta(days=day - 1)


def add_months(when: date, months: int) -> date:
    """`when` moved by `months`, its day kept where the month is long enough, else the month's last day."""
    index = when.month - 1 + months
    year, month = when.year + index // 12, index % 12 + 1
    return when.replace(year=year, month=month, day=min(when.day, month_length(year, month)))
