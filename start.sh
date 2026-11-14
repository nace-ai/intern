#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if ! command -v node >/dev/null 2>&1; then
  echo "Agent Heist needs Node.js 22.12+ and npm. Install Node, then run ./start.sh again." >&2
  exit 1
fi
if ! node -e 'const [a,b]=process.versions.node.split(".").map(Number);process.exit(a>22||(a===22&&b>=12)?0:1)'; then
  echo "Agent Heist needs Node.js 22.12+; found $(node --version). Use 'nvm install 22 && nvm use 22' if you use nvm." >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "npm is missing. Install the npm version included with Node.js 22.12+." >&2
  exit 1
fi
exec node "$ROOT/scripts/dev-launcher.mjs" start "$@"
