#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js is required to contact Agent Heist's local process supervisor." >&2
  exit 1
fi
exec node "$ROOT/scripts/dev-launcher.mjs" stop "$@"
