#!/usr/bin/env bash
# Full pipeline. ./run_all.sh --mock  (no keys)  |  ./run_all.sh  (real where keys exist)
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] && . .venv/bin/activate
python data/generate.py
python ndi_extract.py "$@"
python drex_decide.py "$@"
python learn.py "$@"
python baseline_llm.py "$@"
python compare.py "$@"
