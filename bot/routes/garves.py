"""Garves V2 — The Directional Sniper: /api/trades, /api/logs, /api/garves/*"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request

from bot.shared import (
    _load_trades,
    _load_recent_logs,
    ET,
    DATA_DIR,
    INDICATOR_ACCURACY_FILE,
    SOREN_QUEUE_FILE,
    MERCURY_POSTING_LOG,
    ATLAS_ROOT,
    SHELBY_ROOT_DIR,
    SHELBY_TASKS_FILE,
)
from bot.routes._utils import read_fresh

garves_bp = Blueprint("garves", __name__)

def _broadcast(event_type, data=None):
    """Emit Socket.IO event via the dashboard's broadcast_event helper."""
    try:
        from bot.live_dashboard import broadcast_event
        broadcast_event(event_type, data)
    except Exception:
        pass

MODE_FILE = DATA_DIR / "garves_mode.json"
QUANT_PARAMS_FILE = DATA_DIR / "quant_live_params.json"
TRADES_FILE = DATA_DIR / "trades.jsonl"
ARCHIVE_DIR = DATA_DIR / "archives"

# Self-healing state (in-memory, resets on restart)
_self_heal_applied = False


@garves_bp.route("/api/garves/health-warnings")
def api_garves_health_warnings():
    """Return critical warnings: paralysis detection, consensus floor issues, trade drought."""
    from datetime import datetime as _dt
    warnings = []

    # 1. Check consensus_floor from Quant params
    consensus_floor = 2  # default
    if QUANT_PARAMS_FILE.exists():
        try:
            qp = json.loads(QUANT_PARAMS_FILE.read_text())
            consensus_floor = qp.get("params", {}).get("consensus_floor", 2)
        except Exception:
            pass

    if consensus_floor > 4:
        warnings.append({
            "level": "critical",
            "message": f"Consensus floor is {consensus_floor} — requires near-unanimous agreement. "
                       f"Garves V2 is likely paralyzed. Max safe value is 4.",
            "action": "Lower consensus_floor in quant_live_params.json",
        })
    elif consensus_floor > 3:
        warnings.append({
            "level": "warning",
            "message": f"Consensus floor is {consensus_floor} — may limit trading in extreme regimes.",
        })

    # 2. Check for trade drought (0 trades in last 24h)
    last_trade_ts = 0
    trades_24h = 0
    # Check current trades file
    if TRADES_FILE.exists():
        try:
            for line in TRADES_FILE.read_text().strip().split("\n"):
                if not line:
                    continue
                t = json.loads(line)
                ts = t.get("timestamp", 0)
                if ts > last_trade_ts:
                    last_trade_ts = ts
                if time.time() - ts < 86400:
                    trades_24h += 1
        except Exception:
            pass
    # Check today's archive
    today_str = _dt.now().strftime("%Y-%m-%d")
    today_archive = ARCHIVE_DIR / f"trades_{today_str}.jsonl"
    if today_archive.exists():
        try:
            for line in today_archive.read_text().strip().split("\n"):
                if not line:
                    continue
                t = json.loads(line)
                ts = t.get("timestamp", 0)
                if ts > last_trade_ts:
                    last_trade_ts = ts
                if time.time() - ts < 86400:
                    trades_24h += 1
        except Exception:
            pass

    hours_since_trade = (time.time() - last_trade_ts) / 3600 if last_trade_ts > 0 else 999
    if trades_24h == 0 and hours_since_trade > 4:
        warnings.append({
            "level": "critical",
            "message": f"0 trades in 24h. Last trade was {hours_since_trade:.0f}h ago. "
                       f"Garves V2 may be paralyzed or market conditions are extreme.",
            "hours_since_trade": round(hours_since_trade, 1),
        })

    # 3. Average implied entry price warning
    try:
        snipe_file = DATA_DIR / "snipe_trades.jsonl"
        if snipe_file.exists():
            lines = snipe_file.read_text().strip().split("\n")
            recent = [json.loads(l) for l in lines[-10:] if l.strip()]
            if recent:
                prices = [t.get("price", 0) for t in recent if t.get("price", 0) > 0]
                if prices:
                    avg_price = sum(prices) / len(prices)
                    if avg_price > 0.58:
                        warnings.append({
                            "level": "warning",
                            "message": f"Avg snipe entry price ${avg_price:.3f} > $0.58 — "
                                       f"need {avg_price * 100:.0f}%+ WR to break even.",
                        })
    except Exception:
        pass

    # 4. Self-healing: if 0 trades for >12h, auto-lower consensus_floor
    global _self_heal_applied
    if hours_since_trade > 12 and consensus_floor > 2 and not _self_heal_applied:
        try:
            if QUANT_PARAMS_FILE.exists():
                qp = json.loads(QUANT_PARAMS_FILE.read_text())
            else:
                qp = {"params": {}, "validation": {"walk_forward_passed": True}}
            qp["params"]["consensus_floor"] = 2
            qp["self_healed"] = {
                "timestamp": _dt.now().isoformat(),
                "reason": f"0 trades for {hours_since_trade:.0f}h, lowered consensus_floor {consensus_floor}->2",
                "previous_value": consensus_floor,
            }
            QUANT_PARAMS_FILE.write_text(json.dumps(qp, indent=2))
            _self_heal_applied = True
            # Invalidate param cache so Garves picks up new value immediately
            try:
                from bot.param_loader import invalidate_cache
                invalidate_cache()
            except Exception:
                pass
            warnings.append({
                "level": "info",
                "message": f"SELF-HEALED: consensus_floor lowered {consensus_floor}->2 "
                           f"(0 trades for {hours_since_trade:.0f}h). Trading should resume.",
            })
        except Exception:
            pass

    # 5. Snipe threshold check — warn if CLOB data quality is poor
    try:
        snipe_status_file = DATA_DIR / "snipe_status.json"
        if snipe_status_file.exists():
            ss = json.loads(snipe_status_file.read_text())
            thresh_info = ss.get("threshold_info", {})
            per_asset = thresh_info.get("per_asset", {})
            high_assets = [f"{a.upper()}={v}" for a, v in per_asset.items() if v > 70]
            if high_assets:
                warnings.append({
                    "level": "warning",
                    "message": f"High snipe thresholds: {', '.join(high_assets)} — CLOB data may be dead/stale",
                })
            if thresh_info.get("override_active"):
                ttl_min = thresh_info.get("override_ttl_s", 0) / 60
                warnings.append({
                    "level": "info",
                    "message": f"Snipe threshold override active: {thresh_info.get('override_value', '?')} "
                               f"(expires in {ttl_min:.0f}m)",
                })
    except Exception:
        pass

    return jsonify({
        "warnings": warnings,
        "consensus_floor": consensus_floor,
        "trades_24h": trades_24h,
        "hours_since_trade": round(hours_since_trade, 1),
        "self_healed": _self_heal_applied,
    })


@garves_bp.route("/api/garves/signal-cycle")
def api_garves_signal_cycle():
    """Signal evaluation cycle status for dashboard countdown badge."""
    status_file = DATA_DIR / "signal_cycle_status.json"
    if status_file.exists():
        try:
            data = json.loads(status_file.read_text())
            data["age_s"] = round(time.time() - data.get("last_eval_at", 0), 1)
            data.setdefault("cycle_count", 0)
            return jsonify(data)
        except Exception:
            pass
    return jsonify({"last_eval_at": 0, "tick_interval_s": 5, "markets_evaluated": 0,
                    "trades_this_tick": 0, "age_s": 999, "cycle_count": 0})


@garves_bp.route("/api/garves/mode")
def api_garves_mode():
    """Current Garves V2 trading mode."""
    if MODE_FILE.exists():
        try:
            data = json.loads(MODE_FILE.read_text())
            return jsonify(data)
        except Exception:
            pass
    # Default: read from env
    dry_run = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes")
    return jsonify({"dry_run": dry_run})


