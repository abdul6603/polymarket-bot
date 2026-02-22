"""Oracle report generator — builds the Weekly Crypto Outlook."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from oracle.config import OracleConfig
from oracle.data_pipeline import MarketContext
from oracle.edge_calculator import TradeSignal
from oracle.ensemble import EnsembleResult
from oracle.executor import OrderResult

log = logging.getLogger(__name__)


def generate_report(
    cfg: OracleConfig,
    context: MarketContext,
    ensemble: EnsembleResult,
    signals: list[TradeSignal],
    trades: list[TradeSignal],
    results: list[OrderResult],
    accuracy: dict[str, Any],
) -> str:
    """Generate the full Oracle Weekly Crypto Outlook report."""
    now = datetime.now(timezone.utc)
    week_start = now.strftime("%B %d")
    week_end = (now + timedelta(days=6)).strftime("%B %d")

    # Build sections
    lines = []
    lines.append(f"**Oracle Weekly Crypto Outlook**")
    lines.append(f"**Week of {week_start} - {week_end}**")
    lines.append(f"**Issued: {now.strftime('%B %d, %Y')} at {now.strftime('%H:%M')} UTC**")
    lines.append("")

    # Executive Summary
    top_trades = [s for s in signals if s.conviction in ("HIGH", "MEDIUM")][:3]
    regime = ensemble.regime.replace("_", " ").title()
    conf = ensemble.confidence * 100
    lines.append(f"**Executive Summary**")
    if top_trades:
        best = top_trades[0]
        lines.append(
            f"{regime} regime detected ({conf:.0f}% confidence). "
            f"Top edge: {best.market.asset.upper()} {best.side} with +{best.edge_abs*100:.1f}% edge."
        )
    else:
        lines.append(f"{regime} regime detected ({conf:.0f}% confidence). Limited edges found this week.")
    lines.append("")

    # Market Regime
    lines.append(f"**Market Regime**")
    lines.append(f"Current Regime: {regime}")
    lines.append(f"Confidence: {conf:.0f}%")
    if context.fear_greed is not None:
        lines.append(f"Fear & Greed: {context.fear_greed} ({context.fear_greed_label})")
    for asset, price in context.prices.items():
        chg = context.weekly_change_pct.get(asset, 0)
        lines.append(f"  {asset.upper()}: ${price:,.2f} ({chg:+.1f}% week)")
    lines.append("")

    # Key Weekly Questions table
    actionable = [s for s in signals if s.conviction != "SKIP"][:15]
    if actionable:
        lines.append(f"**Key Weekly Questions**")
        lines.append("")
        lines.append("| Question | Market | Oracle | Edge | Verdict |")
        lines.append("|----------|--------|--------|------|---------|")
        for s in actionable:
            q = s.market.question[:45]
            mkt = f"{s.market_prob*100:.0f}%"
            orc = f"{s.oracle_prob*100:.0f}%"
            edge = f"{'+' if s.edge > 0 else ''}{s.edge*100:.0f}%"
            verdict = f"{s.conviction} {s.side}"
            lines.append(f"| {q} | {mkt} | {orc} | {edge} | {verdict} |")
        lines.append("")

    # Why Oracle Believes This
    lines.append(f"**Why Oracle Believes This**")
    reasons = []
    if context.fear_greed is not None:
        if context.fear_greed < 25:
            reasons.append("Extreme fear readings historically precede reversals")
        elif context.fear_greed > 75:
            reasons.append("Greed levels suggest caution on bullish bets")
    if context.funding_rates:
        avg_fr = sum(context.funding_rates.values()) / len(context.funding_rates)
        if avg_fr > 0.02:
            reasons.append("Elevated funding rates signal crowded longs")
        elif avg_fr < -0.01:
            reasons.append("Negative funding suggests short-heavy positioning")
    if context.atlas_insights:
        reasons.append(f"Atlas intelligence: {context.atlas_insights[0][:100]}")
    if not reasons:
        reasons.append("According to all available data, current positioning is balanced")
    for r in reasons[:3]:
        lines.append(f"- {r}")
    lines.append("")

    # Trade Ideas
    if trades:
        lines.append(f"**Trades {'Executed' if not cfg.dry_run else 'Identified (DRY RUN)'}**")
        for t in trades:
            result = next((r for r in results if r.signal.market.condition_id == t.market.condition_id), None)
            status = "FILLED" if result and result.success else "PENDING"
            lines.append(
                f"- {t.side} {t.market.asset.upper()} | {t.market.question[:50]} | "
                f"${t.size:.0f} @ {t.market_prob*100:.0f}% | edge +{t.edge_abs*100:.1f}% | "
                f"conviction {t.conviction} | {status}"
            )
        total_wagered = sum(t.size for t in trades)
        total_ev = sum(t.expected_value for t in trades)
        lines.append(f"\nTotal wagered: ${total_wagered:.0f} | Expected value: ${total_ev:.2f}")
    else:
        lines.append("**No trades this week** — insufficient edge across all markets.")
    lines.append("")

    # Risk Radar
    lines.append(f"**Risk Radar**")
    if context.prices.get("bitcoin", 0) > 0:
        btc = context.prices["bitcoin"]
        lines.append(f"- BTC invalidation: drop below ${btc * 0.92:,.0f} (-8%) triggers mid-week review")
    lines.append("- Black swan watch: regulatory actions, exchange incidents, macro surprises")
    lines.append("")

    # Accuracy
    wr = accuracy.get("overall_win_rate", 0)
    total_pred = accuracy.get("total_predictions", 0)
    total_pnl = accuracy.get("total_pnl", 0)
    weeks = accuracy.get("weeks_tracked", 0)
    if total_pred > 0:
        lines.append(f"**Oracle Track Record** ({weeks} weeks)")
        lines.append(f"Win Rate: {wr:.1f}% | Total P&L: ${total_pnl:+.2f} | Predictions: {total_pred}")
    lines.append("")

    # Final Action Plan
    lines.append(f"**Final Action Plan**")
    if trades:
        best_trade = trades[0]
        lines.append(
            f"- If you do nothing else: {best_trade.side} {best_trade.market.asset.upper()} "
            f"({best_trade.market.question[:40]})"
        )
        lines.append(f"- Best move this week: let Oracle execute and monitor via dashboard")
    else:
        lines.append("- If you do nothing else: hold current positions, no action needed")
        lines.append("- Best move this week: wait for better edges next cycle")

    report = "\n".join(lines)
    log.info("Report generated: %d lines, %d trades", len(lines), len(trades))
    return report
