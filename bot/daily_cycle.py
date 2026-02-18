"""Garves — Daily Cycle Manager.

Every 24 hours (at midnight ET), archives the day's trading stats into a
daily report with performance analysis, strategy notes, and mistake tracking.
Then resets trades.jsonl for a fresh day.

Daily reports are stored in data/daily_reports.json.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from bot.bankroll import calculate_trade_pnl

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
DATA_DIR = Path(__file__).parent.parent / "data"
TRADES_FILE = DATA_DIR / "trades.jsonl"
DAILY_REPORTS_FILE = DATA_DIR / "daily_reports.json"
INDICATOR_ACCURACY_FILE = DATA_DIR / "indicator_accuracy.json"
LAST_RESET_FILE = DATA_DIR / "last_daily_reset.json"


def _load_trades() -> list[dict]:
    """Load all trades from trades.jsonl."""
    if not TRADES_FILE.exists():
        return []
    trades = []
    seen = set()
    try:
        with open(TRADES_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    t = json.loads(line)
                    tid = t.get("trade_id", "")
                    if tid not in seen:
                        seen.add(tid)
                        trades.append(t)
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        log.warning("Failed to read trades file: %s", e)
    return trades


def _analyze_mistakes(trades: list[dict], resolved: list[dict]) -> list[dict]:
    """Analyze the day's trading for mistakes and weaknesses."""
    mistakes = []
    stake = float(os.getenv("ORDER_SIZE_USD", "10.0"))

    losses = [t for t in resolved if not t.get("won")]
    wins = [t for t in resolved if t.get("won")]

    # 1. Check for low-edge trades that lost
    low_edge_losses = [t for t in losses if t.get("edge", 0) < 0.05]
    if low_edge_losses:
        mistakes.append({
            "type": "approach",
            "severity": "high",
            "title": "Low-Edge Trades Taken",
            "detail": f"{len(low_edge_losses)} losing trades had edge < 5%. "
                      f"ConvictionEngine should block these more aggressively.",
            "trades": [t.get("trade_id", "")[:12] for t in low_edge_losses[:3]],
        })

    # 2. Check for dead-zone trading (shouldn't happen with filter, but check)
    dead_zone_trades = []
    for t in resolved:
        ts = t.get("timestamp", 0)
        dt = datetime.fromtimestamp(ts, tz=ET)
        h = dt.hour
        if h < 8 or h >= 23:
            dead_zone_trades.append(t)
    if dead_zone_trades:
        mistakes.append({
            "type": "technical",
            "severity": "medium",
            "title": "Dead Zone Trades Leaked Through",
            "detail": f"{len(dead_zone_trades)} trades placed outside 8AM-11PM ET window.",
            "trades": [t.get("trade_id", "")[:12] for t in dead_zone_trades[:3]],
        })

    # 3. Check for losing streaks (3+ consecutive losses)
    streak = 0
    max_streak = 0
    sorted_resolved = sorted(resolved, key=lambda t: t.get("timestamp", 0))
    for t in sorted_resolved:
        if not t.get("won"):
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    if max_streak >= 3:
        mistakes.append({
            "type": "approach",
            "severity": "high",
            "title": f"Losing Streak of {max_streak}",
            "detail": f"Hit a {max_streak}-trade losing streak. Safety rails should have "
                      f"reduced sizing or paused trading after 3 consecutive losses.",
        })

    # 4. Check asset performance imbalance
    by_asset = {}
    for t in resolved:
        a = t.get("asset", "unknown")
        if a not in by_asset:
            by_asset[a] = {"wins": 0, "losses": 0}
        if t.get("won"):
            by_asset[a]["wins"] += 1
        else:
            by_asset[a]["losses"] += 1

    for asset, counts in by_asset.items():
        total = counts["wins"] + counts["losses"]
        if total >= 3:
            wr = counts["wins"] / total * 100
            if wr < 35:
                mistakes.append({
                    "type": "approach",
                    "severity": "high",
                    "title": f"{asset.upper()} Underperforming ({wr:.0f}% WR)",
                    "detail": f"{asset.upper()}: {counts['wins']}W-{counts['losses']}L ({wr:.0f}%). "
                              f"Consider reducing exposure or disabling this asset.",
                })

    # 5. Check timeframe performance
    by_tf = {}
    for t in resolved:
        tf = t.get("timeframe", "?")
        if tf not in by_tf:
            by_tf[tf] = {"wins": 0, "losses": 0}
        if t.get("won"):
            by_tf[tf]["wins"] += 1
        else:
            by_tf[tf]["losses"] += 1

    for tf, counts in by_tf.items():
        total = counts["wins"] + counts["losses"]
        if total >= 3:
            wr = counts["wins"] / total * 100
            if wr < 35:
                mistakes.append({
                    "type": "approach",
                    "severity": "medium",
                    "title": f"{tf} Timeframe Weak ({wr:.0f}% WR)",
                    "detail": f"{tf}: {counts['wins']}W-{counts['losses']}L. This timeframe "
                              f"may not suit current market conditions.",
                })

    # 6. Check direction bias
    by_dir = {}
    for t in resolved:
        d = t.get("direction", "?")
        if d not in by_dir:
            by_dir[d] = {"wins": 0, "losses": 0}
        if t.get("won"):
            by_dir[d]["wins"] += 1
        else:
            by_dir[d]["losses"] += 1

    for direction, counts in by_dir.items():
        total = counts["wins"] + counts["losses"]
        if total >= 3:
            wr = counts["wins"] / total * 100
            if wr < 35:
                mistakes.append({
                    "type": "approach",
                    "severity": "medium",
                    "title": f"'{direction.upper()}' Direction Weak ({wr:.0f}% WR)",
                    "detail": f"Predicting '{direction}' has {wr:.0f}% win rate. "
                              f"Signal engine may have a bias issue.",
                })

    # 7. Check for stale/unresolved trades (check full trades list, not resolved which filters these out)
    stale = [t for t in trades if t.get("resolved") and t.get("outcome") == "unknown"]
    if len(stale) > 3:
        mistakes.append({
            "type": "technical",
            "severity": "medium",
            "title": f"{len(stale)} Trades Marked Stale",
            "detail": f"{len(stale)} trades couldn't be resolved (API timeout or market issue). "
                      f"Resolution logic may need improvement.",
        })

    # 8. Check regime awareness
    regimes = {}
    for t in resolved:
        r = t.get("regime_label", "unknown")
        if r not in regimes:
            regimes[r] = {"wins": 0, "losses": 0}
        if t.get("won"):
            regimes[r]["wins"] += 1
        else:
            regimes[r]["losses"] += 1

    for regime, counts in regimes.items():
        total = counts["wins"] + counts["losses"]
        if total >= 3:
            wr = counts["wins"] / total * 100
            if wr < 40 and regime in ("extreme_fear", "extreme_greed"):
                mistakes.append({
                    "type": "approach",
                    "severity": "high",
                    "title": f"Poor Performance in {regime.replace('_', ' ').title()} ({wr:.0f}%)",
                    "detail": f"Win rate drops to {wr:.0f}% during {regime}. Regime adjustments "
                              f"may not be aggressive enough.",
                })

    # 9. If no mistakes found, note that
    if not mistakes:
        mistakes.append({
            "type": "info",
            "severity": "low",
            "title": "No Major Issues Detected",
            "detail": "Clean trading day. All systems performed within expected parameters.",
        })

    return mistakes


