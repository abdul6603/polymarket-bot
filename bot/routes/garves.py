"""Garves (trading) routes: /api/trades, /api/logs, /api/garves/*"""
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

garves_bp = Blueprint("garves", __name__)

MODE_FILE = DATA_DIR / "garves_mode.json"


@garves_bp.route("/api/garves/mode")
def api_garves_mode():
    """Current Garves trading mode."""
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
    """Toggle Garves between live and paper trading."""
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

    # PnL estimate
    total_pnl = 0.0
    stake = float(os.getenv("ORDER_SIZE_USD", "5.0"))
    for t in resolved:
        if t.get("outcome") == "unknown":
            continue
        implied = t.get("implied_up_price", 0.5)
        direction = t.get("direction", "up")
        entry_price = implied if direction == "up" else (1 - implied)
        if t.get("won"):
            total_pnl += stake * (1 - entry_price) - stake * 0.02
        else:
            total_pnl += -stake * entry_price

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
        if t.get("resolved"):
            if t.get("outcome") == "unknown":
                est_pnl = 0.0
            elif t.get("won"):
                est_pnl = stake * (1 - entry_price) - stake * 0.02
            else:
                est_pnl = -stake * entry_price
        else:
            est_pnl = stake * (1 - entry_price) - stake * 0.02
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
            "est_pnl": round(est_pnl, 2),
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
        implied = t.get("implied_up_price", 0.5)
        direction = t.get("direction", "up")
        entry_price = implied if direction == "up" else (1 - implied)
        if t.get("won"):
            total_pnl += stake * (1 - entry_price) - stake * 0.02
        else:
            total_pnl += -stake * entry_price

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
        if t.get("resolved"):
            if t.get("outcome") == "unknown":
                est_pnl = 0.0
            elif t.get("won"):
                est_pnl = stake * (1 - entry_price) - stake * 0.02
            else:
                est_pnl = -stake * entry_price
        else:
            est_pnl = stake * (1 - entry_price) - stake * 0.02
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
            "est_pnl": round(est_pnl, 2),
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
            implied = t.get("implied_up_price", 0.5)
            d = t.get("direction", "up")
            ep = implied if d == "up" else (1 - implied)
            if t.get("won"):
                pnl += stake * (1 - ep) - stake * 0.02
            else:
                pnl += -stake * ep

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
    result = {"funding_rates": {}, "liquidations": {}, "spot_depth": {}, "connected": False}
    try:
        from bot.derivatives_feed import DerivativesFeed
        # Read from the shared state file if the bot is running
        deriv_state_file = DATA_DIR / "derivatives_state.json"
        if deriv_state_file.exists():
            with open(deriv_state_file) as f:
                result = json.load(f)
    except Exception:
        pass

    # Also try spot depth from binance depth state
    try:
        depth_file = DATA_DIR / "spot_depth.json"
        if depth_file.exists():
            with open(depth_file) as f:
                result["spot_depth"] = json.load(f)
    except Exception:
        pass

    return jsonify(result)


@garves_bp.route("/api/garves/broadcasts")
def api_garves_broadcasts():
    """Process and acknowledge broadcasts for Garves."""
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
USDC_E_CONTRACT = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
POLYGON_RPC = "https://polygon-rpc.com"


def _fetch_usdc_balance(wallet: str) -> float:
    """Query on-chain USDC.e balance via Polygon RPC."""
    import urllib.request
    padded = "000000000000000000000000" + wallet[2:].lower()
    payload = json.dumps({
        "jsonrpc": "2.0", "method": "eth_call",
        "params": [{"to": USDC_E_CONTRACT, "data": "0x70a08231" + padded}, "latest"],
        "id": 1,
    })
    req = urllib.request.Request(POLYGON_RPC, data=payload.encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    return int(data.get("result", "0x0"), 16) / 1e6


def _fetch_position_value(wallet: str) -> float:
    """Get open position value from Polymarket data API."""
    import urllib.request
    url = f"https://data-api.polymarket.com/value?user={wallet.lower()}"
    req = urllib.request.Request(url, headers={"User-Agent": "Garves/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    if isinstance(data, list) and data:
        return float(data[0].get("value", 0))
    return 0.0


@garves_bp.route("/api/garves/balance")
def api_garves_balance():
    """Live Polymarket portfolio balance — on-chain USDC + position value."""
    # Check cache first
    if BALANCE_CACHE_FILE.exists():
        try:
            cached = json.loads(BALANCE_CACHE_FILE.read_text())
            if time.time() - cached.get("fetched_at", 0) < BALANCE_CACHE_TTL:
                return jsonify(cached)
        except Exception:
            pass

    bankroll = 250.0
    result = {"portfolio": 0.0, "cash": 0.0, "positions_value": 0.0,
              "pnl": 0.0, "bankroll": bankroll, "live": False, "error": None}

    wallet = POLYMARKET_WALLET
    errors = []

    # 1. On-chain USDC.e balance (cash)
    try:
        cash = _fetch_usdc_balance(wallet)
        result["cash"] = round(cash, 2)
        result["live"] = True
    except Exception as e:
        errors.append(f"USDC: {str(e)[:80]}")

    # 2. Open position value from data API
    try:
        pos_val = _fetch_position_value(wallet)
        result["positions_value"] = round(pos_val, 2)
        result["live"] = True
    except Exception as e:
        errors.append(f"Positions: {str(e)[:80]}")

    # 3. Compute portfolio and PnL
    if result["live"]:
        result["portfolio"] = round(result["cash"] + result["positions_value"], 2)
        result["pnl"] = round(result["portfolio"] - bankroll, 2)
    else:
        # Fallback: estimate from trade records
        trades = _load_trades()
        live_trades = [t for t in trades if not t.get("dry_run", True)]
        stake = float(os.getenv("ORDER_SIZE_USD", "5.0"))
        pnl, in_play = 0.0, 0.0
        for t in live_trades:
            implied = t.get("implied_up_price", 0.5)
            d = t.get("direction", "up")
            ep = implied if d == "up" else (1 - implied)
            if t.get("resolved"):
                if t.get("outcome") == "unknown":
                    continue
                if t.get("won"):
                    pnl += stake * (1 - ep) - stake * 0.02
                else:
                    pnl -= stake * ep
            else:
                in_play += stake
        result["portfolio"] = round(bankroll + pnl, 2)
        result["cash"] = round(bankroll + pnl - in_play, 2)
        result["pnl"] = round(pnl, 2)

    if errors:
        result["error"] = "; ".join(errors)

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
