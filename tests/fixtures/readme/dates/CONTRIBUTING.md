# Contributing

Thank you for helping.

## Set up

```
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
```

## Before a pull request

- `pytest -q` passes.
- A new format is in `FORMATS` (in the order it should be tried), in `docs/dates.md`, and has a test in
  `tests/test_parser.py`.
- A format `strptime` cannot read on its own goes in `SPECIAL`, with its own branch in `parse_date`.
- Calendar arithmetic goes in `calendar_utils.py`, not in the parser.
- The changelog has a line under "Unreleased".

## Style

Black at 120 columns. Errors say what text could not be read and why.
