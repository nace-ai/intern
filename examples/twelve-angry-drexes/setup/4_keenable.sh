#!/bin/bash
# 4 of 5 (optional). Keenable key. Without it the keyless public endpoint is used, which already works.
#   bash setup/4_keenable.sh
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"

python3 tools/set_secret.py KEENABLE_API_KEY || { echo "No key saved; the keyless endpoint stays in use."; }
echo
python3 tools/smoke_e2e.py --only keenable
