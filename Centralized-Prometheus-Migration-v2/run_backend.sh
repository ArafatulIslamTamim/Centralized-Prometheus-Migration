#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/backend"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -r requirements.txt
if [ ! -f ../config.json ]; then
  cp ../config.example.json ../config.json
  echo "Created ../config.json from example. Edit it in the GUI or manually."
fi
export CONFIG_PATH="${CONFIG_PATH:-$(cd .. && pwd)/config.json}"
uvicorn app.main:app --host 0.0.0.0 --port "${BACKEND_PORT:-8000}" --reload
