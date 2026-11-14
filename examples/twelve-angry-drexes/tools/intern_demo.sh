#!/bin/bash
# Runs one question through Intern: Drex as System 1, Qwen on DeepInfra as System 2, and the jury engine,
# Nace's MCP server and Keenable as MCP tools. Progress goes to stderr, Intern's reply to stdout.
#
#   bash tools/intern_demo.sh                                   # the default question (tool theft, Texas) with its report
#   bash tools/intern_demo.sh "your question" [--document FILE] [--case ID]
#
# Needs Intern, NACE_API_KEY and DEEPINFRA_API_KEY:   bash tools/setup_demo.sh     Check:   python3 tools/doctor.py
# The work is done by engine/intern_runner.py, which the Slack bridge and the web pages use too.
set -euo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"
if [ "$#" -eq 0 ]; then
  set -- "A man left our hardware store with two tool kits priced at \$2,740 and says his employer had prepaid. Should we charge it as a state jail felony?" \
         --document cases/files/offense_report.pdf --case tx-theft
fi
exec python3 -m engine.intern_runner "$@"
