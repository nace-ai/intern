#!/usr/bin/env python3
"""Build 175 benchmark cases plus six stage demos and sealed labels; W4 renders documents separately."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from billproof.synth.generator import generate, refresh_demos

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=20261114)
    parser.add_argument('--demo-only', action='store_true', help='Refresh stage demos and their label rows only; rerender their PDFs afterwards')
    args = parser.parse_args()
    print(json.dumps(refresh_demos() if args.demo_only else generate(args.seed), indent=2))
