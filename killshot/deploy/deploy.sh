#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────
# Killshot Deploy — push code from local Mac to AWS VPS
#
# Usage:
#   ./deploy.sh <VPS_IP> [SSH_USER]
#
# Examples:
#   ./deploy.sh 18.130.45.67              # uses default user 'ubuntu'
#   ./deploy.sh 18.130.45.67 admin        # custom SSH user
#
# What this does:
#   1. Rsyncs killshot/ module to VPS
#   2. Rsyncs required bot/ modules to VPS
#   3. Restarts the killshot systemd service
#   4. Tails logs for 10 seconds to verify startup
#
# Prerequisites:
#   - SSH key auth configured for the VPS
#   - setup_vps.sh already run on the VPS
#   - .env file configured on the VPS with real values
# ──────────────────────────────────────────────────────────────

if [ $# -lt 1 ]; then
    echo "Usage: $0 <VPS_IP> [SSH_USER]"
    echo "  VPS_IP    — IP address or hostname of the AWS instance"
    echo "  SSH_USER  — SSH user (default: ubuntu)"
    exit 1
fi

VPS_IP="$1"
SSH_USER="${2:-ubuntu}"
LOCAL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REMOTE_ROOT="/home/killshot/polymarket-bot"

SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"

echo "============================================"
echo " Killshot Deploy"
echo "============================================"
echo "  VPS:    ${SSH_USER}@${VPS_IP}"
echo "  Local:  ${LOCAL_ROOT}"
echo "  Remote: ${REMOTE_ROOT}"
echo ""

# Verify local source exists
if [ ! -d "${LOCAL_ROOT}/killshot" ]; then
    echo "ERROR: killshot/ not found at ${LOCAL_ROOT}/killshot"
    exit 1
fi
if [ ! -d "${LOCAL_ROOT}/bot" ]; then
    echo "ERROR: bot/ not found at ${LOCAL_ROOT}/bot"
    exit 1
fi

# ── 1. Rsync killshot module ────────────────────────────────

echo "[1/4] Syncing killshot/ module..."
rsync -avz --delete \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='deploy/' \
    -e "ssh ${SSH_OPTS}" \
    "${LOCAL_ROOT}/killshot/" \
    "${SSH_USER}@${VPS_IP}:${REMOTE_ROOT}/killshot/"

# ── 2. Rsync required bot/ modules ──────────────────────────

echo "[2/4] Syncing bot/ dependencies..."

# Create bot directory structure on remote
ssh ${SSH_OPTS} "${SSH_USER}@${VPS_IP}" \
    "sudo -u killshot mkdir -p ${REMOTE_ROOT}/bot/snipe"

# Individual bot files that Killshot imports
BOT_FILES=(
    "bot/__init__.py"
    "bot/config.py"
    "bot/price_cache.py"
    "bot/binance_feed.py"
    "bot/http_session.py"
    "bot/chainlink_feed.py"
)

for f in "${BOT_FILES[@]}"; do
    if [ -f "${LOCAL_ROOT}/${f}" ]; then
        rsync -avz \
            -e "ssh ${SSH_OPTS}" \
            "${LOCAL_ROOT}/${f}" \
            "${SSH_USER}@${VPS_IP}:${REMOTE_ROOT}/${f}"
    else
        echo "  WARNING: ${f} not found locally — skipping"
    fi
done

# bot/snipe/ subpackage
SNIPE_FILES=(
    "bot/snipe/__init__.py"
    "bot/snipe/clob_book.py"
    "bot/snipe/window_tracker.py"
)

for f in "${SNIPE_FILES[@]}"; do
    if [ -f "${LOCAL_ROOT}/${f}" ]; then
        rsync -avz \
            -e "ssh ${SSH_OPTS}" \
            "${LOCAL_ROOT}/${f}" \
            "${SSH_USER}@${VPS_IP}:${REMOTE_ROOT}/${f}"
    else
        echo "  WARNING: ${f} not found locally — skipping"
    fi
done

# ── 3. Fix ownership + create data dir ──────────────────────

echo "[3/4] Fixing permissions and restarting service..."
ssh ${SSH_OPTS} "${SSH_USER}@${VPS_IP}" << 'REMOTE_CMDS'
    sudo chown -R killshot:killshot /home/killshot/polymarket-bot/
    sudo mkdir -p /home/killshot/polymarket-bot/data
    sudo chown killshot:killshot /home/killshot/polymarket-bot/data

    # Clear __pycache__ to avoid stale bytecode
    sudo find /home/killshot/polymarket-bot/ -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

    # Restart the service
    sudo systemctl restart killshot
    echo "  Service restarted"
REMOTE_CMDS

# ── 4. Tail logs to verify startup ──────────────────────────

echo "[4/4] Tailing logs for 10 seconds..."
echo "---"
ssh ${SSH_OPTS} "${SSH_USER}@${VPS_IP}" \
    "timeout 10 tail -f /var/log/killshot.log 2>/dev/null || true"
echo "---"

# Quick health check
echo ""
echo "Checking service status..."
ssh ${SSH_OPTS} "${SSH_USER}@${VPS_IP}" \
    "sudo systemctl is-active killshot && echo 'Service: RUNNING' || echo 'Service: FAILED — check logs'"

echo ""
echo "============================================"
echo " Deploy complete!"
echo "============================================"
echo ""
echo "  Logs:    ssh ${SSH_USER}@${VPS_IP} 'tail -f /var/log/killshot.log'"
echo "  Status:  ssh ${SSH_USER}@${VPS_IP} 'sudo systemctl status killshot'"
echo "  Stop:    ssh ${SSH_USER}@${VPS_IP} 'sudo systemctl stop killshot'"
echo ""
