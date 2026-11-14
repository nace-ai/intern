#!/usr/bin/env bash
# Run Intern (Nace's coding agent) on this project, headless, and keep a log of every run.
#   System 2 (does the work): an LLM on DeepInfra   -> $DEEPINFRA_API_KEY
#   System 1 (watches every turn, guards tool calls): Drex from the Nace console -> $NACE_API_KEY
# Usage: scripts/intern.sh "task description" [extra intern flags]
# Logs: intern_logs/<time>-<slug>.jsonl (one JSON event per line), plus the final reply in .txt
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a

task="$1"; shift || true
system2="${INTERN_SYSTEM2:-deepinfra/deepseek-ai/DeepSeek-V4-Pro}"
system1="${INTERN_SYSTEM1:-drex-max-v1.1}"
slug=$(printf '%s' "$task" | tr 'A-Z' 'a-z' | tr -cs 'a-z0-9' '-' | cut -c1-40 | sed 's/-$//')
mkdir -p intern_logs
log="intern_logs/$(date +%Y%m%d-%H%M%S)-${slug}"

echo "intern: system2=$system2 system1=$system1 -> $log.jsonl" >&2
intern -p --json --system2 "$system2" --system1 "$system1" "$@" -- "$task" \
  | tee "$log.jsonl" | python3 scripts/intern_log.py | tee "$log.txt"
