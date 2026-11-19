# Date formats

`parse_date` tries these formats in order and returns the first that reads the whole text.

| Format | Example | Notes |
|---|---|---|
| ISO date | `2026-01-28` | |
| basic ISO date | `20260128` | |
| ISO date and time | `2026-01-28T09:30:00` | the time is dropped |
| written date | `28 January 2026`, `January 28, 2026`, `28 Jan. 2026` | month names in English, full or the first three letters |
| ordinal date | `2026-028` | the 28th day of 2026 |
| ISO week date | `2026-W05-3` | Wednesday of ISO week 5 |
| numeric date | `28/01/2026`, `01/28/2026` | see below |

## Numeric dates

A numeric date with a number over 12 reads one way only. Otherwise both numbers could be the day:
`03/04/2026` raises `AmbiguousDate` unless you pass `day_first=True` (3 April) or the two numbers are equal.
Two-digit years are in the 2000s.

## ISO week dates

An ISO week starts on a Monday, and week 1 is the week with the year's first Thursday in it, so
`2026-W01-1` is Monday 29 December 2025. The weekday is 1 for Monday to 7 for Sunday. The arithmetic is
`iso_week(year, week, weekday)` in `calendar_utils.py`.

## Time zones

`parse_date` returns a `date` and drops any time and zone. `timezones.split_zone` reads the zone at the end of a
date-time (`Z`, `+05:30`, `-0800`, or one of eight names) for callers that need it.
