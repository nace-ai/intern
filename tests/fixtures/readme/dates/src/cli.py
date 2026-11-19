"""`dates` on the command line: `dates find FILE`, `dates normalise FILE`, `dates parse TEXT`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .api import find_dates, normalise
from .errors import DateError
from .parser import parse_date


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dates")
    parser.add_argument("--day-first", action="store_true", help="read 03/04 as 3 April")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("find", help="list the dates in a file").add_argument("file", type=Path)
    sub.add_parser("normalise", help="rewrite a file's dates as ISO dates").add_argument("file", type=Path)
    sub.add_parser("parse", help="read one date").add_argument("text")
    args = parser.parse_args(argv)

    if args.command == "parse":
        try:
            print(parse_date(args.text, day_first=args.day_first).isoformat())
        except DateError as e:
            print(e, file=sys.stderr)
            return 1
        return 0
    text = args.file.read_text()
    if args.command == "find":
        for item in find_dates(text, day_first=args.day_first):
            print(f"{item.start}\t{item.text}\t{item.value.isoformat()}")
    else:
        sys.stdout.write(normalise(text, day_first=args.day_first))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