def generate_daily_report(date_str: str | None = None) -> dict:
    """Generate a daily performance report from current trades.jsonl."""
    now = datetime.now(ET)
    if not date_str:
        date_str = now.strftime("%Y-%m-%d")

    trades = _load_trades()
    stake = float(os.getenv("ORDER_SIZE_USD", "10.0"))

    resolved = [t for t in trades if t.get("resolved") and t.get("outcome") not in ("unknown", None)]
    pending = [t for t in trades if not t.get("resolved")]
    stale = [t for t in trades if t.get("resolved") and t.get("outcome") == "unknown"]
    wins = [t for t in resolved if t.get("won")]
    losses = [t for t in resolved if not t.get("won")]

    total_resolved = len(wins) + len(losses)
    win_rate = (len(wins) / total_resolved * 100) if total_resolved > 0 else 0

    # PnL calculation (uses shared Polymarket-accurate formula)
    total_pnl = 0.0
    for t in resolved:
        size_usd = t.get("size_usd") or stake
        prob = t.get("probability", 0.5)
        total_pnl += calculate_trade_pnl(
            won=t.get("won", False),
            probability=prob,
            size_usd=size_usd,
        )

    # By asset breakdown
    by_asset = {}
    for t in resolved:
        a = t.get("asset", "unknown")
        if a not in by_asset:
            by_asset[a] = {"wins": 0, "losses": 0, "pnl": 0.0}
        size_usd = t.get("size_usd") or stake
        prob = t.get("probability", 0.5)
        pnl = calculate_trade_pnl(won=t.get("won", False), probability=prob, size_usd=size_usd)
        if t.get("won"):
            by_asset[a]["wins"] += 1
        else:
            by_asset[a]["losses"] += 1
        by_asset[a]["pnl"] += pnl

    # By timeframe
    by_tf = {}
    for t in resolved:
        tf = t.get("timeframe", "?")
        if tf not in by_tf:
            by_tf[tf] = {"wins": 0, "losses": 0}
        if t.get("won"):
            by_tf[tf]["wins"] += 1
        else:
            by_tf[tf]["losses"] += 1

    # By direction
    by_direction = {}
    for t in resolved:
        d = t.get("direction", "?")
        if d not in by_direction:
            by_direction[d] = {"wins": 0, "losses": 0}
        if t.get("won"):
            by_direction[d]["wins"] += 1
        else:
            by_direction[d]["losses"] += 1

    # Avg edge and confidence
    avg_edge = sum(t.get("edge", 0) for t in resolved) / len(resolved) if resolved else 0
    avg_conf = sum(t.get("confidence", 0) for t in resolved) / len(resolved) if resolved else 0

    # Regime distribution
    regime_dist = {}
    for t in trades:
        r = t.get("regime_label", "unknown")
        regime_dist[r] = regime_dist.get(r, 0) + 1

    # Strategy summary
    strategy = {
        "engine": "11-indicator ensemble + ConvictionEngine",
        "conviction_scoring": "9-layer evidence (0-100)",
        "safety_rails": "losing streak 0.75x, low WR 0.7x, extreme fear 0.90x, $50 daily loss cap",
        "sizing": "conviction-based $25-$50",
        "assets": list(set(t.get("asset", "?") for t in trades)),
        "timeframes": list(set(t.get("timeframe", "?") for t in trades)),
        "regime": max(regime_dist, key=regime_dist.get) if regime_dist else "unknown",
    }

    # Analyze mistakes
    mistakes = _analyze_mistakes(trades, resolved)

    report = {
        "date": date_str,
        "generated_at": now.isoformat(),
        "summary": {
            "total_trades": len(trades),
            "resolved": total_resolved,
            "pending": len(pending),
            "stale": len(stale),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "pnl": round(total_pnl, 2),
            "avg_edge": round(avg_edge * 100, 2),
            "avg_confidence": round(avg_conf, 4),
        },
        "by_asset": {k: {**v, "pnl": round(v["pnl"], 2)} for k, v in by_asset.items()},
        "by_timeframe": by_tf,
        "by_direction": by_direction,
        "regime_distribution": regime_dist,
        "strategy": strategy,
        "mistakes": mistakes,
    }

    return report