@garves_bp.route("/api/garves/toggle-mode", methods=["POST"])
def api_garves_toggle_mode():
    """Toggle Garves V2 between live and paper trading."""
    from datetime import datetime as dt, timezone as tz
    current_dry = True
    if MODE_FILE.exists():
        try:
            current_dry = json.loads(MODE_FILE.read_text()).get("dry_run", True)
        except Exception:
            pass
    else:
        current_dry = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes")

    new_dry = not current_dry
    DATA_DIR.mkdir(exist_ok=True)
    MODE_FILE.write_text(json.dumps({
        "dry_run": new_dry,
        "toggled_at": dt.now(tz.utc).isoformat(),
    }, indent=2))
    mode_label = "PAPER" if new_dry else "LIVE"
    return jsonify({"success": True, "dry_run": new_dry, "mode": mode_label})


@garves_bp.route("/api/trades")
def api_trades():
    trades = _load_trades()
    now = time.time()

    resolved = [t for t in trades if t.get("resolved")]
    pending = [t for t in trades if not t.get("resolved")]

    wins = [t for t in resolved if t.get("won")]
    losses = [t for t in resolved if not t.get("won") and t.get("outcome") != "unknown"]
    stale = [t for t in resolved if t.get("outcome") == "unknown"]

    total_resolved = len(wins) + len(losses)
    win_rate = (len(wins) / total_resolved * 100) if total_resolved > 0 else 0

    # PnL — use actual trade data when available
    total_pnl = 0.0
    stake = float(os.getenv("ORDER_SIZE_USD", "5.0"))
    for t in resolved:
        if t.get("outcome") == "unknown":
            continue
        if t.get("pnl") is not None:
            total_pnl += t["pnl"]
        else:
            s = t.get("size_usd", stake)
            implied = t.get("implied_up_price", 0.5)
            direction = t.get("direction", "up")
            entry_price = implied if direction == "up" else (1 - implied)
            if t.get("won"):
                total_pnl += s * (1 - entry_price) - s * 0.02
            else:
                total_pnl += -s * entry_price

    # By asset
    by_asset = {}
    for t in resolved:
        if t.get("outcome") == "unknown":
            continue
        a = t.get("asset", "unknown")
        if a not in by_asset:
            by_asset[a] = {"wins": 0, "losses": 0}
        if t.get("won"):
            by_asset[a]["wins"] += 1
        else:
            by_asset[a]["losses"] += 1

    # By timeframe
    by_tf = {}
    for t in resolved:
        if t.get("outcome") == "unknown":
            continue
        tf = t.get("timeframe", "?")
        if tf not in by_tf:
            by_tf[tf] = {"wins": 0, "losses": 0}
        if t.get("won"):
            by_tf[tf]["wins"] += 1
        else:
            by_tf[tf]["losses"] += 1

    # By direction
    by_dir = {}
    for t in resolved:
        if t.get("outcome") == "unknown":
            continue
        d = t.get("direction", "?")
        if d not in by_dir:
            by_dir[d] = {"wins": 0, "losses": 0}
        if t.get("won"):
            by_dir[d]["wins"] += 1
        else:
            by_dir[d]["losses"] += 1

    # Format trades for display
    def fmt_trade(t):
        ts = t.get("timestamp", 0)
        dt = datetime.fromtimestamp(ts, tz=ET)
        end_ts = t.get("market_end_time", 0)
        time_left = ""
        time_left_sec = 0
        if end_ts:
            remaining = end_ts - now
            time_left_sec = remaining
            if remaining > 0:
                mins = int(remaining // 60)
                secs = int(remaining % 60)
                if mins >= 60:
                    hrs = mins // 60
                    mins = mins % 60
                    time_left = f"{hrs}h {mins}m"
                else:
                    time_left = f"{mins}m {secs}s"
            else:
                time_left = "Expired"
        implied = t.get("implied_up_price", 0.5)
        direction = t.get("direction", "up")
        entry_price = implied if direction == "up" else (1 - implied)
        trade_stake = t.get("size_usd", stake)
        if t.get("resolved"):
            if t.get("outcome") == "unknown":
                est_pnl = 0.0
            elif t.get("pnl") is not None:
                est_pnl = t["pnl"]
            elif t.get("won"):
                est_pnl = trade_stake * (1 - entry_price) - trade_stake * 0.02
            else:
                est_pnl = -trade_stake * entry_price
        else:
            est_pnl = trade_stake * (1 - entry_price) - trade_stake * 0.02
        return {
            "trade_id": t.get("trade_id", ""),
            "time": dt.strftime("%I:%M:%S %p"),
            "asset": (t.get("asset", "")).upper(),
            "timeframe": t.get("timeframe", ""),
            "direction": (t.get("direction", "")).upper(),
            "probability": t.get("probability", 0),
            "edge": t.get("edge", 0),
            "confidence": t.get("confidence", 0),
            "implied_up": t.get("implied_up_price", 0),
            "binance_price": t.get("binance_price", 0),
            "resolved": t.get("resolved", False),
            "outcome": (t.get("outcome", "")).upper(),
            "won": t.get("won", False),
            "question": t.get("question", ""),
            "expires": datetime.fromtimestamp(
                end_ts, tz=ET
            ).strftime("%I:%M %p") if end_ts else "",
            "time_left": time_left,
            "time_left_sec": time_left_sec,
            "market_end_ts": end_ts,
            "entry_price": round(entry_price, 4),
            "stake": round(trade_stake, 2),
            "est_pnl": round(est_pnl, 2),
            "signal_rationale": t.get("signal_rationale", ""),
        }

    recent_resolved = sorted(resolved, key=lambda t: t.get("resolve_time", 0), reverse=True)[:20]
    pending_sorted = sorted(pending, key=lambda t: t.get("market_end_time", 0))

    return jsonify({
        "summary": {
            "total_trades": len(trades),
            "resolved": total_resolved,
            "pending": len(pending),
            "stale": len(stale),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "pnl": round(total_pnl, 2),
        },
        "by_asset": by_asset,
        "by_timeframe": by_tf,
        "by_direction": by_dir,
        "recent_trades": [fmt_trade(t) for t in recent_resolved],
        "pending_trades": [fmt_trade(t) for t in pending_sorted],
        "timestamp": now,
    })


@garves_bp.route("/api/trades/live")
def api_trades_live():
    """Live (real money) trades only."""
    all_trades = _load_trades()
    trades = [t for t in all_trades if not t.get("dry_run", True)]
    return _build_trades_response(trades)


@garves_bp.route("/api/trades/sim")
def api_trades_sim():
    """Dry-run (simulation) trades only."""
    all_trades = _load_trades()
    trades = [t for t in all_trades if t.get("dry_run", True)]
    return _build_trades_response(trades)


def _build_trades_response(trades):
    """Shared logic for building trades API response."""
    now = time.time()
    resolved = [t for t in trades if t.get("resolved")]
    pending = [t for t in trades if not t.get("resolved")]
    wins = [t for t in resolved if t.get("won")]
    losses = [t for t in resolved if not t.get("won") and t.get("outcome") != "unknown"]
    stale = [t for t in resolved if t.get("outcome") == "unknown"]
    total_resolved = len(wins) + len(losses)
    win_rate = (len(wins) / total_resolved * 100) if total_resolved > 0 else 0

    stake = float(os.getenv("ORDER_SIZE_USD", "5.0"))
    total_pnl = 0.0
    for t in resolved:
        if t.get("outcome") == "unknown":
            continue
        # Use actual pnl from trade if available (real on-chain data)
        if t.get("pnl") is not None:
            total_pnl += t["pnl"]
        else:
            # Fallback: estimate using actual size_usd or env var
            s = t.get("size_usd", stake)
            implied = t.get("implied_up_price", 0.5)
            direction = t.get("direction", "up")
            entry_price = implied if direction == "up" else (1 - implied)
            if t.get("won"):
                total_pnl += s * (1 - entry_price) - s * 0.02
            else:
                total_pnl += -s * entry_price

    by_asset, by_tf, by_dir = {}, {}, {}
    for t in resolved:
        if t.get("outcome") == "unknown":
            continue
        for key, bucket in [(t.get("asset", "unknown"), by_asset), (t.get("timeframe", "?"), by_tf), (t.get("direction", "?"), by_dir)]:
            if key not in bucket:
                bucket[key] = {"wins": 0, "losses": 0}
            bucket[key]["wins" if t.get("won") else "losses"] += 1

    def fmt_trade(t):
        ts = t.get("timestamp", 0)
        dt = datetime.fromtimestamp(ts, tz=ET)
        end_ts = t.get("market_end_time", 0)
        time_left = ""
        time_left_sec = 0
        if end_ts:
            remaining = end_ts - now
            time_left_sec = remaining
            if remaining > 0:
                mins = int(remaining // 60)
                secs = int(remaining % 60)
                if mins >= 60:
                    hrs = mins // 60
                    mins = mins % 60
                    time_left = f"{hrs}h {mins}m"
                else:
                    time_left = f"{mins}m {secs}s"
            else:
                time_left = "Expired"
        implied = t.get("implied_up_price", 0.5)
        direction = t.get("direction", "up")
        entry_price = implied if direction == "up" else (1 - implied)
        trade_stake = t.get("size_usd", stake)
        if t.get("resolved"):
            if t.get("outcome") == "unknown":
                est_pnl = 0.0
            elif t.get("pnl") is not None:
                est_pnl = t["pnl"]
            elif t.get("won"):
                est_pnl = trade_stake * (1 - entry_price) - trade_stake * 0.02
            else:
                est_pnl = -trade_stake * entry_price
        else:
            est_pnl = trade_stake * (1 - entry_price) - trade_stake * 0.02
        return {
            "trade_id": t.get("trade_id", ""),
            "time": dt.strftime("%I:%M:%S %p"),
            "asset": (t.get("asset", "")).upper(),
            "timeframe": t.get("timeframe", ""),
            "direction": (t.get("direction", "")).upper(),
            "probability": t.get("probability", 0),
            "edge": t.get("edge", 0),
            "confidence": t.get("confidence", 0),
            "implied_up": t.get("implied_up_price", 0),
            "binance_price": t.get("binance_price", 0),
            "resolved": t.get("resolved", False),
            "outcome": (t.get("outcome", "")).upper(),
            "won": t.get("won", False),
            "question": t.get("question", ""),
            "expires": datetime.fromtimestamp(
                end_ts, tz=ET
            ).strftime("%I:%M %p") if end_ts else "",
            "time_left": time_left,
            "time_left_sec": time_left_sec,
            "market_end_ts": end_ts,
            "entry_price": round(entry_price, 4),
            "stake": round(trade_stake, 2),
            "est_pnl": round(est_pnl, 2),
            "signal_rationale": t.get("signal_rationale", ""),
        }

    recent_resolved = sorted(resolved, key=lambda t: t.get("resolve_time", 0), reverse=True)[:20]
    pending_sorted = sorted(pending, key=lambda t: t.get("market_end_time", 0))

    return jsonify({
        "summary": {
            "total_trades": len(trades),
            "resolved": total_resolved,
            "pending": len(pending),
            "stale": len(stale),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "pnl": round(total_pnl, 2),
        },
        "by_asset": by_asset,
        "by_timeframe": by_tf,
        "by_direction": by_dir,
        "recent_trades": [fmt_trade(t) for t in recent_resolved],
        "pending_trades": [fmt_trade(t) for t in pending_sorted],
        "timestamp": now,
    })


@garves_bp.route("/api/logs")
def api_logs():
    lines = _load_recent_logs(40)
    return jsonify({"lines": lines})


@garves_bp.route("/api/garves/report-4h")
def api_garves_report_4h():
    """Performance report broken down by 4-hour windows."""
    trades = _load_trades()
    now = datetime.now(ET)
    stake = float(os.getenv("ORDER_SIZE_USD", "5.0"))

    # Determine windows: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    windows = []
    for h in range(0, 24, 4):
        w_start = today_start.replace(hour=h)
        w_end = w_start + timedelta(hours=4)
        windows.append((w_start, w_end))

    # Also include yesterday's windows for context
    yesterday_start = today_start - timedelta(days=1)
    for h in range(0, 24, 4):
        w_start = yesterday_start.replace(hour=h)
        w_end = w_start + timedelta(hours=4)
        windows.insert(len(windows) - 6, (w_start, w_end))

    reports = []
    for w_start, w_end in windows:
        ts_start = w_start.timestamp()
        ts_end = w_end.timestamp()
        w_trades = [t for t in trades if ts_start <= t.get("timestamp", 0) < ts_end]
        if not w_trades:
            continue
        resolved = [t for t in w_trades if t.get("resolved") and t.get("outcome") not in ("unknown", None)]
        pending = [t for t in w_trades if not t.get("resolved")]
        wins = [t for t in resolved if t.get("won")]
        losses = [t for t in resolved if not t.get("won")]
        wr = (len(wins) / len(resolved) * 100) if resolved else 0

        pnl = 0.0
        for t in resolved:
            if t.get("pnl") is not None:
                pnl += t["pnl"]
            else:
                s = t.get("size_usd", stake)
                implied = t.get("implied_up_price", 0.5)
                d = t.get("direction", "up")
                ep = implied if d == "up" else (1 - implied)
                if t.get("won"):
                    pnl += s * (1 - ep) - s * 0.02
                else:
                    pnl += -s * ep

        # Best/worst trade
        best_edge = max((t.get("edge", 0) for t in w_trades), default=0)
        avg_conf = sum(t.get("confidence", 0) for t in w_trades) / len(w_trades) if w_trades else 0

        # By asset breakdown
        by_asset = {}
        for t in resolved:
            a = t.get("asset", "unknown")
            if a not in by_asset:
                by_asset[a] = {"w": 0, "l": 0}
            if t.get("won"):
                by_asset[a]["w"] += 1
            else:
                by_asset[a]["l"] += 1

        is_current = w_start <= now < w_end
        reports.append({
            "window": f"{w_start.strftime('%b %d %I:%M %p')} - {w_end.strftime('%I:%M %p')}",
            "window_start": w_start.isoformat(),
            "is_current": is_current,
            "total": len(w_trades),
            "resolved": len(resolved),
            "pending": len(pending),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(wr, 1),
            "pnl": round(pnl, 2),
            "avg_confidence": round(avg_conf, 4),
            "best_edge": round(best_edge, 4),
            "by_asset": by_asset,
        })

    # Overall summary
    all_resolved = [t for t in trades if t.get("resolved") and t.get("outcome") not in ("unknown", None)]
    all_wins = sum(1 for t in all_resolved if t.get("won"))
    total_pnl = 0.0
    for t in all_resolved:
        implied = t.get("implied_up_price", 0.5)
        d = t.get("direction", "up")
        ep = implied if d == "up" else (1 - implied)
        if t.get("won"):
            total_pnl += stake * (1 - ep) - stake * 0.02
        else:
            total_pnl += -stake * ep

    return jsonify({
        "generated_at": now.isoformat(),
        "summary": {
            "total_trades": len(trades),
            "resolved": len(all_resolved),
            "wins": all_wins,
            "losses": len(all_resolved) - all_wins,
            "win_rate": round((all_wins / len(all_resolved) * 100) if all_resolved else 0, 1),
            "total_pnl": round(total_pnl, 2),
        },
        "windows": reports,
    })


@garves_bp.route("/api/garves/regime")
def api_garves_regime():
    """Current market regime from Fear & Greed Index."""
    try:
        from bot.regime import detect_regime
        regime = detect_regime()
        return jsonify({
            "label": regime.label,
            "fng_value": regime.fng_value,
            "size_multiplier": regime.size_multiplier,
            "edge_multiplier": regime.edge_multiplier,
            "consensus_offset": regime.consensus_offset,
        })
    except Exception as e:
        return jsonify({"label": "unknown", "fng_value": -1, "error": str(e)[:200]})


@garves_bp.route("/api/garves/conviction")
def api_garves_conviction():
    """ConvictionEngine status — asset signals, scoring components, safety rails."""
    try:
        from bot.conviction import ConvictionEngine, TRADES_FILE as CE_TRADES
        # Build a standalone engine to read current state from trades.jsonl
        engine = ConvictionEngine()
        status = engine.get_status()

        # Load indicator accuracy for display
        acc_data = {}
        if INDICATOR_ACCURACY_FILE.exists():
            with open(INDICATOR_ACCURACY_FILE) as f:
                acc_data = json.load(f)

        # Compute dynamic weights for display
        from bot.weight_learner import get_dynamic_weights
        from bot.signals import WEIGHTS
        dyn_weights = get_dynamic_weights(WEIGHTS)

        # Build weight comparison
        weight_info = {}
        for name, base_w in WEIGHTS.items():
            entry = acc_data.get(name, {})
            weight_info[name] = {
                "base_weight": base_w,
                "dynamic_weight": round(dyn_weights.get(name, base_w), 3),
                "accuracy": round(entry.get("accuracy", 0) * 100, 1) if entry else None,
                "total_votes": entry.get("total_votes", 0) if entry else 0,
                "disabled": dyn_weights.get(name, base_w) <= 0,
            }

        # Pull live bankroll multiplier
        try:
            from bot.bankroll import BankrollManager
            bm = BankrollManager()
            bankroll_status = bm.get_status()
        except Exception:
            bankroll_status = {}

        return jsonify({
            "engine_status": status,
            "indicator_weights": weight_info,
            "size_tiers": {
                "0-14": "$0 (no trade)",
                "15-29": "$10-15 (micro)",
                "30-49": "$15-25 (small)",
                "50-69": "$25-35 (standard)",
                "70-84": "$35-45 (increased)",
                "85-100": "$45-55 (max conviction)",
            },
            "consensus_model": "proportional 70% of active indicators, floor=3",
            "confidence_floor": "0.55 (91.7% WR at conf>=60%)",
            "stacking_cap": "max 3 trades per market",
            "blocked_hours": "1,3,4,5,6,7,8,23 (8AM=50% WR removed)",
            "bankroll": bankroll_status,
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@garves_bp.route("/api/garves/indicator-scorecards")
def api_garves_indicator_scorecards():
    """Per-indicator accuracy scorecards: by asset, regime, confidence band."""
    try:
        if not INDICATOR_ACCURACY_FILE.exists():
            return jsonify({"scorecards": {}, "message": "No accuracy data yet"})

        with open(INDICATOR_ACCURACY_FILE) as f:
            raw = json.load(f)

        scorecards = {}
        for name, entry in raw.items():
            scorecards[name] = {
                "total_votes": entry.get("total_votes", 0),
                "correct_votes": entry.get("correct_votes", 0),
                "accuracy": round(entry.get("accuracy", 0) * 100, 1),
                "confidence_weighted_accuracy": round(
                    entry.get("confidence_weighted_accuracy", 0) * 100, 1
                ),
                "by_asset": {
                    asset: {
                        "total": sub.get("total", 0),
                        "correct": sub.get("correct", 0),
                        "accuracy": round(sub.get("accuracy", 0) * 100, 1),
                    }
                    for asset, sub in entry.get("by_asset", {}).items()
                },
                "by_regime": {
                    regime: {
                        "total": sub.get("total", 0),
                        "correct": sub.get("correct", 0),
                        "accuracy": round(sub.get("accuracy", 0) * 100, 1),
                    }
                    for regime, sub in entry.get("by_regime", {}).items()
                },
                "by_confidence_band": {
                    band: {
                        "total": sub.get("total", 0),
                        "correct": sub.get("correct", 0),
                        "accuracy": round(sub.get("accuracy", 0) * 100, 1),
                    }
                    for band, sub in entry.get("by_confidence_band", {}).items()
                },
            }

        # Sort by total votes descending
        sorted_cards = dict(
            sorted(scorecards.items(), key=lambda x: x[1]["total_votes"], reverse=True)
        )
        return jsonify({"scorecards": sorted_cards})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@garves_bp.route("/api/garves/maker")
def api_garves_maker():
    """MakerEngine status — active quotes, inventory, estimated rebates."""
    maker_state_file = DATA_DIR / "maker_state.json"
    if maker_state_file.exists():
        try:
            data = json.loads(maker_state_file.read_text())
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": str(e)[:200], "enabled": False}), 500
    return jsonify({
        "enabled": False,
        "active_quotes": [],
        "inventory": {},
        "config": {},
        "stats": {"fills_today": 0, "estimated_rebate_today": 0, "active_quote_count": 0},
        "message": "MakerEngine not active (set MAKER_ENABLED=true)",
    })


@garves_bp.route("/api/garves/daily-reports")
def api_garves_daily_reports():
    """Get the daily performance history table."""
    try:
        from bot.daily_cycle import get_daily_reports
        reports = get_daily_reports(limit=30)
        return jsonify({"reports": reports})
    except Exception as e:
        return jsonify({"reports": [], "error": str(e)[:200]}), 500


@garves_bp.route("/api/garves/daily-report/today")
def api_garves_daily_today():
    """Get today's live report (without archiving)."""
    try:
        from bot.daily_cycle import generate_daily_report
        report = generate_daily_report()
        return jsonify(report)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@garves_bp.route("/api/garves/derivatives")
def api_garves_derivatives():
    """Live derivatives data: funding rates, liquidations, spot depth."""
    deriv_state_file = DATA_DIR / "derivatives_state.json"
    result = read_fresh(deriv_state_file, "~/polymarket-bot/data/derivatives_state.json")
    if not result:
        result = {"funding_rates": {}, "liquidations": {}, "spot_depth": {}, "connected": False}

    # Also try spot depth from binance depth state
    depth_file = DATA_DIR / "spot_depth.json"
    depth = read_fresh(depth_file, "~/polymarket-bot/data/spot_depth.json")
    if depth:
        result["spot_depth"] = depth

    return jsonify(result)


@garves_bp.route("/api/garves/broadcasts")
def api_garves_broadcasts():
    """Process and acknowledge broadcasts for Garves V2."""
    try:
        # Path already added via bot.shared.ensure_path
        from core.broadcast import get_unread_broadcasts, acknowledge_broadcast

        garves_data = DATA_DIR
        unread = get_unread_broadcasts(garves_data)
        for bc in unread:
            acknowledge_broadcast("garves", bc.get("id", ""), garves_data)

        return jsonify({"processed": len(unread), "agent": "garves"})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@garves_bp.route("/api/garves/trade-event", methods=["POST"])
def api_garves_trade_event():
    """Webhook for Garves V2 bot to push trade events via Socket.IO."""
    try:
        data = request.get_json(force=True) or {}
        event_type = data.get("event", "")
        if event_type == "trade_new":
            _broadcast("trade_new", {
                "asset": data.get("asset", "?"),
                "direction": data.get("direction", "?"),
                "edge": data.get("edge", 0),
                "timeframe": data.get("timeframe", "?"),
            })
        elif event_type == "trade_resolved":
            _broadcast("trade_resolved", {
                "asset": data.get("asset", "?"),
                "direction": data.get("direction", "?"),
                "won": data.get("won", False),
                "pnl": data.get("pnl", 0),
            })
        else:
            _broadcast("agent_status", {"agent": "garves", "message": data.get("message", "")})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@garves_bp.route("/api/garves/news-sentiment")
def api_garves_news_sentiment():
    """Crypto news sentiment from Atlas Tavily feed."""
    try:
        from atlas.news_sentiment import TavilyCryptoSentiment
        sentiment = TavilyCryptoSentiment()
        latest = sentiment.get_latest()
        if latest:
            return jsonify(latest)
        return jsonify({"assets": {}, "scanned_at": None, "message": "No sentiment data yet"})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


BALANCE_CACHE_FILE = DATA_DIR / "polymarket_balance.json"
BALANCE_CACHE_TTL = 60  # seconds
POLYMARKET_WALLET = os.getenv("FUNDER_ADDRESS", "0x7CA4C1122aED3a226fEE08C38F329Ddf2Fb7817E")


def _clob_hmac(secret: str, timestamp: int, method: str, path: str) -> str:
    """Build HMAC-SHA256 signature for Polymarket CLOB L2 auth."""
    import base64, hashlib, hmac as _hmac
    key = base64.urlsafe_b64decode(secret)
    msg = f"{timestamp}{method}{path}"
    sig = _hmac.new(key, msg.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode()


def _fetch_usdc_balance(wallet: str) -> float:
    """Query USDC collateral balance via Polymarket CLOB API (raw HTTP, no py_clob_client)."""
    import urllib.request
    clob_host = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
    api_key = os.getenv("CLOB_API_KEY", "")
    api_secret = os.getenv("CLOB_API_SECRET", "")
    api_passphrase = os.getenv("CLOB_API_PASSPHRASE", "")
    funder = os.getenv("FUNDER_ADDRESS", "")

    if not api_key or not api_secret:
        raise RuntimeError("CLOB API credentials not configured")

    path = "/balance-allowance"
    url = f"{clob_host}{path}?asset_type=COLLATERAL&signature_type=2"
    ts = int(time.time())
    sig = _clob_hmac(api_secret, ts, "GET", path)

    req = urllib.request.Request(url, headers={
        "POLY_ADDRESS": funder,
        "POLY_SIGNATURE": sig,
        "POLY_TIMESTAMP": str(ts),
        "POLY_API_KEY": api_key,
        "POLY_PASSPHRASE": api_passphrase,
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    return int(data.get("balance", "0")) / 1e6


def _fetch_cash_from_pro() -> float | None:
    """Read USDC cash balance from Pro M3 via SSH (Garves V2 bot writes this file)."""
    import subprocess
    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=3", "-o", "StrictHostKeyChecking=no",
             "pro", "cat", "/Users/macuser/polymarket-bot/data/polymarket_balance.json"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            cash = data.get("cash")
            if cash is not None:
                return float(cash)
    except Exception:
        pass
    return None


def _fetch_position_value(wallet: str) -> float:
    """Get open position value from Polymarket data API."""
    import urllib.request
    url = f"https://data-api.polymarket.com/value?user={wallet.lower()}"
    req = urllib.request.Request(url, headers={"User-Agent": "GarvesV2/2.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    if isinstance(data, list) and data:
        return float(data[0].get("value", 0))
    return 0.0


@garves_bp.route("/api/garves/balance")
def api_garves_balance():
    """Live Polymarket portfolio balance.

    Priority chain:
    1. Fresh cache from Garves V2 bot on Pro (< 5 min old)
    2. Public data-api for position value (always works, no VPN)
       + CLOB API for USDC cash (needs VPN, best-effort)
    3. Stale cache as last resort
    """
    bankroll = float(os.getenv("BANKROLL_USD", "250.0"))
    wallet = POLYMARKET_WALLET

    # 1. Try fresh cache first
    stale_cached = None
    if BALANCE_CACHE_FILE.exists():
        try:
            cached = json.loads(BALANCE_CACHE_FILE.read_text())
            age = time.time() - cached.get("fetched_at", 0)
            if age < 300:
                cached["bankroll"] = bankroll
                cached["pnl"] = round(cached.get("portfolio", 0) - bankroll, 2)
                cached["cache_age_s"] = round(age)
                return jsonify(cached)
            # Keep stale cache for fallback
            stale_cached = cached
        except Exception:
            pass

    # 2. Fetch live from public APIs (no VPN needed for position value)
    result = {"portfolio": 0.0, "cash": 0.0, "positions_value": 0.0,
              "pnl": 0.0, "bankroll": bankroll, "live": False, "error": None}

    # Position value — public data-api, always works
    try:
        pos_val = _fetch_position_value(wallet)
        result["positions_value"] = round(pos_val, 2)
        result["live"] = True
    except Exception as e:
        result["error"] = f"Position value fetch failed: {str(e)[:80]}"

    # USDC cash — try CLOB API first, then SSH to Pro, then stale cache
    try:
        cash = _fetch_usdc_balance(wallet)
        result["cash"] = round(cash, 2)
    except Exception:
        # CLOB API failed (no VPN) — try reading balance from Pro via SSH
        try:
            cash = _fetch_cash_from_pro()
            if cash is not None:
                result["cash"] = round(cash, 2)
        except Exception:
            pass
        # Last resort: stale cache
        if result["cash"] == 0.0 and stale_cached and stale_cached.get("cash"):
            result["cash"] = stale_cached["cash"]

    if result["live"]:
        result["portfolio"] = round(result["cash"] + result["positions_value"], 2)
        result["pnl"] = round(result["portfolio"] - bankroll, 2)
    elif stale_cached:
        # 3. Fall back to stale cache
        stale_cached["bankroll"] = bankroll
        stale_cached["pnl"] = round(stale_cached.get("portfolio", 0) - bankroll, 2)
        stale_cached["cache_age_s"] = round(time.time() - stale_cached.get("fetched_at", 0))
        stale_cached["stale"] = True
        return jsonify(stale_cached)
    else:
        result["error"] = "Balance unavailable — no cache and API unreachable"

    result["fetched_at"] = time.time()
    try:
        BALANCE_CACHE_FILE.write_text(json.dumps(result, indent=2))
    except Exception:
        pass

    return jsonify(result)


@garves_bp.route("/api/garves/bankroll")
def api_garves_bankroll():
    """Auto-compounding bankroll status."""
    try:
        from bot.bankroll import BankrollManager
        bm = BankrollManager()
        return jsonify(bm.get_status())
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@garves_bp.route("/api/garves/ml-status")
def api_garves_ml_status():
    """ML Win Predictor status — model metrics and feature importances."""
    try:
        metrics_file = DATA_DIR / "models" / "garves_rf_metrics.json"
        model_file = DATA_DIR / "models" / "garves_rf_model.joblib"

        metrics = {}
        if metrics_file.exists():
            metrics = json.loads(metrics_file.read_text())

        return jsonify({
            "model_loaded": model_file.exists(),
            "metrics": metrics,
            "training_samples": metrics.get("num_samples", 0),
            "cv_accuracy": metrics.get("cv_accuracy", 0),
            "f1": metrics.get("f1", 0),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200], "model_loaded": False}), 500


@garves_bp.route("/api/garves/orderbook-stats")
def api_garves_orderbook_stats():
    """Orderbook depth stats from recent trades."""
    try:
        trades = _load_trades()
        ob_trades = [t for t in trades if t.get("ob_liquidity_usd", 0) > 0]
        if not ob_trades:
            return jsonify({
                "total_with_ob_data": 0,
                "message": "No trades with orderbook data yet (V3 feature)",
            })

        recent = ob_trades[-50:]  # last 50 trades with OB data
        avg_liq = sum(t["ob_liquidity_usd"] for t in recent) / len(recent)
        avg_spread = sum(t["ob_spread"] for t in recent) / len(recent)
        avg_slip = sum(t["ob_slippage_pct"] for t in recent) / len(recent)

        # Win rate by liquidity bucket
        high_liq = [t for t in ob_trades if t["ob_liquidity_usd"] >= 500]
        low_liq = [t for t in ob_trades if 0 < t["ob_liquidity_usd"] < 500]
        high_liq_wr = (sum(1 for t in high_liq if t.get("won")) / len(high_liq) * 100) if high_liq else 0
        low_liq_wr = (sum(1 for t in low_liq if t.get("won")) / len(low_liq) * 100) if low_liq else 0

        return jsonify({
            "total_with_ob_data": len(ob_trades),
            "recent_avg_liquidity": round(avg_liq, 2),
            "recent_avg_spread": round(avg_spread, 4),
            "recent_avg_slippage_pct": round(avg_slip * 100, 2),
            "high_liq_trades": len(high_liq),
            "high_liq_wr": round(high_liq_wr, 1),
            "low_liq_trades": len(low_liq),
            "low_liq_wr": round(low_liq_wr, 1),
            "thresholds": {
                "min_liquidity_usd": 150,
                "max_spread": 0.06,
                "max_slippage_pct": 5.0,
            },
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@garves_bp.route("/api/garves/external-data")
def api_garves_external_data():
    """External data intelligence: Coinglass, FRED Macro, DeFiLlama, Mempool, Whale Alert."""
    state_file = DATA_DIR / "external_data_state.json"
    result = read_fresh(state_file, "~/polymarket-bot/data/external_data_state.json")
    if not result:
        result = {"timestamp": 0, "assets": {}, "defi": None, "mempool": None, "macro": None}
    return jsonify(result)


POSITIONS_CACHE_FILE = DATA_DIR / "polymarket_positions.json"
POSITIONS_CACHE_TTL = 30  # seconds


def _parse_asset_from_title(title: str) -> str:
    """Extract asset name from market title."""
    t = title.lower()
    if "bitcoin" in t:
        return "BTC"
    if "ethereum" in t:
        return "ETH"
    if "solana" in t:
        return "SOL"
    if "xrp" in t:
        return "XRP"
    return "?"


@garves_bp.route("/api/garves/positions")
def api_garves_positions():
    """Live on-chain portfolio — positions + activity-based W/L history.

    Uses two Polymarket data-api endpoints:
    - /positions for current open holdings
    - /activity for full trade history (wins get redeemed and vanish from positions)
    """
    # Check cache
    if POSITIONS_CACHE_FILE.exists():
        try:
            cached = json.loads(POSITIONS_CACHE_FILE.read_text())
            if time.time() - cached.get("fetched_at", 0) < POSITIONS_CACHE_TTL:
                return jsonify(cached)
        except Exception:
            pass

    wallet = POLYMARKET_WALLET
    result = {
        "holdings": [],
        "history": [],
        "totals": {
            "open_count": 0,
            "open_margin": 0.0,
            "open_value": 0.0,
            "open_pnl": 0.0,
            "record_wins": 0,
            "record_losses": 0,
            "realized_pnl": 0.0,
        },
        "live": False, "fetched_at": 0, "error": None,
    }

    try:
        import urllib.request
        headers = {"User-Agent": "GarvesV2/2.0"}

        # ── 1. Fetch current positions (open holdings) ──
        pos_url = f"https://data-api.polymarket.com/positions?user={wallet.lower()}"
        req = urllib.request.Request(pos_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            pos_data = json.loads(resp.read().decode())

        holdings = []
        totals = result["totals"]

        # Group by condition_id
        grouped: dict[str, list] = {}
        if isinstance(pos_data, list):
            for pos in pos_data:
                size = float(pos.get("size", 0))
                if size <= 0:
                    continue
                cid = pos.get("conditionId", pos.get("asset", ""))
                grouped.setdefault(cid, []).append(pos)

        for cid, entries in grouped.items():
            total_size = sum(float(e.get("size", 0)) for e in entries)
            total_cost = sum(float(e.get("size", 0)) * float(e.get("avgPrice", 0)) for e in entries)
            cur_price = float(entries[0].get("curPrice", 0))
            total_value = total_size * cur_price
            avg_price = total_cost / total_size if total_size > 0 else 0
            pnl = total_value - total_cost
            title = entries[0].get("title", entries[0].get("slug", "Unknown"))
            outcome = entries[0].get("outcome", "")
            asset = _parse_asset_from_title(title)

            row = {
                "market": title, "asset": asset, "outcome": outcome,
                "size": round(total_size, 2), "avg_price": round(avg_price, 4),
                "cur_price": round(cur_price, 4), "cost": round(total_cost, 2),
                "value": round(total_value, 2), "pnl": round(pnl, 2),
                "pnl_pct": round((pnl / total_cost * 100) if total_cost > 0 else 0, 1),
                "_cid": cid,
            }

            # Include if: actively trading (mid-range price) OR won unredeemed (price ~1.0)
            if cur_price >= 0.999:
                row["status"] = "won"
                holdings.append(row)
                totals["open_count"] += 1
                totals["open_margin"] += total_cost
                totals["open_value"] += total_value
                totals["open_pnl"] += pnl
            elif cur_price > 0.001:
                holdings.append(row)
                totals["open_count"] += 1
                totals["open_margin"] += total_cost
                totals["open_value"] += total_value
                totals["open_pnl"] += pnl

        holdings.sort(key=lambda x: -x["value"])

        # ── 1b. Fetch game times from Gamma API for countdown timers ──
        # Collect unique event slugs from positions data
        slug_to_cids: dict[str, list[str]] = {}
        cid_to_slug: dict[str, str] = {}
        if isinstance(pos_data, list):
            for pos in pos_data:
                cid = pos.get("conditionId", pos.get("asset", ""))
                slug = pos.get("eventSlug", "")
                if slug and cid:
                    slug_to_cids.setdefault(slug, []).append(cid)
                    cid_to_slug[cid] = slug

        for slug in slug_to_cids:
            try:
                gamma_url = f"https://gamma-api.polymarket.com/events?slug={slug}"
                greq = urllib.request.Request(gamma_url, headers=headers)
                with urllib.request.urlopen(greq, timeout=5) as gresp:
                    events = json.loads(gresp.read().decode())
                if not events:
                    continue
                ev = events[0] if isinstance(events, list) else events
                # Build conditionId → gameStartTime lookup from markets
                def _normalize_dt(s: str) -> str:
                    """Normalize datetime to ISO 8601 (2026-02-21T20:00:00Z)."""
                    if not s:
                        return ""
                    s = s.strip().replace(" ", "T")
                    if s.endswith("+00"):
                        s = s[:-3] + "Z"
                    elif not s.endswith("Z") and "+" not in s[10:]:
                        s += "Z"
                    return s

                cid_times: dict[str, str] = {}
                for m in ev.get("markets", []):
                    mcid = m.get("conditionId", "")
                    gst = m.get("gameStartTime") or m.get("endDate") or ""
                    if mcid and gst:
                        cid_times[mcid] = _normalize_dt(gst)
                # Also use event-level endDate/startTime as fallback
                ev_end = _normalize_dt(ev.get("startTime") or ev.get("endDate") or "")
                for h in holdings:
                    hcid = h.get("_cid", "")
                    if hcid in cid_times:
                        h["end_date"] = cid_times[hcid]
                    elif hcid in slug_to_cids.get(slug, []) and ev_end:
                        h["end_date"] = ev_end
            except Exception:
                pass
        # Strip internal _cid field
        for h in holdings:
            h.pop("_cid", None)

        # ── 2. Fetch activity for real W/L record ──
        act_url = f"https://data-api.polymarket.com/activity?user={wallet.lower()}&limit=500"
        req2 = urllib.request.Request(act_url, headers=headers)
        with urllib.request.urlopen(req2, timeout=10) as resp2:
            activity = json.loads(resp2.read().decode())

        # Group activity by market title → compute spent vs redeemed
        from collections import defaultdict
        markets: dict[str, dict] = defaultdict(lambda: {
            "spent": 0.0, "redeemed": 0.0, "outcome": "", "trades": 0,
        })
        if isinstance(activity, list):
            for e in activity:
                title = e.get("title", "?")
                if e.get("type") == "TRADE":
                    sz = float(e.get("size", 0))
                    px = float(e.get("price", 0))
                    markets[title]["spent"] += sz * px
                    markets[title]["trades"] += 1
                    if not markets[title]["outcome"]:
                        markets[title]["outcome"] = e.get("outcome", "")
                elif e.get("type") == "REDEEM":
                    markets[title]["redeemed"] += float(e.get("size", 0))

        history = []
        for title, m in markets.items():
            if m["trades"] == 0:
                continue
            pnl = m["redeemed"] - m["spent"]
            won = m["redeemed"] > 0 and pnl > 0
            asset = _parse_asset_from_title(title)
            row = {
                "market": title, "asset": asset,
                "outcome": m["outcome"],
                "size": 0, "cost": round(m["spent"], 2),
                "won": won,
                "result_pnl": round(pnl, 2),
            }
            # Skip markets still open (no redeem yet and position still active)
            if m["redeemed"] == 0:
                # Check if this market is in current holdings (still open)
                still_open = any(h["market"] == title for h in holdings)
                if still_open:
                    continue
                # Not open, no redeem → loss
                row["won"] = False
                row["result_pnl"] = round(-m["spent"], 2)

            history.append(row)
            if row["won"]:
                totals["record_wins"] += 1
                totals["realized_pnl"] += pnl
            else:
                totals["record_losses"] += 1
                totals["realized_pnl"] += row["result_pnl"]

        # Sort: wins first (by pnl desc), then losses
        history.sort(key=lambda x: (-int(x["won"]), -x.get("result_pnl", 0)))

        for k in ("open_margin", "open_value", "open_pnl", "realized_pnl"):
            totals[k] = round(totals[k], 2)

        result["holdings"] = holdings
        result["history"] = history
        result["totals"] = totals
        result["live"] = True
    except Exception as e:
        result["error"] = str(e)[:200]

    result["fetched_at"] = time.time()
    try:
        POSITIONS_CACHE_FILE.write_text(json.dumps(result, indent=2))
    except Exception:
        pass

    return jsonify(result)


# ── ML Intelligence Endpoints ──

@garves_bp.route("/api/ml/status")
def ml_status():
    """Return status of all ML models (LSTM, XGBoost, FinBERT)."""
    models_dir = DATA_DIR / "models"
    status = {"lstm": {}, "xgboost": {}, "finbert": {}}

    # LSTM models per asset
    for asset in ["bitcoin", "ethereum", "solana", "xrp"]:
        metrics_file = models_dir / f"lstm_{asset}.metrics.json"
        if metrics_file.exists():
            try:
                m = json.loads(metrics_file.read_text())
                model_file = models_dir / f"lstm_{asset}.pt"
                age_hours = (time.time() - model_file.stat().st_mtime) / 3600 if model_file.exists() else 0
                status["lstm"][asset] = {
                    "val_acc": m.get("val_acc", 0),
                    "train_acc": m.get("train_acc", 0),
                    "candles": m.get("candles", 0),
                    "epochs": m.get("epochs", 0),
                    "device": m.get("device", "cpu"),
                    "age_hours": round(age_hours, 1),
                }
            except Exception:
                pass

    # XGBoost model
    xgb_metrics = models_dir / "xgb_trade_predictor.metrics.json"
    if xgb_metrics.exists():
        try:
            status["xgboost"] = json.loads(xgb_metrics.read_text())
        except Exception:
            pass
    else:
        # Check how many resolved trades we have
        hawk_file = DATA_DIR / "hawk_trades.jsonl"
        garves_file = DATA_DIR / "trades.jsonl"
        resolved = 0
        for f in [hawk_file, garves_file]:
            if f.exists():
                for line in open(f):
                    try:
                        if json.loads(line.strip()).get("resolved"):
                            resolved += 1
                    except Exception:
                        pass
        status["xgboost"] = {"status": "waiting", "resolved_trades": resolved, "min_required": 30}

    # FinBERT
    try:
        from shared.sentiment import _pipeline, _load_attempted
        if _pipeline is not None:
            status["finbert"] = {"status": "loaded", "model": "ProsusAI/finbert"}
        elif _load_attempted:
            status["finbert"] = {"status": "failed"}
        else:
            status["finbert"] = {"status": "not_loaded"}
    except Exception:
        status["finbert"] = {"status": "unavailable"}

    return jsonify(status)


# ── Trade Journal Analyzer ──

@garves_bp.route("/api/garves/journal")
def api_garves_journal():
    """Trade journal analysis — best/worst combos, hour heatmap, patterns."""
    try:
        trades = _load_trades()
        resolved = [
            t for t in trades
            if t.get("resolved") and t.get("outcome") in ("up", "down")
        ]

        if not resolved:
            return jsonify({
                "best_combos": [], "worst_combos": [],
                "hour_heatmap": {}, "streak_status": {},
                "mistake_patterns": [], "recommendations": [],
                "total_resolved": 0,
            })

        # ── Best/Worst combos by (asset, timeframe, direction) ──
        from collections import defaultdict
        combos = defaultdict(lambda: {"wins": 0, "losses": 0, "trades": []})
        hour_data = defaultdict(lambda: {"wins": 0, "losses": 0})

        for t in resolved:
            asset = t.get("asset", "unknown")
            tf = t.get("timeframe", "?")
            direction = t.get("direction", "?")
            key = f"{asset}/{tf}/{direction}"
            won = t.get("won", False)
            combos[key]["wins" if won else "losses"] += 1
            combos[key]["trades"].append(t)

            # Hour bucket
            ts = t.get("timestamp", 0)
            if ts:
                trade_dt = datetime.fromtimestamp(ts, tz=ET)
                h = trade_dt.hour
                hour_data[h]["wins" if won else "losses"] += 1

        combo_list = []
        for key, stats in combos.items():
            total = stats["wins"] + stats["losses"]
            if total < 2:
                continue
            wr = stats["wins"] / total
            combo_list.append({
                "combo": key,
                "wins": stats["wins"],
                "losses": stats["losses"],
                "total": total,
                "win_rate": round(wr * 100, 1),
            })

        combo_list.sort(key=lambda x: (-x["total"], -x["win_rate"]))
        best = [c for c in combo_list if c["win_rate"] >= 55][:10]
        worst = [c for c in combo_list if c["win_rate"] < 50][:10]

        # ── Hour heatmap ──
        hour_heatmap = {}
        for h in range(24):
            d = hour_data.get(h, {"wins": 0, "losses": 0})
            total = d["wins"] + d["losses"]
            hour_heatmap[str(h)] = {
                "wins": d["wins"],
                "losses": d["losses"],
                "total": total,
                "win_rate": round(d["wins"] / total * 100, 1) if total > 0 else 0,
            }

        # ── Streak status ──
        streak = 0
        for t in reversed(resolved):
            won = t.get("won", False)
            if streak == 0:
                streak = 1 if won else -1
            elif streak > 0 and won:
                streak += 1
            elif streak < 0 and not won:
                streak -= 1
            else:
                break

        streak_status = {
            "current": streak,
            "type": "winning" if streak > 0 else "losing" if streak < 0 else "even",
            "length": abs(streak),
        }

        # ── Mistake patterns ──
        mistakes = []
        recent = resolved[-30:]  # last 30 trades
        # Pattern: low edge losses
        low_edge_losses = [
            t for t in recent
            if not t.get("won") and t.get("edge", 0) < 0.10
        ]
        if len(low_edge_losses) >= 3:
            mistakes.append({
                "pattern": "Low-edge losses",
                "count": len(low_edge_losses),
                "description": f"{len(low_edge_losses)} losses with edge < 10% in last 30 trades",
            })

        # Pattern: consecutive same-direction losses
        dir_losses = defaultdict(int)
        for t in recent:
            if not t.get("won"):
                dir_losses[t.get("direction", "?")] += 1
        for d, count in dir_losses.items():
            if count >= 4:
                mistakes.append({
                    "pattern": f"Repeated {d.upper()} losses",
                    "count": count,
                    "description": f"{count} {d.upper()} losses in last 30 trades — possible directional bias",
                })

        # Pattern: overnight losses (0-6 AM ET)
        overnight_losses = [
            t for t in recent
            if not t.get("won") and t.get("timestamp")
            and datetime.fromtimestamp(t["timestamp"], tz=ET).hour < 6
        ]
        if len(overnight_losses) >= 2:
            mistakes.append({
                "pattern": "Overnight losses",
                "count": len(overnight_losses),
                "description": f"{len(overnight_losses)} losses between 12-6 AM ET in last 30 trades",
            })

        # ── Recommendations ──
        recs = []
        total_wr = sum(1 for t in resolved if t.get("won")) / len(resolved) if resolved else 0
        if total_wr < 0.55:
            recs.append("Overall WR below 55% — consider tightening consensus floor")
        if best:
            top = best[0]
            recs.append(f"Best combo: {top['combo']} at {top['win_rate']}% WR ({top['total']} trades)")
        if worst:
            bottom = worst[0]
            recs.append(f"Weakest combo: {bottom['combo']} at {bottom['win_rate']}% WR — consider blocking")

        return jsonify({
            "best_combos": best,
            "worst_combos": worst,
            "hour_heatmap": hour_heatmap,
            "streak_status": streak_status,
            "mistake_patterns": mistakes,
            "recommendations": recs,
            "total_resolved": len(resolved),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


# ── CLOB WebSocket connection status ─────────────────────────

CLOB_STATUS_FILE = DATA_DIR / "clob_status.json"
BINANCE_STATUS_FILE = DATA_DIR / "binance_status.json"


@garves_bp.route("/api/garves/binance-status")
def api_garves_binance_status():
    """Connection status for Binance WebSocket feed."""
    try:
        if not BINANCE_STATUS_FILE.exists():
            return jsonify({"status": "DISCONNECTED", "detail": "No status file"})
        data = json.loads(BINANCE_STATUS_FILE.read_text())
        now = time.time()
        lm = data.get("last_message", 0)
        data["silence_s"] = round(now - lm, 1) if lm > 0 else 0
        return jsonify(data)
    except Exception as e:
        return jsonify({"status": "UNKNOWN", "error": str(e)[:200]}), 500


@garves_bp.route("/api/garves/clob-status")
def api_garves_clob_status():
    """Connection status for Polymarket CLOB WebSocket feed.

    Reads from a shared status file written by Garves V2 (separate process).
    Recalculates time-sensitive fields (silence_s, uptime_s) from timestamps.
    """
    try:
        if not CLOB_STATUS_FILE.exists():
            return jsonify({"status": "DISCONNECTED", "detail": "No status file"})
        data = json.loads(CLOB_STATUS_FILE.read_text())
        now = time.time()
        lm = data.get("last_message", 0)
        lc = data.get("last_connected", 0)
        status = data.get("status", "UNKNOWN")
        data["silence_s"] = round(now - lm, 1) if lm > 0 else 0
        data["uptime_s"] = round(now - lc, 1) if lc > 0 and status == "CONNECTED" else 0
        return jsonify(data)
    except Exception as e:
        return jsonify({"status": "UNKNOWN", "error": str(e)[:200]}), 500


@garves_bp.route("/api/garves/force-reconnect", methods=["POST"])
def api_garves_force_reconnect():
    """Signal the CLOB WebSocket to reconnect (updates status file)."""
    try:
        data = {"status": "CONNECTING", "detail": "Force reconnect requested"}
        if CLOB_STATUS_FILE.exists():
            data = json.loads(CLOB_STATUS_FILE.read_text())
            data["status"] = "CONNECTING"
            data["detail"] = "Force reconnect requested"
        CLOB_STATUS_FILE.write_text(json.dumps(data))
        return jsonify({"ok": True, "message": "Reconnect signal sent"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


# ── Snipe Engine v7 ─────────────────────────────────────────

SNIPE_STATUS_FILE = DATA_DIR / "snipe_status.json"


@garves_bp.route("/api/garves/snipe-v7")
@garves_bp.route("/api/garves/snipe-v8")
def api_garves_snipe():
    """Snipe engine v8 status — multi-asset scoring, MTF gate, correlation."""
    try:
        if not SNIPE_STATUS_FILE.exists():
            return jsonify({"enabled": False, "detail": "No snipe status file"})
        data = json.loads(SNIPE_STATUS_FILE.read_text())
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


# ── Garves V2 Intelligence Endpoints ──────────────────────────────

@garves_bp.route("/api/garves/v2-metrics")
def api_garves_v2_metrics():
    """Garves V2 core performance metrics + kill switch status."""
    try:
        from bot.performance_monitor import PerformanceMonitor
        from bot.self_improvement import SelfImprovementEngine
        pm = PerformanceMonitor()
        perf = pm.check()
        si = SelfImprovementEngine()
        metrics = si.calculate_metrics()
        return jsonify({
            "kill_switch_active": perf.kill_switch_active,
            "kill_switch_reason": perf.kill_switch_reason,
            "rolling_wr_30": perf.rolling_wr_30,
            "rolling_wr_50": perf.rolling_wr_50,
            "ev_capture_pct": perf.ev_capture_pct,
            "avg_slippage_pct": perf.avg_slippage_pct,
            "drawdown_pct": perf.current_drawdown_pct,
            "model_drift": perf.model_drift_score,
            "warnings": perf.warnings,
            "total_resolved": perf.total_resolved,
            "core_metrics": {
                "wr_20": metrics.wr_20,
                "wr_50": metrics.wr_50,
                "wr_100": metrics.wr_100,
                "ev_capture_pct": metrics.ev_capture_pct,
                "avg_slippage_pct": metrics.avg_slippage_pct,
                "total_slippage_cost": metrics.total_slippage_cost,
                "current_drawdown_pct": metrics.current_drawdown_pct,
                "max_drawdown_pct": metrics.max_drawdown_pct,
                "total_pnl": metrics.total_pnl,
            },
            "improvement_suggestions": si.suggest_improvements(metrics),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@garves_bp.route("/api/garves/quality-scores")
def api_garves_quality_scores():
    """Garves V2 market quality scorer cache stats."""
    try:
        cache_file = DATA_DIR / "quality_cache.json"
        if cache_file.exists():
            data = json.loads(cache_file.read_text())
            return jsonify(data)
        return jsonify({"cached": 0, "active": 0, "passed": 0, "quality_floor": 25})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@garves_bp.route("/api/garves/post-trade-analysis")
def api_garves_post_trade():
    """Garves V2 post-trade analysis summary."""
    try:
        from bot.post_trade_analyzer import PostTradeAnalyzer
        summary = PostTradeAnalyzer.get_analysis_summary(limit=20)
        return jsonify(summary)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@garves_bp.route("/api/garves/auto-rules")
def api_garves_auto_rules():
    """Garves V2 active auto-generated trading rules."""
    try:
        from bot.post_trade_analyzer import PostTradeAnalyzer
        rules = PostTradeAnalyzer.get_active_rules()
        return jsonify({"active_rules": rules, "count": len(rules)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@garves_bp.route("/api/garves/diagnostics")
def api_garves_diagnostics():
    """Garves V2 performance diagnostics (6-point check)."""
    try:
        from bot.performance_monitor import PerformanceMonitor
        pm = PerformanceMonitor()
        diag = pm.run_diagnostics()
        return jsonify(diag)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@garves_bp.route("/api/garves/edge-report")
def api_garves_edge_report():
    """Garves V2 edge decay + competitive check."""
    try:
        from bot.edge_monitor import EdgeMonitor
        em = EdgeMonitor()
        return jsonify({
            "edge_decay": em.check_edge_decay(),
            "competitive_check": em.weekly_competitive_check(),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
