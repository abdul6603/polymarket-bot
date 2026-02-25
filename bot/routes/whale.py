"""Whale Follower (Smart Money) routes: /api/whale/*"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from flask import Blueprint, jsonify

from bot.routes._utils import read_fresh

log = logging.getLogger(__name__)
whale_bp = Blueprint("whale", __name__)

DATA_DIR = Path.home() / "polymarket-bot" / "data"
STATUS_FILE = DATA_DIR / "whale_status.json"
COPY_TRADES_FILE = DATA_DIR / "whale_copy_trades.jsonl"


def _load_status() -> dict:
    data = read_fresh(STATUS_FILE, "~/polymarket-bot/data/whale_status.json")
    return data if data else {}


@whale_bp.route("/api/whale")
def api_whale():
    """Whale Follower overview — status, tracked wallets, performance."""
    status = _load_status()
    if not status:
        return jsonify({
            "enabled": False,
            "message": "Whale Follower not active (set WHALE_ENABLED=true)",
        })
    return jsonify(status)


@whale_bp.route("/api/whale/wallets")
def api_whale_wallets():
    """All tracked whale wallets with scores."""
    try:
        from bot.whale_follower import WalletDB
        db = WalletDB()
        tracked = db.get_tracked_wallets()
        all_wallets = db.get_all_wallets()
        blacklisted = [w for w in all_wallets if w.get("is_blacklisted")]

        return jsonify({
            "tracked": tracked[:20],
            "total_scanned": len(all_wallets),
            "blacklisted_count": len(blacklisted),
            "blacklisted": [
                {
                    "wallet": w["proxy_wallet"][:16],
                    "username": w.get("username", ""),
                    "reason": w.get("blacklist_reason", ""),
                    "score": w.get("composite_score", 0),
                }
                for w in blacklisted[:10]
            ],
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@whale_bp.route("/api/whale/copy-trades")
def api_whale_copy_trades():
    """Recent copy trade history."""
    try:
        from bot.whale_follower import WalletDB
        db = WalletDB()
        trades = db.get_copy_trades(limit=50)
        resolved = [t for t in trades if t["status"] in ("WON", "LOST")]
        wins = sum(1 for t in resolved if t["status"] == "WON")
        total_pnl = sum(t.get("pnl", 0) for t in resolved)

        return jsonify({
            "trades": trades,
            "summary": {
                "total": len(trades),
                "resolved": len(resolved),
                "wins": wins,
                "losses": len(resolved) - wins,
                "win_rate": round(wins / len(resolved) * 100, 1) if resolved else 0,
                "total_pnl": round(total_pnl, 2),
            },
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@whale_bp.route("/api/whale/signals")
def api_whale_signals():
    """Active whale signals (consensus and non-consensus)."""
    status = _load_status()
    return jsonify({
        "signals": status.get("signals", []),
        "active_count": status.get("active_signals", 0),
    })


@whale_bp.route("/api/whale/backtest")
def api_whale_backtest():
    """Latest backtest results."""
    status = _load_status()
    return jsonify(status.get("backtest", {"passed": False, "reason": "not_run"}))


@whale_bp.route("/api/whale/wallet-performance/<wallet_prefix>")
def api_whale_wallet_perf(wallet_prefix: str):
    """Copy trade performance for a specific whale wallet."""
    try:
        from bot.whale_follower import WalletDB
        db = WalletDB()
        all_wallets = db.get_all_wallets()
        # Find wallet by prefix match
        matched = [w for w in all_wallets if w["proxy_wallet"].startswith(wallet_prefix)]
        if not matched:
            return jsonify({"error": "Wallet not found"}), 404
        wallet = matched[0]["proxy_wallet"]
        perf = db.get_wallet_copy_performance(wallet)
        perf["wallet"] = wallet[:16]
        perf["username"] = matched[0].get("username", "")
        perf["score"] = matched[0].get("composite_score", 0)
        return jsonify(perf)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
