#!/bin/bash
# 5 of 5. Live end-to-end test: every service, then one real request through Intern (about $0.035).
#   bash setup/5_smoke.sh          # services + one request
#   bash setup/5_smoke.sh slack    # also posts a real thread in SLACK_CHANNEL_ID (one more live trial)
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"

python3 tools/doctor.py --offline
echo
if [ "${1:-}" = "slack" ]; then python3 tools/smoke_e2e.py --slack; else python3 tools/smoke_e2e.py; fi
