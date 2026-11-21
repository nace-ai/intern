#!/usr/bin/env bash
# Run the triage loop as an Intern session (Nace's agent harness).
#   System 1 (watches every turn, guards tool calls): Drex from the Nace console (NACE_API_KEY)
#   System 2 (does the work): a DeepInfra chat model (DEEPINFRA_API_KEY), our partner tool
# Usage: ./run_intern.sh [case_id ...]   (default: three demo cases)
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"
[ -f .env ] && set -a && . ./.env && set +a
: "${NACE_API_KEY:?NACE_API_KEY is required (Intern's System 1 is Drex)}"
: "${DEEPINFRA_API_KEY:?DEEPINFRA_API_KEY is required (Intern's System 2 runs on DeepInfra)}"
MODEL="${INTERN_SYSTEM2:-deepinfra/Qwen/Qwen3.8-27B}"
CASES="${*:-case_007 case_041 case_049}"
rm -f results/intern_thresholds.json results/intern_feedback.json
mkdir -p results
intern -p --json --system2 "$MODEL" --system1 drex:drex-max-v1.1 --max-turns 40 --cwd "$PWD" --no-memory -- \
  "Triage these CI failures following AGENTS.md: $CASES" | tee results/intern_run.jsonl
