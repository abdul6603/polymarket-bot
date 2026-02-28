#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────
# Killshot VPS Setup — Ubuntu 22.04+ on AWS eu-west-2 t3.micro
#
# Run as root (or via sudo) on a fresh instance:
#   chmod +x setup_vps.sh && sudo ./setup_vps.sh
#
# What this does:
#   1. System updates + Python 3.12
#   2. Creates 'killshot' service user
#   3. Sets up project directory + Python venv
#   4. Installs pip dependencies
#   5. Creates placeholder .env
#   6. Installs systemd service
#   7. Configures UFW firewall (SSH only)
#   8. Hardens SSH (no root login, no password auth)
#   9. Sets up log rotation
#  10. Installs cron job for trade sync
# ──────────────────────────────────────────────────────────────

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="/home/killshot/polymarket-bot"

echo "============================================"
echo " Killshot VPS Setup"
echo "============================================"

# ── 1. System packages ───────────────────────────────────────

echo "[1/10] Updating system packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq

echo "[2/10] Installing Python 3.12..."
apt-get install -y -qq software-properties-common
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update -qq
apt-get install -y -qq python3.12 python3.12-venv python3.12-dev

# Verify Python 3.12
python3.12 --version

# ── 2. Create killshot user ──────────────────────────────────

echo "[3/10] Creating killshot user..."
if id "killshot" &>/dev/null; then
    echo "  User 'killshot' already exists — skipping"
else
    useradd --system --create-home --shell /usr/sbin/nologin killshot
    echo "  User 'killshot' created"
fi

# ── 3. Directory structure ───────────────────────────────────

echo "[4/10] Setting up project directories..."
mkdir -p "${PROJECT_ROOT}"/{bot/snipe,killshot,data}
chown -R killshot:killshot "${PROJECT_ROOT}"

# ── 4. Python venv + dependencies ────────────────────────────

echo "[5/10] Creating Python virtual environment..."
if [ ! -d "${PROJECT_ROOT}/.venv" ]; then
    python3.12 -m venv "${PROJECT_ROOT}/.venv"
fi
chown -R killshot:killshot "${PROJECT_ROOT}/.venv"

echo "[6/10] Installing Python dependencies..."
if [ -f "${DEPLOY_DIR}/requirements.txt" ]; then
    sudo -u killshot "${PROJECT_ROOT}/.venv/bin/pip" install --upgrade pip -q
    sudo -u killshot "${PROJECT_ROOT}/.venv/bin/pip" install -r "${DEPLOY_DIR}/requirements.txt" -q
    echo "  Dependencies installed"
else
    echo "  WARNING: requirements.txt not found at ${DEPLOY_DIR}/requirements.txt"
    echo "  You will need to install dependencies manually."
fi

# ── 5. Placeholder .env ─────────────────────────────────────

echo "[7/10] Creating placeholder .env..."
ENV_FILE="${PROJECT_ROOT}/.env"
if [ ! -f "${ENV_FILE}" ]; then
    cat > "${ENV_FILE}" << 'ENVEOF'
# ── Killshot Configuration ───────────────────────────────────
# Copy from Pro's .env and fill in real values

# Mode (MUST be true until paper results are validated)
KILLSHOT_DRY_RUN=true
KILLSHOT_ENABLED=true

# Bankroll
KILLSHOT_BANKROLL_USD=50
KILLSHOT_MAX_BET_USD=5
KILLSHOT_DAILY_LOSS_CAP_USD=15

# Direction detection
KILLSHOT_DIRECTION_THRESHOLD=0.0010

# Entry pricing
KILLSHOT_ENTRY_PRICE_MIN=0.60
KILLSHOT_ENTRY_PRICE_MAX=0.75

# Kill zone timing
KILLSHOT_WINDOW_SECONDS=20
KILLSHOT_MIN_WINDOW_SECONDS=10

# Assets
KILLSHOT_ASSETS=bitcoin

# Loop intervals
KILLSHOT_TICK_INTERVAL_S=0.1
KILLSHOT_SCAN_INTERVAL_S=60

# Wallet (required for live mode — leave empty for paper)
KILLSHOT_PRIVATE_KEY=
KILLSHOT_CLOB_API_KEY=
KILLSHOT_CLOB_API_SECRET=
KILLSHOT_CLOB_API_PASSPHRASE=
KILLSHOT_FUNDER_ADDRESS=

