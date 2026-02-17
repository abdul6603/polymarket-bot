"""
GARVES — Performance Dashboard
Reads trades.jsonl and generates comprehensive reports.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
TRADES_FILE = DATA_DIR / "trades.jsonl"
ET = ZoneInfo("America/New_York")


def _load_trades() -> list[dict]:
    """Load all trades from JSONL file."""
    if not TRADES_FILE.exists():
        return []
    trades = []
    try:
        with open(TRADES_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                trades.append(json.loads(line))
    except Exception:
        return []
    return trades


def generate_report() -> str:
    """Generate a full performance report."""
    trades = _load_trades()
    if not trades:
        return "No trades recorded yet."

    total = len(trades)
    resolved = [t for t in trades if t.get("resolved") and t.get("outcome") not in ("", "unknown")]
    unresolved = [t for t in trades if not t.get("resolved")]
    stale = [t for t in trades if t.get("outcome") == "unknown"]

    lines = []
    lines.append("=" * 60)
    lines.append("  GARVES PERFORMANCE DASHBOARD")
    lines.append("=" * 60)
    lines.append("")

    # ── Overview ──
    lines.append(f"  Total trades: {total}")
    lines.append(f"  Resolved: {len(resolved)} | Pending: {len(unresolved)} | Stale: {len(stale)}")
    lines.append("")

    if not resolved:
        lines.append("  No resolved trades yet — waiting for market outcomes.")
        lines.append("")
        _add_pending_summary(lines, unresolved)
        return "\n".join(lines)

    # ── Win Rate ──
    wins = [t for t in resolved if t.get("won")]
    losses = [t for t in resolved if not t.get("won")]
    win_rate = len(wins) / len(resolved) * 100

    lines.append("  OVERALL RESULTS")
    lines.append("  " + "-" * 40)
    lines.append(f"  Win Rate: {win_rate:.1f}% ({len(wins)}W / {len(losses)}L)")
    lines.append("")

    # ── PnL Estimate ──
    # In prediction markets: win pays (1 - entry_price) * stake, loss costs entry_price * stake
    # Simplified: assume $5 per trade
    stake = 5.0
    total_pnl = 0.0
    for t in resolved:
        implied = t.get("implied_up_price", 0.5)
        direction = t.get("direction", "up")
        entry_price = implied if direction == "up" else (1 - implied)
        entry_price = max(0.01, min(0.99, entry_price))
        if t.get("won"):
            profit = stake * (1 - entry_price) - stake * 0.02  # minus 2% winner fee
            total_pnl += profit
        else:
            total_pnl -= stake * entry_price

    lines.append(f"  Est. PnL: ${total_pnl:+.2f} (on ${stake:.0f}/trade)")
    lines.append(f"  Avg Edge (predicted): {_avg(resolved, 'edge') * 100:.2f}%")
    lines.append(f"  Avg Confidence: {_avg(resolved, 'confidence'):.3f}")
    lines.append("")

    # ── By Asset ──
    lines.append("  BY ASSET")
    lines.append("  " + "-" * 40)
    by_asset = defaultdict(list)
    for t in resolved:
        by_asset[t.get("asset", "unknown")].append(t)

    for asset in sorted(by_asset.keys()):
        at = by_asset[asset]
        aw = sum(1 for t in at if t.get("won"))
        wr = aw / len(at) * 100
        lines.append(f"  {asset.upper():10s}  {wr:5.1f}%  ({aw}W/{len(at) - aw}L)  avg_edge={_avg(at, 'edge') * 100:.2f}%")
    lines.append("")

    # ── By Timeframe ──
    lines.append("  BY TIMEFRAME")
    lines.append("  " + "-" * 40)
    by_tf = defaultdict(list)
    for t in resolved:
        by_tf[t.get("timeframe", "?")].append(t)

    for tf in ["5m", "15m", "1h", "4h"]:
        if tf not in by_tf:
            continue
        tt = by_tf[tf]
        tw = sum(1 for t in tt if t.get("won"))
        wr = tw / len(tt) * 100
        lines.append(f"  {tf:10s}  {wr:5.1f}%  ({tw}W/{len(tt) - tw}L)  avg_edge={_avg(tt, 'edge') * 100:.2f}%")
    lines.append("")

    # ── Direction Analysis ──
    lines.append("  BY DIRECTION")
    lines.append("  " + "-" * 40)
    for d in ["up", "down"]:
        dt = [t for t in resolved if t.get("direction") == d]
        if not dt:
            continue
        dw = sum(1 for t in dt if t.get("won"))
        wr = dw / len(dt) * 100
        lines.append(f"  {d.upper():10s}  {wr:5.1f}%  ({dw}W/{len(dt) - dw}L)")
    lines.append("")

    # ── Edge vs Outcome Calibration ──
    lines.append("  CALIBRATION (predicted edge vs actual)")
    lines.append("  " + "-" * 40)
    # Bucket by edge: 0-2%, 2-5%, 5%+
    buckets = [("0-2%", 0, 0.02), ("2-5%", 0.02, 0.05), ("5%+", 0.05, 1.0)]
    for label, lo, hi in buckets:
        bt = [t for t in resolved if lo <= t.get("edge", 0) < hi]
        if not bt:
            continue
        bw = sum(1 for t in bt if t.get("won"))
        wr = bw / len(bt) * 100
        lines.append(f"  Edge {label:6s}  {wr:5.1f}%  ({len(bt)} trades)")
    lines.append("")

    # ── Recent Trades ──
    lines.append("  LAST 10 TRADES")
    lines.append("  " + "-" * 40)
    recent = sorted(resolved, key=lambda t: t.get("timestamp", 0), reverse=True)[:10]
    for t in recent:
        ts = datetime.fromtimestamp(t.get("timestamp", 0), tz=ET).strftime("%I:%M%p")
        result = "WIN " if t.get("won") else "LOSS"
        asset = t.get("asset", "?")[:3].upper()
        tf = t.get("timeframe", "?")
        edge = t.get("edge", 0) * 100
        lines.append(f"  {ts}  {result}  {asset}/{tf:3s}  {t.get('direction', '?'):4s}  edge={edge:+.1f}%")
    lines.append("")

    # ── Pending Trades ──
    _add_pending_summary(lines, unresolved)

    lines.append("=" * 60)
    return "\n".join(lines)


def _add_pending_summary(lines: list[str], unresolved: list[dict]):
    """Add pending trades summary."""
    if not unresolved:
        return
    lines.append(f"  PENDING TRADES ({len(unresolved)})")
    lines.append("  " + "-" * 40)
    for t in unresolved[-5:]:
        ts = datetime.fromtimestamp(t.get("timestamp", 0), tz=ET).strftime("%I:%M%p")
        asset = t.get("asset", "?")[:3].upper()
        tf = t.get("timeframe", "?")
        edge = t.get("edge", 0) * 100
        expires = datetime.fromtimestamp(t.get("market_end_time", 0), tz=ET).strftime("%I:%M%p")
        lines.append(f"  {ts}  {asset}/{tf:3s}  {t.get('direction', '?'):4s}  edge={edge:+.1f}%  expires={expires}")
    if len(unresolved) > 5:
        lines.append(f"  ... and {len(unresolved) - 5} more")
    lines.append("")


def _avg(trades: list[dict], key: str) -> float:
    """Average of a numeric field across trades."""
    vals = [t.get(key, 0) for t in trades if t.get(key) is not None]
    return sum(vals) / len(vals) if vals else 0.0


def main():
    """CLI entry point."""
    print(generate_report())


if __name__ == "__main__":
    main()
