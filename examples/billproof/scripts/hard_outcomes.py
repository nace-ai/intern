#!/usr/bin/env python3
"""Score recorded Hard answers. Does not call a model."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.bench.hard.outcomes import write_outcomes  # noqa: E402


def main() -> None:
    payload = write_outcomes("test")
    for lane, body in payload["lanes"].items():
        line = body["line_verdict"]
        bill = body["bill_verdict"]
        strict = body["strict_case"]
        caught = body["overcharge_caught_usd"]
        wrong = body["overcharge_wrongly_disputed_usd"]
        print(
            f"{lane} cases={body['n_cases']} "
            f"line={line.get('k')}/{line.get('n')} "
            f"bill={bill.get('k')}/{bill.get('n')} "
            f"strict={strict.get('k')}/{strict.get('n')} "
            f"caught_usd={caught.get('value')} wrong_usd={wrong.get('value')}",
            flush=True,
        )


if __name__ == "__main__":
    main()
