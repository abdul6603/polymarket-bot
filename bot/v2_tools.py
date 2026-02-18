"""Garves V2 — Advanced Trading Tools.

New capabilities:
- emergency_stop / clear_emergency_stop — file-based kill switch
- get_open_positions — query current open trades
- daily_trade_report — structured 24h trade summary
- push_trade_alert — notify Shelby on new trade / resolution
- accept_commands — check for commands from Shelby
- generate_signal_rationale — human-readable trade reasoning

Garves V2 Identity:
    Sees markets as probability distributions, not stories.
    Trusts signals over sentiment, mathematics over feelings.
    Every trade: Signal -> Probability -> Edge % -> Action -> Confidence -> P&L
    Ends every major update with "Monitoring." or "Standing by."
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
DATA_DIR = Path(__file__).parent.parent / "data"
TRADES_FILE = DATA_DIR / "trades.jsonl"
STOP_FLAG = DATA_DIR / "emergency_stop.flag"
COMMANDS_FILE = DATA_DIR / "shelby_commands.json"

SHELBY_INTEL = Path.home() / "shelby" / "data" / "intel.json"


# ═══════════════════════════════════════════
#  EMERGENCY STOP
# ═══════════════════════════════════════════

def emergency_stop(reason: str = "Manual stop") -> dict:
    """Activate emergency stop — bot will halt all trading next tick."""
    ts = datetime.now(ET).isoformat()
    STOP_FLAG.write_text(json.dumps({"reason": reason, "timestamp": ts}))
    log.warning("[V2] EMERGENCY STOP activated: %s", reason)
    push_trade_alert({
        "type": "emergency_stop",
        "reason": reason,
        "timestamp": ts,
    }, "emergency_stop")
    return {"stopped": True, "reason": reason, "timestamp": ts}


def clear_emergency_stop() -> dict:
    """Clear the emergency stop flag — resume trading."""
    if STOP_FLAG.exists():
        STOP_FLAG.unlink()
        log.info("[V2] Emergency stop cleared — trading resumed")
        return {"cleared": True}
    return {"cleared": False, "message": "No stop flag active"}


def is_emergency_stopped() -> dict | None:
    """Check if emergency stop is active. Returns stop info or None."""
    if STOP_FLAG.exists():
        try:
            return json.loads(STOP_FLAG.read_text())
        except Exception:
            return {"reason": "unknown", "timestamp": "unknown"}
    return None


# ═══════════════════════════════════════════
#  OPEN POSITIONS
# ═══════════════════════════════════════════

def get_open_positions(tracker) -> dict:
    """Query current open positions from the PositionTracker."""
    positions = []
    for pos in tracker.open_positions:
        positions.append({
            "market_id": pos.market_id[:16],
            "direction": pos.direction,
            "size_usd": pos.size_usd,
            "entry_price": pos.entry_price,
            "strategy": pos.strategy,
            "age_seconds": int(time.time() - pos.opened_at),
        })
    return {
        "count": tracker.count,
        "total_exposure": tracker.total_exposure,
        "positions": positions,
    }


# ═══════════════════════════════════════════
#  DAILY TRADE REPORT
# ═══════════════════════════════════════════

def daily_trade_report() -> dict:
    """Generate structured 24h trade summary."""
    if not TRADES_FILE.exists():
        return {"trades_24h": 0, "message": "No trades file found. Standing by."}

    trades = []
    with open(TRADES_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    now = time.time()
    cutoff = now - 86400
    recent = [t for t in trades if t.get("timestamp", 0) > cutoff]
    resolved = [t for t in recent if t.get("resolved") and t.get("outcome") != "unknown"]
    pending = [t for t in recent if not t.get("resolved")]

    wins = sum(1 for t in resolved if t.get("won"))
    losses = len(resolved) - wins
    wr = (wins / len(resolved) * 100) if resolved else 0

    # By asset
    by_asset = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})
    for t in resolved:
        asset = t.get("asset", "unknown")
        by_asset[asset]["total"] += 1
        if t.get("won"):
            by_asset[asset]["wins"] += 1
        else:
            by_asset[asset]["losses"] += 1

    asset_breakdown = {}
    for asset, stats in by_asset.items():
        asset_wr = (stats["wins"] / stats["total"] * 100) if stats["total"] else 0
        asset_breakdown[asset] = {
            "wins": stats["wins"],
            "losses": stats["losses"],
            "win_rate": round(asset_wr, 1),
        }

    # By timeframe
    by_tf = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})
    for t in resolved:
        tf = t.get("timeframe", "unknown")
        by_tf[tf]["total"] += 1
        if t.get("won"):
            by_tf[tf]["wins"] += 1
        else:
            by_tf[tf]["losses"] += 1

    tf_breakdown = {}
    for tf, stats in by_tf.items():
        tf_wr = (stats["wins"] / stats["total"] * 100) if stats["total"] else 0
        tf_breakdown[tf] = {
            "wins": stats["wins"],
            "losses": stats["losses"],
            "win_rate": round(tf_wr, 1),
        }

    # Avg edge and confidence
    avg_edge = sum(t.get("edge", 0) for t in resolved) / len(resolved) * 100 if resolved else 0
    avg_conf = sum(t.get("confidence", 0) for t in resolved) / len(resolved) if resolved else 0

    # Regime distribution
    regimes = defaultdict(int)
    for t in recent:
        regimes[t.get("regime_label", "unknown")] += 1

    report = {
        "period": "24h",
        "timestamp": datetime.now(ET).isoformat(),
        "trades_24h": len(recent),
        "resolved": len(resolved),
        "pending": len(pending),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wr, 1),
        "avg_edge_pct": round(avg_edge, 2),
        "avg_confidence": round(avg_conf, 3),
        "by_asset": asset_breakdown,
        "by_timeframe": tf_breakdown,
        "regime_distribution": dict(regimes),
        "status": "Monitoring." if pending else "Standing by.",
    }

    return report


# ═══════════════════════════════════════════
#  TRADE ALERTS TO SHELBY
# ═══════════════════════════════════════════

def push_trade_alert(trade_data: dict, event_type: str = "new_trade") -> bool:
    """Push a trade event to Shelby's intel file."""
    try:
        ts = datetime.now(ET).isoformat()
        alert = {
            "type": f"garves_{event_type}",
            "timestamp": ts,
            "source": "garves_v2",
            "data": trade_data,
        }

        existing = []
        if SHELBY_INTEL.exists():
            try:
                with open(SHELBY_INTEL) as f:
                    existing = json.load(f)
            except Exception:
                existing = []

        existing.append(alert)
        existing = existing[-50:]

        SHELBY_INTEL.parent.mkdir(parents=True, exist_ok=True)
        with open(SHELBY_INTEL, "w") as f:
            json.dump(existing, f, indent=2)

        return True
    except Exception as e:
        log.warning("[V2] Failed to push alert to Shelby: %s", str(e)[:100])
        return False


