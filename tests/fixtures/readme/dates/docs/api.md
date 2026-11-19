# API

```python
from dates import find_dates, normalise, parse_date, months_between
```

## `parse_date(text, *, day_first=False) -> date`

The date `text` names. Raises `UnparsedDate` when no format reads it and `AmbiguousDate` when a numeric date
could be read day-first or month-first and `day_first` was not given. See [date formats](dates.md).

## `parse_many(texts, *, day_first=False) -> list[date | None]`

Each text's date, `None` where one names no date. Ambiguous dates still raise.

## `find_dates(text, *, day_first=False) -> list[Found]`

Every date written in `text`, in order. A `Found` has the `text` matched, its `start` and `end` in the string, and
its `value`. Candidates that look like dates but are not (`2026-13-01`) are skipped.

## `normalise(text, *, day_first=False) -> str`

`text` with every date found rewritten as an ISO date; the rest is left as it was.

## `months_between(start, end) -> int`

Whole months from `start` to `end`: 31 January to 28 February is 0 months, to 1 March is 1.

## Errors

All errors are `DateError`, a `ValueError`. `UnparsedDate` has the `text` and the `reason`; `AmbiguousDate` the
`text`.

## Command line

```
dates parse "28 Jan 2026"        # 2026-01-28
dates find notes.txt             # offset, text and ISO date of each date found
dates --day-first normalise notes.txt
```
