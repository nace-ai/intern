#!/usr/bin/env python3
"""Write data/metrics/hard_*.json from hard-bench JSONL."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from billproof.bench.hard.metrics import report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "test"), required=True)
    args = parser.parse_args()
    payload = report(args.split)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