def format_trade_alert(rec_dict: dict, event: str) -> dict:
    """Format a trade record into a clean alert payload."""
    asset = rec_dict.get("asset", "?").upper()
    tf = rec_dict.get("timeframe", "?")
    direction = rec_dict.get("direction", "?").upper()
    edge = rec_dict.get("edge", 0) * 100
    conf = rec_dict.get("confidence", 0)
    prob = rec_dict.get("probability", 0)

    if event == "new_trade":
        return {
            "summary": f"NEW TRADE: {asset}/{tf} {direction}",
            "probability": round(prob, 3),
            "edge_pct": round(edge, 1),
            "confidence": round(conf, 3),
            "regime": rec_dict.get("regime_label", "?"),
            "rationale": rec_dict.get("signal_rationale", ""),
        }
    elif event == "resolution":
        won = rec_dict.get("won", False)
        outcome = rec_dict.get("outcome", "?").upper()
        return {
            "summary": f"{'WIN' if won else 'LOSS'}: {asset}/{tf} predicted={direction} actual={outcome}",
            "won": won,
            "edge_pct": round(edge, 1),
            "confidence": round(conf, 3),
        }
    return rec_dict


# ═══════════════════════════════════════════
#  SHELBY COMMAND INTERFACE
# ═══════════════════════════════════════════

def accept_commands() -> list[dict]:
    """Check for and consume pending commands from Shelby."""
    if not COMMANDS_FILE.exists():
        return []

    try:
        with open(COMMANDS_FILE) as f:
            commands = json.load(f)

        if not commands:
            return []

        # Clear the file after reading
        with open(COMMANDS_FILE, "w") as f:
            json.dump([], f)

        log.info("[V2] Received %d command(s) from Shelby", len(commands))
        return commands
    except Exception as e:
        log.warning("[V2] Failed to read commands: %s", str(e)[:100])
        return []


def process_command(cmd: dict, bot=None) -> dict:
    """Process a single command from Shelby. Returns response."""
    action = cmd.get("action", "")
    ts = datetime.now(ET).isoformat()

    if action == "status":
        positions = get_open_positions(bot.tracker) if bot else {"error": "no bot ref"}
        report = daily_trade_report()
        return {
            "action": "status",
            "timestamp": ts,
            "positions": positions,
            "daily_report": report,
            "emergency_stopped": is_emergency_stopped() is not None,
        }

    elif action == "pause":
        reason = cmd.get("reason", "Shelby ordered pause")
        return emergency_stop(reason)

    elif action == "resume":
        return clear_emergency_stop()

    elif action == "report":
        return daily_trade_report()

    else:
        return {"error": f"Unknown command: {action}", "timestamp": ts}


# ═══════════════════════════════════════════
#  SIGNAL RATIONALE
# ═══════════════════════════════════════════

def generate_signal_rationale(
    direction: str,
    indicator_votes: dict,
    edge: float,
    confidence: float,
    regime_label: str,
    regime_fng: int,
    asset: str,
    timeframe: str,
    implied_up_price: float | None = None,
) -> str:
    """Generate human-readable rationale for a trade signal."""
    total = len(indicator_votes)
    if total == 0:
        return f"{direction.upper()} signal on {asset.upper()}/{timeframe}. No indicator detail."

    agreeing = [name for name, vote in indicator_votes.items()
                if vote.lower() == direction.lower()]
    dissenting = [name for name, vote in indicator_votes.items()
                  if vote.lower() != direction.lower()]

    parts = []

    # Vote summary
    parts.append(f"{len(agreeing)}/{total} indicators voted {direction.upper()}")

    # Key agreeing indicators
    if agreeing:
        parts.append(f"({', '.join(agreeing)})")

    # Dissent
    if dissenting:
        parts.append(f"Dissent: {', '.join(dissenting)}")

    # Regime context
    if regime_label:
        fng_str = f" FnG={regime_fng}" if regime_fng >= 0 else ""
        parts.append(f"Regime: {regime_label}{fng_str}")

    # Edge and confidence
    parts.append(f"Edge: {edge*100:.1f}% after fees")
    parts.append(f"Confidence: {confidence:.1%}")

    # Market context
    if implied_up_price is not None:
        market_bias = "market favors UP" if implied_up_price > 0.55 else \
                      "market favors DOWN" if implied_up_price < 0.45 else \
                      "market is neutral"
        parts.append(f"Implied UP: ${implied_up_price:.3f} ({market_bias})")

    return ". ".join(parts) + "."
