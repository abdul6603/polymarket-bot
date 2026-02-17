#!/usr/bin/env python3
"""Generate a performance report from trade history."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "trades.jsonl"
ET = ZoneInfo("America/New_York")


def load_trades() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    trades = []
    with open(DATA_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                trades.append(json.loads(line))
    return trades


def generate_report() -> str:
    trades = load_trades()
    now = datetime.now(ET)

    lines = []
    lines.append("=" * 70)
    lines.append(f"  GARVES — PERFORMANCE REPORT")
    lines.append(f"  Generated: {now.strftime('%B %d, %Y at %I:%M %p ET')}")
    lines.append("=" * 70)
    lines.append("")

    if not trades:
        lines.append("No trades recorded yet.")
        return "\n".join(lines)

    # Split into resolved and pending
    resolved = [t for t in trades if t.get("resolved") and t.get("outcome") not in ("", "unknown")]
    pending = [t for t in trades if not t.get("resolved")]
    stale = [t for t in trades if t.get("outcome") == "unknown"]
    total = len(trades)

    # ── Overall Stats ──
    lines.append("OVERALL SUMMARY")
    lines.append("-" * 40)
    lines.append(f"  Total signals generated:  {total}")
    lines.append(f"  Resolved:                 {len(resolved)}")
    lines.append(f"  Pending resolution:       {len(pending)}")
    lines.append(f"  Stale (unresolvable):     {len(stale)}")
    lines.append("")

    if resolved:
        wins = [t for t in resolved if t.get("won")]
        losses = [t for t in resolved if not t.get("won")]
        win_rate = len(wins) / len(resolved) * 100

        lines.append(f"  Wins:      {len(wins)}")
        lines.append(f"  Losses:    {len(losses)}")
        lines.append(f"  WIN RATE:  {win_rate:.1f}%")
        lines.append("")

        # Average edge on wins vs losses
        avg_edge_win = sum(t["edge"] for t in wins) / len(wins) * 100 if wins else 0
        avg_edge_loss = sum(t["edge"] for t in losses) / len(losses) * 100 if losses else 0
        avg_conf_win = sum(t["confidence"] for t in wins) / len(wins) if wins else 0
        avg_conf_loss = sum(t["confidence"] for t in losses) / len(losses) if losses else 0

        lines.append(f"  Avg edge on wins:    {avg_edge_win:.1f}%")
        lines.append(f"  Avg edge on losses:  {avg_edge_loss:.1f}%")
        lines.append(f"  Avg conf on wins:    {avg_conf_win:.2f}")
        lines.append(f"  Avg conf on losses:  {avg_conf_loss:.2f}")
        lines.append("")

        # ── By Asset ──
        lines.append("BY ASSET")
        lines.append("-" * 40)
        by_asset: dict[str, list] = defaultdict(list)
        for t in resolved:
            by_asset[t["asset"]].append(t)
        for asset in sorted(by_asset):
            at = by_asset[asset]
            aw = [t for t in at if t.get("won")]
            wr = len(aw) / len(at) * 100 if at else 0
            lines.append(f"  {asset.upper():12s}  {len(aw)}/{len(at)} wins  ({wr:.0f}%)")
        lines.append("")

        # ── By Timeframe ──
        lines.append("BY TIMEFRAME")
        lines.append("-" * 40)
        by_tf: dict[str, list] = defaultdict(list)
        for t in resolved:
            by_tf[t["timeframe"]].append(t)
        for tf in ["5m", "15m", "1h", "4h"]:
            if tf in by_tf:
                tt = by_tf[tf]
                tw = [t for t in tt if t.get("won")]
                wr = len(tw) / len(tt) * 100 if tt else 0
                lines.append(f"  {tf:6s}  {len(tw)}/{len(tt)} wins  ({wr:.0f}%)")
        lines.append("")

        # ── By Direction ──
        lines.append("BY DIRECTION")
        lines.append("-" * 40)
        for d in ["up", "down"]:
            dt = [t for t in resolved if t["direction"] == d]
            dw = [t for t in dt if t.get("won")]
            wr = len(dw) / len(dt) * 100 if dt else 0
            lines.append(f"  {d.upper():6s}  {len(dw)}/{len(dt)} wins  ({wr:.0f}%)")
        lines.append("")

        # ── Simulated P&L (assuming $5 per trade at predicted probability) ──
        lines.append("SIMULATED P&L (DRY RUN)")
        lines.append("-" * 40)
        total_pnl = 0.0
        for t in resolved:
            prob = t["probability"]
            cost = 5.00  # order_size_usd
            if t.get("won"):
                # Bought at prob, pays out $1/share, so profit = (1 - prob) * shares
                shares = cost / prob
                pnl = shares * 1.0 - cost  # payout - cost
            else:
                pnl = -cost  # lose the cost
            total_pnl += pnl
        lines.append(f"  Total simulated P&L:  ${total_pnl:+.2f}")
        lines.append(f"  Per-trade avg:        ${total_pnl / len(resolved):+.2f}")
        lines.append("")

    # ── Recent Trade Log ──
    lines.append("RECENT TRADES (last 20)")
    lines.append("-" * 70)
    lines.append(f"  {'Time':8s} {'Asset':10s} {'TF':4s} {'Dir':5s} {'Prob':6s} {'Edge':6s} {'Result':8s}")
    lines.append(f"  {'─'*8} {'─'*10} {'─'*4} {'─'*5} {'─'*6} {'─'*6} {'─'*8}")

    recent = sorted(trades, key=lambda t: t["timestamp"], reverse=True)[:20]
    for t in recent:
        ts = datetime.fromtimestamp(t["timestamp"], tz=ET).strftime("%H:%M")
        asset = t["asset"][:10].upper()
        tf = t["timeframe"]
        d = t["direction"].upper()
        prob = f"{t['probability']*100:.0f}%"
        edge = f"{t['edge']*100:.1f}%"
        if t.get("resolved"):
            if t.get("outcome") == "unknown":
                result = "STALE"
            elif t.get("won"):
                result = "WIN"
            else:
                result = "LOSS"
        else:
            result = "PENDING"
        lines.append(f"  {ts:8s} {asset:10s} {tf:4s} {d:5s} {prob:6s} {edge:6s} {result:8s}")

    lines.append("")
    lines.append("=" * 70)
    lines.append(f"  Next report: check data/trades.jsonl for live updates")
    lines.append("=" * 70)

    return "\n".join(lines)


if __name__ == "__main__":
    report = generate_report()
    print(report)

    # Also save to file
    report_file = Path(__file__).parent / "data" / "report.txt"
    report_file.parent.mkdir(exist_ok=True)
    with open(report_file, "w") as f:
        f.write(report)
    print(f"\nReport saved to: {report_file}")
