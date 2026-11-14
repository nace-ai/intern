#!/bin/bash
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  if ! command -v python3 >/dev/null 2>&1 ||
     ! python3 -c 'import sys;sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
    echo "Parking Watch needs Python 3.10 or newer. Install a current Python from python.org first."
    read -r -p "Press Enter to exit..."
    exit 1
  fi
  echo "Creating the local Python environment..."
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -r requirements.txt
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env. For Drex detection, open .env and add NACE_API_KEY. For actual texts, add Twilio settings."
fi
if command -v open >/dev/null 2>&1; then
  (sleep 2; open http://localhost:8000) &
fi
echo "Parking Watch running at http://localhost:8000. Press Ctrl+C to stop."
exec uvicorn server:app --host 127.0.0.1 --port 8000