def archive_and_reset() -> dict:
    """Archive today's trades into daily_reports.json and clear trades.jsonl."""
    report = generate_daily_report()

    # Load existing daily reports
    reports = []
    if DAILY_REPORTS_FILE.exists():
        try:
            with open(DAILY_REPORTS_FILE) as f:
                reports = json.load(f)
        except Exception:
            reports = []

    # Add this day's report
    reports.append(report)
    # Keep last 90 days
    reports = reports[-90:]

    # Save daily report
    try:
        with open(DAILY_REPORTS_FILE, "w") as f:
            json.dump(reports, f, indent=2)
    except OSError as e:
        log.error("Failed to save daily report: %s — aborting reset to prevent data loss", e)
        return report

    # Archive raw trades to a dated file
    date_str = report["date"]
    archive_file = DATA_DIR / "archives" / f"trades_{date_str}.jsonl"
    archive_file.parent.mkdir(parents=True, exist_ok=True)

    if TRADES_FILE.exists():
        import shutil
        shutil.copy2(TRADES_FILE, archive_file)
        log.info("Archived %d trades to %s", report["summary"]["total_trades"], archive_file)

    # Only clear trades.jsonl AFTER successful archive
    if archive_file.exists():
        with open(TRADES_FILE, "w") as f:
            pass  # empty file
    else:
        log.error("Archive file not created — skipping trades.jsonl clear to prevent data loss")

    # Record reset timestamp
    now = datetime.now(ET)
    with open(LAST_RESET_FILE, "w") as f:
        json.dump({
            "last_reset": now.isoformat(),
            "date": date_str,
            "archived_trades": report["summary"]["total_trades"],
        }, f, indent=2)

    log.info("Daily reset complete: archived %d trades for %s, trades.jsonl cleared",
             report["summary"]["total_trades"], date_str)

    return report


def should_reset() -> bool:
    """Check if it's time for a daily reset (past midnight ET, not yet reset today)."""
    now = datetime.now(ET)
    today = now.strftime("%Y-%m-%d")

    if LAST_RESET_FILE.exists():
        try:
            with open(LAST_RESET_FILE) as f:
                data = json.load(f)
            last_date = data.get("date", "")
            if last_date == today:
                return False  # Already reset today
        except Exception:
            pass

    # Check if there are any trades to archive
    if not TRADES_FILE.exists():
        return False
    if TRADES_FILE.stat().st_size < 10:
        return False  # Empty or near-empty

    return True


def get_daily_reports(limit: int = 30) -> list[dict]:
    """Get the daily reports history."""
    if not DAILY_REPORTS_FILE.exists():
        return []
    try:
        with open(DAILY_REPORTS_FILE) as f:
            reports = json.load(f)
        return reports[-limit:]
    except Exception:
        return []
