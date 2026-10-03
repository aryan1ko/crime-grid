#!/usr/bin/env bash
# Start the Relay backend.
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate
exec uvicorn app.main:app --host "${RELAY_HOST:-127.0.0.1}" --port "${RELAY_PORT:-8000}" "$@"
