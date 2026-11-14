#!/usr/bin/env bash
# Serve the BillProof UI with no Python app.
# Demo mode reads only static/snapshot/ (frozen API responses).
# Usage: scripts/offline_demo.sh [port]
# Then open http://127.0.0.1:<port>/static/index.html
set -euo pipefail
cd "$(dirname "$0")/../src/billproof/server"
port="${1:-8795}"
echo "open http://127.0.0.1:${port}/static/index.html  (Ctrl-C to stop)"
exec python3 -m http.server "$port" --bind 127.0.0.1
