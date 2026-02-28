"""Odin trade analyzer + parameter optimizer.

Analyzes Odin's real paper/live trades to find optimal parameters.
Generates actionable recommendations for conviction thresholds,
symbol selection, exit tuning, and risk management.
"""
from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from pathlib import Path

log = logging.getLogger(__name__)

ODIN_TRADES_PATH = Path.home() / "odin" / "data" / "odin_trades.jsonl"


def load_odin_trades() -> list[dict]:
    """Load all Odin trades from JSONL."""
    if not ODIN_TRADES_PATH.exists():
        return []
    trades = []
    for line in ODIN_TRADES_PATH.read_text().splitlines():
        if line.strip():
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return trades


def analyze_odin_trades(trades: list[dict]) -> dict:
    """Deep analysis of Odin's real trade history.

    Returns comprehensive analysis dict with findings and recommendations.
    """
    if not trades:
        return {"error": "No trades to analyze", "recommendations": []}

    total = len(trades)
    wins = [t for t in trades if t.get("is_win")]
    losses = [t for t in trades if not t.get("is_win")]
    wr = len(wins) / total * 100
    total_pnl = sum(t.get("pnl_usd", 0) for t in trades)

    # ── Exit reason analysis ──
    by_exit = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        reason = t.get("exit_reason", "unknown")
        by_exit[reason]["trades"] += 1
        by_exit[reason]["pnl"] += t.get("pnl_usd", 0)
        if t.get("is_win"):
            by_exit[reason]["wins"] += 1
    for v in by_exit.values():
        v["pnl"] = round(v["pnl"], 2)
        v["wr"] = round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0

    # ── Symbol analysis ──
    by_symbol = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        sym = t.get("symbol", "unknown")
        by_symbol[sym]["trades"] += 1
        by_symbol[sym]["pnl"] += t.get("pnl_usd", 0)
        if t.get("is_win"):
            by_symbol[sym]["wins"] += 1
    for v in by_symbol.values():
        v["pnl"] = round(v["pnl"], 2)
        v["wr"] = round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0

    # ── Conviction score sweep ──
    score_sweep = {}
    for thresh in range(30, 101, 5):
        above = [t for t in trades if t.get("conviction_score", 0) >= thresh]
        if above:
            w = sum(1 for t in above if t.get("is_win"))
            pnl = sum(t.get("pnl_usd", 0) for t in above)
            score_sweep[thresh] = {
                "trades": len(above),
                "wins": w,
                "wr": round(w / len(above) * 100, 1),
                "pnl": round(pnl, 2),
            }

    # Find optimal conviction threshold (best WR with min 5 trades)
    best_thresh = 40
    best_wr = 0
    for thresh, data in score_sweep.items():
        if data["trades"] >= 5 and data["wr"] > best_wr:
            best_wr = data["wr"]
            best_thresh = thresh

    # ── Side analysis ──
    by_side = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        side = t.get("side", "unknown")
        by_side[side]["trades"] += 1
        by_side[side]["pnl"] += t.get("pnl_usd", 0)
        if t.get("is_win"):
            by_side[side]["wins"] += 1
    for v in by_side.values():
        v["pnl"] = round(v["pnl"], 2)
        v["wr"] = round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0

    # ── R-multiple analysis ──
    r_mults = [t.get("rr_ratio", 0) or t.get("actual_rr", 0) for t in trades]
    avg_r_win = 0
    avg_r_loss = 0
    if wins:
        avg_r_win = sum(t.get("rr_ratio", 0) or t.get("actual_rr", 0) for t in wins) / len(wins)
    if losses:
        avg_r_loss = sum(t.get("rr_ratio", 0) or t.get("actual_rr", 0) for t in losses) / len(losses)

    # ── Hold time analysis ──
    avg_hold = sum(t.get("hold_hours", 0) for t in trades) / total
    avg_hold_win = sum(t.get("hold_hours", 0) for t in wins) / len(wins) if wins else 0
    avg_hold_loss = sum(t.get("hold_hours", 0) for t in losses) / len(losses) if losses else 0

    # ── Worst symbols (blacklist candidates) ──
    blacklist = []
    for sym, data in by_symbol.items():
        if data["trades"] >= 2 and data["wr"] == 0 and data["pnl"] < -10:
            blacklist.append(sym)

    # ── Leverage analysis ──
    by_lev = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        lev = t.get("leverage", 1)
        by_lev[lev]["trades"] += 1
        by_lev[lev]["pnl"] += t.get("pnl_usd", 0)
        if t.get("is_win"):
            by_lev[lev]["wins"] += 1
    for v in by_lev.values():
        v["pnl"] = round(v["pnl"], 2)
        v["wr"] = round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0

    # ── Generate recommendations ──
    recommendations = []

    # 1. SL analysis
    sl_data = by_exit.get("SL", {})
    if sl_data.get("trades", 0) > 0 and sl_data.get("wr", 0) < 10:
        recommendations.append({
            "param": "STOP_LOSS_DISTANCE",
            "priority": "HIGH",
            "finding": f"SL exits: {sl_data['trades']} trades, {sl_data['wr']}% WR, ${sl_data['pnl']} PnL",
            "suggestion": "Widen stop losses — 100% of SL exits are losses. Current stops are too tight for market volatility.",
            "action": "Increase SL distance by 30-50% for scalp trades",
        })

    # 2. Conviction score
    if best_wr > wr + 5:
        recommendations.append({
            "param": "MIN_TRADE_SCORE",
            "priority": "MEDIUM",
            "finding": f"Current WR={wr:.0f}% across all trades. Score>={best_thresh} gives {best_wr:.0f}% WR",
            "suggestion": f"Raise MIN_TRADE_SCORE from current to {best_thresh}",
            "action": f"Set MIN_TRADE_SCORE={best_thresh} in conviction.py",
        })
    else:
        recommendations.append({
            "param": "MIN_TRADE_SCORE",
            "priority": "LOW",
            "finding": f"Conviction score has NO predictive power — WR is flat ({wr:.0f}%) across all thresholds",
            "suggestion": "The conviction scoring system needs recalibration. Current components don't predict outcomes.",
            "action": "Review conviction.py component weights — some inputs may be noise",
        })

    # 3. Symbol blacklist
    if blacklist:
        bl_pnl = sum(by_symbol[s]["pnl"] for s in blacklist)
        recommendations.append({
            "param": "SYMBOL_BLACKLIST",
            "priority": "HIGH",
            "finding": f"Symbols with 0% WR and >$10 loss: {', '.join(blacklist)} (total: ${bl_pnl:.2f})",
            "suggestion": f"Blacklist {len(blacklist)} symbols that are consistently losing",
            "action": f"Add to portfolio_blacklist.json: {blacklist}",
        })

    # 4. Core assets only
    core_symbols = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"}
    altcoin_trades = [t for t in trades if t.get("symbol") not in core_symbols]
    core_trades = [t for t in trades if t.get("symbol") in core_symbols]
    if len(altcoin_trades) > len(core_trades):
        alt_wr = sum(1 for t in altcoin_trades if t.get("is_win")) / len(altcoin_trades) * 100 if altcoin_trades else 0
        alt_pnl = sum(t.get("pnl_usd", 0) for t in altcoin_trades)
        recommendations.append({
            "param": "SYMBOL_WHITELIST",
            "priority": "HIGH",
            "finding": f"Altcoin trades: {len(altcoin_trades)} trades, {alt_wr:.0f}% WR, ${alt_pnl:.2f} PnL",
            "suggestion": "Focus on BTC/ETH/SOL/XRP only. Random altcoins are too volatile and unpredictable.",
            "action": "Restrict ODIN_SYMBOLS to BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT",
        })

    # 5. Side bias
    for side, data in by_side.items():
        if data["trades"] >= 3 and data["wr"] < 20:
            other_side = "LONG" if side == "SHORT" else "SHORT"
            recommendations.append({
                "param": f"{side}_BIAS",
                "priority": "MEDIUM",
                "finding": f"{side}: {data['trades']} trades, {data['wr']}% WR, ${data['pnl']} PnL",
                "suggestion": f"Consider reducing {side} exposure — significantly underperforming",
                "action": f"Add {side} penalty to conviction scoring or increase min score for {side}",
            })

    # 6. Time exits
    time_data = by_exit.get("TIME", {})
    if time_data.get("trades", 0) > 3 and time_data.get("wr", 100) < 40:
        recommendations.append({
            "param": "MAX_HOLD_TIME",
            "priority": "MEDIUM",
            "finding": f"TIME exits: {time_data['trades']} trades, {time_data['wr']}% WR, ${time_data['pnl']} PnL",
            "suggestion": "Most time-expired positions are losers. Consider shorter max hold or trailing SL activation.",
            "action": "Reduce SCALP max hold from 20min to 15min, or activate trailing SL earlier",
        })

    return {
        "summary": {
            "total_trades": total,
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": round(wr, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_r_win": round(avg_r_win, 2),
            "avg_r_loss": round(avg_r_loss, 2),
            "avg_hold_hours": round(avg_hold, 2),
            "avg_hold_win": round(avg_hold_win, 2),
            "avg_hold_loss": round(avg_hold_loss, 2),
        },
        "by_exit_reason": dict(by_exit),
        "by_symbol": dict(by_symbol),
        "by_side": dict(by_side),
        "by_leverage": dict(by_lev),
        "conviction_sweep": score_sweep,
        "optimal_conviction": best_thresh,
        "symbol_blacklist": blacklist,
        "recommendations": recommendations,
    }


def write_odin_recommendations(analysis: dict, output_dir: Path) -> Path:
    """Write Odin recommendations to JSON for dashboard + Odin consumption."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "quant_odin_recommendations.json"

    output = {
        "analysis": analysis,
        "recommendation_count": len(analysis.get("recommendations", [])),
        "updated": __import__("datetime").datetime.now().strftime("%Y-%m-%d %I:%M %p ET"),
    }

    output_file.write_text(json.dumps(output, indent=2))
    log.info("[QUANT-ODIN] Wrote %d recommendations → %s",
             len(analysis.get("recommendations", [])), output_file)
    return output_file
