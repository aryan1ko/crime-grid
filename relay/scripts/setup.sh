#!/usr/bin/env bash
# One-shot setup: Python venv + backend deps (core & CV) + frontend deps.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Backend: creating venv and installing dependencies"
cd "$ROOT/backend"
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip -q
./.venv/bin/python -m pip install -q -r requirements.txt
echo "==> Backend: installing CV stack (torch/ultralytics/opencv — this is the big one)"
./.venv/bin/python -m pip install -q -r requirements-cv.txt

echo "==> Frontend: installing npm dependencies"
cd "$ROOT/frontend"
npm install

echo
echo "Setup complete. Start everything with:  scripts/dev.sh"
