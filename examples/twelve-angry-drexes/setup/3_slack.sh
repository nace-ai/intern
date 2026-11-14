#!/bin/bash
# 3 of 5. Slack: library, app, tokens, check. About 3 minutes, part of it in the browser.
#   bash setup/3_slack.sh
# Tokens are pasted at hidden prompts, saved to .env (git-ignored, mode 600) and never shown.
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"

echo "== Slack library"
if [ -x .venv/bin/python ] && .venv/bin/python -c "import slack_bolt" 2>/dev/null; then
  echo "already installed in .venv"
else
  python3 -m venv .venv && .venv/bin/pip install --quiet -r requirements-slack.txt && echo "installed in .venv" || { echo "Install failed (see above)."; exit 1; }
fi
echo
echo "== Slack app (skip to the tokens if the app already exists)"
if command -v pbcopy >/dev/null; then pbcopy < slack/manifest.yaml && echo "slack/manifest.yaml is on the clipboard."; fi
cat <<'STEPS'
1. https://api.slack.com/apps?new_app=1  ->  From a manifest  ->  pick the workspace  ->  paste the manifest (YAML)  ->  Create.
2. Install to Workspace  ->  Allow.
3. OAuth & Permissions  ->  copy "Bot User OAuth Token" (starts with xoxb-).
4. Basic Information  ->  App-Level Tokens  ->  Generate Token and Scopes, scope connections:write  ->  copy it (starts with xapp-).
5. In Slack, in the demo channel:  /invite @twelve-angry-drexes   (channel ID: channel name > View channel details, at the bottom).
STEPS
command -v open >/dev/null && open "https://api.slack.com/apps?new_app=1" 2>/dev/null
echo
echo "== Tokens"
python3 tools/set_secret.py SLACK_BOT_TOKEN
python3 tools/set_secret.py SLACK_APP_TOKEN
echo "(the channel ID is optional: with it, every message in that channel starts a trial and the smoke test can post there)"
python3 tools/set_secret.py SLACK_CHANNEL_ID
echo
python3 tools/smoke_e2e.py --only slack
echo
echo "Start the bridge:   .venv/bin/python -m engine.slack_bridge"
echo "Then in Slack:      @twelve-angry-drexes Should we charge the tool theft as a felony?   (attach cases/files/offense_report.pdf)"
echo "Or post a real thread from here:   python3 tools/smoke_e2e.py --only slack-thread --slack"
