# dates-in-text

Find the dates written in text, and read them as dates.

```python
>>> from dates import find_dates, parse_date
>>> parse_date("28 Jan. 2026")
datetime.date(2026, 1, 28)
>>> [f.value.isoformat() for f in find_dates("Signed 28 January 2026, due 2026-03-01.")]
['2026-01-28', '2026-03-01']
```

It reads ISO dates, written dates in English, numeric dates (day-first or month-first, and it says when it cannot
tell), ordinal dates and ISO week dates. No dependencies; Python 3.10 or later.

## Install

```
pip install dates-in-text
```

## Documentation

- [API](docs/api.md)
- [Date formats](docs/dates.md)

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). Run the tests with `pytest -q`.

## Licence

MIT.
