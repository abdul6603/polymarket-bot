"""Odin (futures trading) routes: /api/odin/*"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request

from bot.routes._utils import read_fresh, read_fresh_jsonl

log = logging.getLogger(__name__)
odin_bp = Blueprint("odin", __name__)

ODIN_DIR = Path.home() / "odin"
DATA_DIR = ODIN_DIR / "data"
STATUS_FILE = DATA_DIR / "odin_status.json"
TRADES_FILE = DATA_DIR / "odin_trades.jsonl"
SIGNALS_FILE = DATA_DIR / "odin_signals.jsonl"
CB_FILE = DATA_DIR / "circuit_breaker.json"
OMNICOIN_FILE = DATA_DIR / "omnicoin_analysis.json"
MODE_FILE = DATA_DIR / "odin_mode.json"
ET = ZoneInfo("America/New_York")


def _load_status() -> dict:
    data = read_fresh(STATUS_FILE, "~/odin/data/odin_status.json")
    return data if data else {"running": False, "mode": "paper"}


def _load_trades() -> list[dict]:
    return read_fresh_jsonl(TRADES_FILE, "~/odin/data/odin_trades.jsonl")


def _load_signals() -> list[dict]:
    return read_fresh_jsonl(SIGNALS_FILE, "~/odin/data/odin_signals.jsonl")


@odin_bp.route("/api/odin/signal-cycle")
def api_odin_signal_cycle():
    """Signal cycle status for dashboard badge."""
    sc_file = DATA_DIR / "odin_signal_cycle.json"
    if sc_file.exists():
        try:
            data = json.loads(sc_file.read_text())
            data["age_s"] = round(time.time() - data.get("last_eval_at", 0), 1)
            return jsonify(data)
        except Exception:
            pass
    return jsonify({"last_eval_at": 0, "symbols_scanned": 0, "cycle_seconds": 300, "age_s": 999})


@odin_bp.route("/api/odin")
def api_odin():
    """Odin overview — status + stats."""
    status = _load_status()
    trades = _load_trades()

    wins = [t for t in trades if t.get("is_win")]
    losses = [t for t in trades if not t.get("is_win")]
    total_pnl = sum(t.get("pnl_usd", 0) for t in trades)
    win_rate = len(wins) / len(trades) * 100 if trades else 0

    return jsonify({
        **status,
        "trade_count": len(trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(win_rate, 1),
        "total_pnl_trades": round(total_pnl, 2),
    })


@odin_bp.route("/api/odin/trades")
def api_odin_trades():
    """Recent trade history."""
    trades = _load_trades()
    # Backfill mode for trades logged before the mode field was added
    for t in trades:
        if "mode" not in t:
            t["mode"] = "paper" if t.get("trade_id", "").startswith("paper_") else "live"
    return jsonify(trades[-50:] if len(trades) > 50 else trades)


@odin_bp.route("/api/odin/signals")
def api_odin_signals():
    """Recent signals (executed and filtered)."""
    signals = _load_signals()
    return jsonify(signals[-30:] if len(signals) > 30 else signals)


@odin_bp.route("/api/odin/positions")
def api_odin_positions():
    """Open paper positions."""
    status = _load_status()
    return jsonify(status.get("paper_positions", []))


@odin_bp.route("/api/odin/macro")
def api_odin_macro():
    """Current macro regime data."""
    status = _load_status()
    return jsonify(status.get("macro") or {
        "regime": "unknown", "score": 0, "multiplier": 0,
    })


@odin_bp.route("/api/odin/circuit-breaker")
def api_odin_circuit_breaker():
    """Circuit breaker state."""
    status = _load_status()
    return jsonify({
        "trading_allowed": status.get("trading_allowed", True),
        "reason": status.get("cb_reason", ""),
        "consecutive_losses": status.get("consecutive_losses", 0),
        "size_modifier": status.get("size_modifier", 1.0),
        "drawdown_pct": status.get("drawdown_pct", 0),
        "daily_pnl": status.get("daily_pnl", 0),
        "weekly_pnl": status.get("weekly_pnl", 0),
    })


@odin_bp.route("/api/odin/conviction")
def api_odin_conviction():
    """Last conviction breakdown."""
    status = _load_status()
    return jsonify(status.get("conviction") or {})


@odin_bp.route("/api/odin/journal")
def api_odin_journal():
    """Trade journal stats."""
    status = _load_status()
    return jsonify(status.get("journal_stats") or {})


@odin_bp.route("/api/odin/brotherhood")
def api_odin_brotherhood():
    """Brotherhood intelligence state."""
    status = _load_status()
    return jsonify(status.get("brotherhood") or {})


@odin_bp.route("/api/odin/skills")
def api_odin_skills():
    """All 13 skill statuses."""
    status = _load_status()
    return jsonify({
        "skills": status.get("skills", {}),
        "skill_count": status.get("skill_count", 0),
        "ob_memory": status.get("ob_memory", {}),
    })


@odin_bp.route("/api/odin/omnicoin", methods=["GET"])
def api_odin_omnicoin_get():
    """Get last OmniCoin analysis result."""
    data = read_fresh(OMNICOIN_FILE, "~/odin/data/omnicoin_analysis.json")
    return jsonify(data if data else {"symbol": None, "confidence": 0})


@odin_bp.route("/api/odin/omnicoin", methods=["POST"])
def api_odin_omnicoin_run():
    """Trigger OmniCoin analysis on a coin.

    Body: {"symbol": "BTC"} or {"symbol": "ETHUSDT"}
    Optional: {"chart_image": "base64..."} for Eye-Vision
    """
    body = request.get_json(silent=True) or {}
    symbol = body.get("symbol", "").strip().upper()
    if not symbol:
        return jsonify({"error": "symbol required"}), 400

    # Normalize
    if not symbol.endswith("USDT"):
        symbol = f"{symbol}USDT"

    chart_image = body.get("chart_image")

    try:
        import sys
        sys.path.insert(0, str(ODIN_DIR))
        from odin.skills.omnicoin import OmniCoinAnalyzer
        from odin.skills import SkillRegistry
        from odin.skills.ob_memory import OBMemory
        from odin.skills.sentiment_fusion import SentimentFusion
        from odin.skills.cross_chain_arb import CrossChainArbScout
        from odin.skills.eye_vision import EyeVision
        from odin.skills.stop_hunt_sim import StopHuntSimulator
        from odin.skills.liquidity_raid import LiquidityRaidPredictor
        from odin.skills.auto_reporter import AutoReporter
        from odin.skills.self_evolve import SelfEvolve

        registry = SkillRegistry()
        registry.register("ob_memory", OBMemory(DATA_DIR / "ob_memory.db"))
        registry.register("sentiment_fusion", SentimentFusion())
        registry.register("cross_chain_arb", CrossChainArbScout())
        registry.register("eye_vision", EyeVision(DATA_DIR))
        registry.register("stop_hunt_sim", StopHuntSimulator())
        registry.register("liquidity_raid", LiquidityRaidPredictor())
        registry.register("auto_reporter", AutoReporter(DATA_DIR))
        registry.register("self_evolve", SelfEvolve(DATA_DIR))

        analyzer = OmniCoinAnalyzer(registry, DATA_DIR)

        # Load regime data from status
        status = _load_status()
        regime_data = status.get("regime")

        # Run analysis
        report = analyzer.analyze(
            symbol=symbol,
            chart_image=chart_image,
            regime_data=regime_data,
        )

        return jsonify(report.to_dict())

    except Exception as e:
        log.error("[OMNICOIN] Analysis error: %s", str(e)[:300])
        return jsonify({"error": str(e)[:200], "symbol": symbol}), 500


@odin_bp.route("/api/odin/portfolio")
def api_odin_portfolio():
    """Portfolio Guard status — heat, direction balance, exposure, blacklist."""
    status = _load_status()
    pg = status.get("portfolio_guard", {})
    config = status.get("config", {})
    return jsonify({
        **pg,
        "coin_universe": config.get("coin_universe", 0),
        "detail_slots": config.get("detail_slots", 0),
        "symbols_per_cycle": config.get("symbols_per_cycle", 0),
    })


@odin_bp.route("/api/odin/pending-orders")
def api_odin_pending_orders():
    """Pending limit orders awaiting fill."""
    status = _load_status()
    return jsonify(status.get("pending_orders", []))


@odin_bp.route("/api/odin/ws-status")
def api_odin_ws_status():
    """WebSocket connection status."""
    status = _load_status()
    ws = status.get("ws_status", {})
    config = status.get("config", {})
    return jsonify({
        **ws,
        "ws_enabled": config.get("ws_enabled", False),
        "scaled_tranches": config.get("scaled_tranches", 1),
        "limit_ttl_s": config.get("limit_ttl_s", 7200),
    })


@odin_bp.route("/api/odin/toggle-mode", methods=["POST"])
def api_odin_toggle_mode():
    """Toggle Odin between live and paper trading."""
    current_mode = "paper"
    if MODE_FILE.exists():
        try:
            current_mode = json.loads(MODE_FILE.read_text()).get("mode", "paper")
        except Exception:
            pass

    new_mode = "live" if current_mode == "paper" else "paper"
    DATA_DIR.mkdir(exist_ok=True)
    MODE_FILE.write_text(json.dumps({
        "mode": new_mode,
        "toggled_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))
    return jsonify({"success": True, "mode": new_mode})
