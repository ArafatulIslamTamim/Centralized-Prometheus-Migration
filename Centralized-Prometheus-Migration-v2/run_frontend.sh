#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/frontend"
npm install
if [ ! -f .env.local ]; then
  cp .env.local.example .env.local
fi
echo "Starting frontend on http://localhost:3001"
npm run dev
