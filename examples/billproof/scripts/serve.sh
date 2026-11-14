#!/usr/bin/env bash
# Start the BillProof server. PORT/HOST via env (defaults: 8000 / 127.0.0.1).
# Usage: scripts/serve.sh [--reload]
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
exec .venv/bin/uvicorn billproof.server.app:app --app-dir src --host "$HOST" --port "$PORT" "$@"
