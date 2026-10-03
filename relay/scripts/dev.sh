#!/usr/bin/env bash
# Run the backend (:8000) and the Vite frontend (:5173) together.
# Ctrl-C stops both.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "==> backend  → http://127.0.0.1:8000"
( cd "$ROOT/backend" && ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 ) &

echo "==> frontend → http://localhost:5173"
( cd "$ROOT/frontend" && npm run dev ) &

wait
