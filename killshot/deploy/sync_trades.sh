#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────
# Killshot Trade Sync — rsync paper trade data back to Pro
#
# Runs via cron every 5 minutes on the VPS:
#   */5 * * * * /home/killshot/polymarket-bot/sync_trades.sh >> /var/log/killshot_sync.log 2>&1
#
# Expects these env vars in /home/killshot/polymarket-bot/.env:
#   SYNC_PRO_HOST  — Pro's SSH hostname or IP (e.g. 192.168.1.100)
#   SYNC_PRO_USER  — SSH user on Pro (e.g. abdallaalhamdan)
#
# SSH key auth required: the killshot user's ~/.ssh/id_ed25519
# must be authorized on Pro.
# ──────────────────────────────────────────────────────────────

PROJECT_ROOT="/home/killshot/polymarket-bot"
DATA_DIR="${PROJECT_ROOT}/data"
ENV_FILE="${PROJECT_ROOT}/.env"
SSH_KEY="/home/killshot/.ssh/id_ed25519"

# Load environment variables
if [ -f "${ENV_FILE}" ]; then
    # Source only the sync-related vars (safe subset)
    SYNC_PRO_HOST="$(grep -E '^SYNC_PRO_HOST=' "${ENV_FILE}" | cut -d'=' -f2- | tr -d '[:space:]')"
    SYNC_PRO_USER="$(grep -E '^SYNC_PRO_USER=' "${ENV_FILE}" | cut -d'=' -f2- | tr -d '[:space:]')"
else
    echo "$(date -Iseconds) ERROR: .env not found at ${ENV_FILE}"
    exit 1
fi

# Validate required vars
if [ -z "${SYNC_PRO_HOST:-}" ] || [ -z "${SYNC_PRO_USER:-}" ]; then
    # Silent exit if sync not configured (not an error — just not set up yet)
    exit 0
fi

# Validate SSH key exists
if [ ! -f "${SSH_KEY}" ]; then
    echo "$(date -Iseconds) ERROR: SSH key not found at ${SSH_KEY}"
    echo "  Generate one: sudo -u killshot ssh-keygen -t ed25519 -N '' -f ${SSH_KEY}"
    echo "  Then add the public key to Pro's ~/.ssh/authorized_keys"
    exit 1
fi

# Files to sync
FILES_TO_SYNC=(
    "killshot_paper.jsonl"
    "killshot_status.json"
)

REMOTE_DIR="polymarket-bot/data"
SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes"

sync_count=0
for fname in "${FILES_TO_SYNC[@]}"; do
    src="${DATA_DIR}/${fname}"
    if [ ! -f "${src}" ]; then
        continue
    fi

    # Rsync with compression, preserving timestamps
    # --timeout=30 prevents hanging on network issues
    if rsync -az --timeout=30 \
        -e "ssh ${SSH_OPTS}" \
        "${src}" \
        "${SYNC_PRO_USER}@${SYNC_PRO_HOST}:${REMOTE_DIR}/${fname}"; then
        sync_count=$((sync_count + 1))
    else
        echo "$(date -Iseconds) WARN: Failed to sync ${fname}"
    fi
done

if [ "${sync_count}" -gt 0 ]; then
    echo "$(date -Iseconds) OK: synced ${sync_count} file(s) to ${SYNC_PRO_HOST}"
fi
