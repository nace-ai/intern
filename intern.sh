#!/usr/bin/env bash
# Run intern with this project's .env keys and the pinned models.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -a; source "$here/.env"; set +a
exec intern --system2 deepinfra/Qwen/Qwen3.8-27B --system1 drex-latest "$@"
