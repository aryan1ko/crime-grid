#!/bin/bash
set -e

echo "=== Austin Crime Ontology: Environment Setup ==="

# Check Python version
python3 --version || { echo "Python 3 required"; exit 1; }

# Create virtualenv
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Create data directories
mkdir -p data/raw data/processed data/geo

# Create .env if it doesn't exist
if [ ! -f .env ]; then
  cp .env.example .env
  echo ""
  echo ">>> .env created. Add your Census API key to .env before running pipeline."
  echo ">>> Get a free key at: https://api.census.gov/data/key_signup.html"
fi

echo ""
echo "=== Setup complete. Activate with: source .venv/bin/activate ==="
