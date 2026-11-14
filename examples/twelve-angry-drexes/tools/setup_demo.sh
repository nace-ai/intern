#!/bin/bash
# One pass to get the live demo ready: install what is missing, ask for the keys that are empty, print a check.
#
#   bash tools/setup_demo.sh            # installs, then keys, then the check
#   bash tools/setup_demo.sh --keys     # only keys: asks for each empty one (hidden prompts)
#   bash tools/setup_demo.sh --smoke    # live end-to-end smoke test (about $0.035; add "slack" to also post a real thread)
#   bash tools/setup_demo.sh --slack    # Slack app: copies the manifest, opens the Slack page, asks for the two tokens
#   bash tools/setup_demo.sh --check    # only the readiness check: no installs, no prompts
#
# Safe to run again: a step that is already done is skipped. No key is ever printed.
# Installs: nace CLI (MIT), Intern with its MCP extra (Apache-2.0), slack_bolt (MIT) in .venv/ of this folder.
# Remove later: uv tool uninstall nace-cli intern-agent; rm -rf .venv intern/home
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
MODE="${1:-all}"
say() { printf '\n== %s\n' "$1"; }

install_tools() {
  say "1. Tools"
  if ! command -v uv >/dev/null; then
    echo "uv is missing. Install it first:  brew install uv   (then run this script again)"
    return 1
  fi
  if command -v nace >/dev/null; then echo "nace CLI: already installed"; else uv tool install nace-cli || echo "nace CLI: install failed (see above)"; fi
  if command -v intern >/dev/null; then
    echo "Intern: already installed"
  else
    echo "Installing Intern with its MCP extra (Python 3.12, from github.com/nace-ai/intern)..."
    uv tool install --python 3.12 "intern-agent[mcp] @ git+https://github.com/nace-ai/intern" || echo "Intern: install failed (see above)"
  fi
  if command -v intern >/dev/null; then
    mkdir -p intern/home
    # Intern's own event schema, kept for reference next to the generated config (overwritten on each setup).
    intern schema > intern/home/event-schema.json 2>/dev/null || true
  fi
  if [ -x .venv/bin/python ] && .venv/bin/python -c "import slack_bolt" 2>/dev/null; then
    echo "Slack library: already installed in .venv"
  else
    echo "Installing the Slack library into .venv ..."
    python3 -m venv .venv && .venv/bin/pip install --quiet -r requirements-slack.txt && echo "Slack library: installed" || echo "Slack library: install failed (see above)"
  fi
  command -v uvx >/dev/null && echo "Nace MCP server: fetched by uvx on first use (uvx nace-mcp)"
}

slack_app() {
  say "Slack app (about 3 minutes, in the browser)"
  if command -v pbcopy >/dev/null; then pbcopy < slack/manifest.yaml && echo "slack/manifest.yaml is now on the clipboard."; fi
  cat <<'EOF'
1. Open https://api.slack.com/apps?new_app=1  ->  "From a manifest"  ->  pick the workspace  ->  paste the manifest (YAML)  ->  Create.
2. "Install to Workspace"  ->  Allow.
3. OAuth & Permissions  ->  copy "Bot User OAuth Token" (starts with xoxb-).
4. Basic Information  ->  App-Level Tokens  ->  Generate, scope connections:write  ->  copy it (starts with xapp-).
5. In Slack: /invite @Twelve Angry Drexes in the channel you will demo in.
EOF
  command -v open >/dev/null && open "https://api.slack.com/apps?new_app=1" 2>/dev/null
  python3 tools/set_secret.py SLACK_BOT_TOKEN
  python3 tools/set_secret.py SLACK_APP_TOKEN
  echo "Then start the bridge:  .venv/bin/python -m engine.slack_bridge"
}

case "$MODE" in
  --keys)  say "Keys and tokens"; python3 tools/set_secret.py --missing ;;
  --smoke) if [ "${2:-}" = "slack" ]; then python3 tools/smoke_e2e.py --slack; else python3 tools/smoke_e2e.py; fi ;;
  --slack) slack_app ;;
  --check) python3 tools/doctor.py ;;
  all)
    install_tools
    say "2. Keys and tokens still empty (hidden prompts; Enter skips one)"
    python3 tools/set_secret.py --missing
    say "3. Readiness check"
    python3 tools/doctor.py
    echo
    echo "Next: python3 tools/smoke_e2e.py   (live end-to-end test, about \$0.035; --slack also posts a real thread)"
    ;;
  *) sed -n 2,13p "$0"; exit 2 ;;
esac
