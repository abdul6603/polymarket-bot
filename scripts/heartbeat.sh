#!/bin/bash
# Brotherhood Heartbeat — pushes agent status to GitHub gist every cycle
# Called by the auto-pull script after each check

REPO_DIR="$HOME/polymarket-bot"
HEARTBEAT_FILE="$REPO_DIR/data/imac_heartbeat.json"

# Collect status
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
LOCAL_TIME=$(date "+%b %d, %Y %I:%M %p")

# Check which agents are running
garves_pid=$(pgrep -f "bot\.main" | head -1)
hawk_pid=$(pgrep -f "hawk" | head -1)
dashboard_pid=$(pgrep -f "live_dashboard" | head -1)
shelby_pid=$(pgrep -f "shelby" | head -1)
atlas_pid=$(pgrep -f "atlas" | head -1)
thor_pid=$(pgrep -f "thor" | head -1)
viper_pid=$(pgrep -f "viper" | head -1)
robotox_pid=$(pgrep -f "sentinel" | head -1)

# Get Garves details if running
garves_status="offline"
garves_trades=""
garves_wr=""
garves_pnl=""
garves_dry_run=""
if [ -n "$garves_pid" ]; then
    garves_status="online"
    # Read from tracker/status files
    if [ -f "$REPO_DIR/data/trades.jsonl" ]; then
        total=$(wc -l < "$REPO_DIR/data/trades.jsonl" | tr -d ' ')
        garves_trades="$total"
    fi
fi

# Read latest Garves log line for activity
last_trade=""
if [ -f /tmp/garves.log ]; then
    last_trade=$(grep -E "(Order placed|TRADE|No trades this tick)" /tmp/garves.log | tail -1 | head -c 200)
    garves_dry_run=$(grep "Dry run:" /tmp/garves.log | tail -1 | awk '{print $NF}')
fi

# Build JSON
cat > "$HEARTBEAT_FILE" << EOJSON
{
  "timestamp": "$TIMESTAMP",
  "local_time": "$LOCAL_TIME",
  "machine": "iMac",
  "agents": {
    "garves": {"status": "$garves_status", "pid": "${garves_pid:-null}", "dry_run": "${garves_dry_run:-unknown}", "trades": "${garves_trades:-0}"},
    "hawk": {"status": "$([ -n "$hawk_pid" ] && echo online || echo offline)", "pid": "${hawk_pid:-null}"},
    "dashboard": {"status": "$([ -n "$dashboard_pid" ] && echo online || echo offline)", "pid": "${dashboard_pid:-null}"},
    "shelby": {"status": "$([ -n "$shelby_pid" ] && echo online || echo offline)", "pid": "${shelby_pid:-null}"},
    "atlas": {"status": "$([ -n "$atlas_pid" ] && echo online || echo offline)", "pid": "${atlas_pid:-null}"},
    "thor": {"status": "$([ -n "$thor_pid" ] && echo online || echo offline)", "pid": "${thor_pid:-null}"},
    "viper": {"status": "$([ -n "$viper_pid" ] && echo online || echo offline)", "pid": "${viper_pid:-null}"},
    "robotox": {"status": "$([ -n "$robotox_pid" ] && echo online || echo offline)", "pid": "${robotox_pid:-null}"}
  },
  "last_garves_activity": "$(echo "$last_trade" | sed 's/"/\\"/g')"
}
EOJSON

# Push to GitHub (commit + push the heartbeat file)
cd "$REPO_DIR"
git add data/imac_heartbeat.json 2>/dev/null
git commit -m "heartbeat: $(date '+%H:%M')" --no-gpg-sign 2>/dev/null
git push origin main 2>/dev/null

echo "  Heartbeat pushed at $LOCAL_TIME"
