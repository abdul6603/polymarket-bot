#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Polymarket BTC/USD 5-Min Trading Bot Setup ==="
echo

# Check Python version (py-clob-client requires >=3.9.10)
PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: No python3 found. Install Python 3.10+ and retry."
    exit 1
fi

PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
PY_MINOR=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")
PY_MICRO=$("$PYTHON" -c "import sys; print(sys.version_info.micro)")

if [ "$PY_MINOR" -lt 10 ] && { [ "$PY_MINOR" -lt 9 ] || [ "$PY_MICRO" -lt 10 ]; }; then
    echo "WARNING: Python $PY_VERSION detected. py-clob-client requires >=3.9.10."
    echo "Install Python 3.10+ (e.g. 'brew install python@3.12') and re-run this script."
    echo "Continuing setup anyway (deps install may fail)..."
    echo
fi

echo "Using: $PYTHON ($PY_VERSION)"

# 1. Create virtual environment
if [ ! -d .venv ]; then
    echo "[1/3] Creating Python virtual environment..."
    "$PYTHON" -m venv .venv
else
    echo "[1/3] Virtual environment already exists."
fi

# 2. Install dependencies
echo "[2/3] Installing dependencies..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

# 3. Create .env from template if it doesn't exist
if [ ! -f .env ]; then
    echo "[3/3] Creating .env from .env.example..."
    cp .env.example .env
    echo "     Edit .env with your credentials before running the bot."
else
    echo "[3/3] .env already exists, skipping."
fi

echo
echo "=== Setup complete ==="
echo
echo "--- Wallet Setup Guide ---"
echo "1. Export your private key from MetaMask:"
echo "   Settings > Security > Reveal Private Key"
echo "   Paste it into .env as PRIVATE_KEY (without 0x prefix)"
echo
echo "2. Derive your CLOB API credentials:"
echo "   source .venv/bin/activate"
echo "   python -c \""
echo "from py_clob_client.client import ClobClient"
echo "c = ClobClient('https://clob.polymarket.com', chain_id=137, key='YOUR_PRIVATE_KEY_HERE')"
echo "creds = c.derive_api_key()"
echo "print(f'CLOB_API_KEY={creds.api_key}')"
echo "print(f'CLOB_API_SECRET={creds.api_secret}')"
echo "print(f'CLOB_API_PASSPHRASE={creds.api_passphrase}')"
echo "\""
echo
echo "3. Set FUNDER_ADDRESS to your Polymarket proxy wallet address."
echo
echo "4. Fund wallet with ~\$10 USDC.e on Polygon for live trading."
echo
echo "5. Run the bot:"
echo "   source .venv/bin/activate"
echo "   python -m bot.main"