# Garves shared config (needed by bot/config.py)
PRIVATE_KEY=
CLOB_API_KEY=
CLOB_API_SECRET=
CLOB_API_PASSPHRASE=
FUNDER_ADDRESS=
DRY_RUN=true
BINANCE_WS_URL=wss://stream.binance.com:9443

# Telegram notifications (optional)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Trade sync to Pro (used by sync_trades.sh cron)
SYNC_PRO_HOST=
SYNC_PRO_USER=
ENVEOF
    chown killshot:killshot "${ENV_FILE}"
    chmod 600 "${ENV_FILE}"
    echo "  .env created at ${ENV_FILE} — EDIT WITH REAL VALUES"
else
    echo "  .env already exists — skipping"
fi

# ── 6. Systemd service ──────────────────────────────────────

echo "[8/10] Installing systemd service..."
cp "${DEPLOY_DIR}/killshot.service" /etc/systemd/system/killshot.service
touch /var/log/killshot.log
chown killshot:killshot /var/log/killshot.log
systemctl daemon-reload
systemctl enable killshot.service
echo "  Service installed and enabled (not started yet)"

# Log rotation for killshot.log
cat > /etc/logrotate.d/killshot << 'LOGEOF'
/var/log/killshot.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    size 50M
}
LOGEOF

# ── 7. Cron job for trade sync ──────────────────────────────

echo "[9/10] Installing trade sync cron..."
if [ -f "${DEPLOY_DIR}/sync_trades.sh" ]; then
    cp "${DEPLOY_DIR}/sync_trades.sh" "${PROJECT_ROOT}/sync_trades.sh"
    chown killshot:killshot "${PROJECT_ROOT}/sync_trades.sh"
    chmod 755 "${PROJECT_ROOT}/sync_trades.sh"

    # Install cron as killshot user (every 5 minutes)
    CRON_LINE="*/5 * * * * ${PROJECT_ROOT}/sync_trades.sh >> /var/log/killshot_sync.log 2>&1"
    (crontab -u killshot -l 2>/dev/null | grep -v "sync_trades.sh"; echo "${CRON_LINE}") | crontab -u killshot -
    touch /var/log/killshot_sync.log
    chown killshot:killshot /var/log/killshot_sync.log
    echo "  Cron installed: sync every 5 minutes"
else
    echo "  WARNING: sync_trades.sh not found — skipping cron setup"
fi

# ── 8. UFW firewall ─────────────────────────────────────────

echo "[10/10] Configuring UFW firewall..."
apt-get install -y -qq ufw
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw --force enable
echo "  Firewall active: SSH only"

# ── 9. Harden SSH ───────────────────────────────────────────

echo "Hardening SSH..."
SSHD_CONFIG="/etc/ssh/sshd_config"

# Disable root login
if grep -q "^PermitRootLogin" "${SSHD_CONFIG}"; then
    sed -i 's/^PermitRootLogin.*/PermitRootLogin no/' "${SSHD_CONFIG}"
else
    echo "PermitRootLogin no" >> "${SSHD_CONFIG}"
fi

# Disable password authentication
if grep -q "^PasswordAuthentication" "${SSHD_CONFIG}"; then
    sed -i 's/^PasswordAuthentication.*/PasswordAuthentication no/' "${SSHD_CONFIG}"
else
    echo "PasswordAuthentication no" >> "${SSHD_CONFIG}"
fi

# Validate config before restarting
sshd -t && systemctl restart sshd
echo "  SSH hardened: no root, no passwords"

echo ""
echo "============================================"
echo " Setup complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Edit ${ENV_FILE} with real API keys"
echo "  2. Deploy code:  ./deploy.sh <VPS_IP>"
echo "  3. Start:        sudo systemctl start killshot"
echo "  4. Check logs:   sudo journalctl -u killshot -f"
echo "                   tail -f /var/log/killshot.log"
echo ""
echo "  IMPORTANT: Keep KILLSHOT_DRY_RUN=true until paper"
echo "  results are validated. Do NOT flip to live without"
echo "  Jordan's explicit approval."
echo ""
