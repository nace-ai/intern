#!/bin/bash
# 2 of 5. Install Intern (Nace's agent harness, Apache-2.0) with its MCP extra, then check it.
#   bash setup/2_intern.sh
# Needs uv (brew install uv). Installs with Python 3.12 into uv's tool folder; remove with: uv tool uninstall intern-agent
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"

command -v uv >/dev/null || { echo "uv is missing. Run: brew install uv   then run this script again."; exit 1; }
if command -v intern >/dev/null; then
  echo "Intern is already installed: $(command -v intern)"
else
  uv tool install --python 3.12 "intern-agent[mcp] @ git+https://github.com/nace-ai/intern" || { echo "Install failed (see above)."; exit 1; }
  command -v intern >/dev/null || { echo "Installed, but 'intern' is not on PATH. Run: uv tool update-shell   then open a new terminal."; exit 1; }
fi
intern --version 2>/dev/null || true
mkdir -p intern/home
# Intern's own event schema, kept for reference (overwritten each time this script runs).
intern schema > intern/home/event-schema.json 2>/dev/null && echo "event schema saved: intern/home/event-schema.json"
echo
python3 -m engine.intern_runner --check
echo "Next: bash setup/5_smoke.sh   (one real request through Intern)"
