#!/usr/bin/env bash
# Browse intern sessions: enter opens a run from the agents list, esc goes back; a running one is followed live.
#   ./watch-intern.sh                    every session in ~/.intern/sessions plus any running intern's --session
#   ./watch-intern.sh FILE.jsonl|DIR     also those, starting on FILE
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec uv run --quiet --project "$here" python "$here/tools/watch_intern.py" "$@"
