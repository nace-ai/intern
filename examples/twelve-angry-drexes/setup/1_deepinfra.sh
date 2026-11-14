#!/bin/bash
# 1 of 5. DeepInfra key for Qwen 3.8 27B: Intern's System 2 and the courtroom speeches.
#   bash setup/1_deepinfra.sh
# Paste the key at the hidden prompt (https://deepinfra.com/dash/api_keys). It is saved to .env (git-ignored,
# mode 600) and never shown. Then one tiny live call to Qwen proves the key and the model name work.
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"

python3 tools/set_secret.py DEEPINFRA_API_KEY || { echo "No key saved. Run this script again."; exit 1; }
echo
python3 tools/smoke_e2e.py --only deepinfra
