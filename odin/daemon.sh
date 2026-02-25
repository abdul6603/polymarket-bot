#!/bin/bash
# Odin daemon — launched by launchctl
set -euo pipefail

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONPATH="$HOME"
cd "$HOME/odin"

# Use the shared venv Python
PYTHON="$HOME/polymarket-bot/.venv/bin/python"

# Ensure data directory
mkdir -p data/macro

echo "[$(date)] Odin daemon starting..."
exec "$PYTHON" -m odin
