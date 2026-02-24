"""Odin backtest scorer — computes performance metrics from simulated trades.

Calculates: PnL, win rate, Sharpe, max drawdown, profit factor,
R-multiple distribution, breakdown by regime/symbol/direction.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class OdinBacktestScore:
    """Comprehensive scoring of an Odin backtest run."""
    # Overview
    total_trades: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0

    # PnL
    total_pnl: float = 0.0
    avg_pnl_per_trade: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    profit_factor: float = 0.0

    # R-multiples
    avg_r: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0

    # Risk
    max_drawdown_usd: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    calmar_ratio: float = 0.0   # Annual return / max DD

    # Timing
    avg_hold_hours: float = 0.0
    avg_win_hold_hours: float = 0.0
    avg_loss_hold_hours: float = 0.0

    # Exit analysis
    exit_by_reason: dict = field(default_factory=dict)

    # Breakdowns
    by_regime: dict = field(default_factory=dict)
    by_symbol: dict = field(default_factory=dict)
    by_direction: dict = field(default_factory=dict)

    # Equity curve (balance over time)
    equity_curve: list = field(default_factory=list)

    # Per-trade R distribution (for histogram)
    r_distribution: list = field(default_factory=list)

    # Timestamps
    first_trade_time: float = 0.0
    last_trade_time: float = 0.0
    elapsed_seconds: float = 0.0

    # Candle data info
    symbols_tested: list = field(default_factory=list)
    total_candles: int = 0
    signals_generated: int = 0
    signals_filtered: int = 0


def score_odin_backtest(
    results: dict,
    starting_balance: float = 1000.0,
) -> OdinBacktestScore:
    """Score one or more OdinBacktestResult objects.

    Args:
        results: Dict of {symbol: OdinBacktestResult} from run_multi_asset_backtest,
                 or a single OdinBacktestResult wrapped in a dict.
        starting_balance: For drawdown calculation.

    Returns:
        OdinBacktestScore with all metrics computed.
    """
    score = OdinBacktestScore()

    # Collect all trades across assets
    all_trades = []
    for symbol, bt_result in results.items():
        score.symbols_tested.append(symbol)
        score.total_candles += bt_result.candles_used
        score.signals_generated += bt_result.signals_generated
        score.signals_filtered += bt_result.signals_filtered
        score.elapsed_seconds += bt_result.elapsed_seconds
        all_trades.extend(bt_result.trades)

    if not all_trades:
        return score

    # Sort by entry time
    all_trades.sort(key=lambda t: t.entry_time)

    score.total_trades = len(all_trades)
    score.first_trade_time = all_trades[0].entry_time
    score.last_trade_time = all_trades[-1].entry_time

    # Wins/losses
    wins = [t for t in all_trades if t.is_win]
    losses = [t for t in all_trades if not t.is_win]
    score.win_count = len(wins)
    score.loss_count = len(losses)
    score.win_rate = len(wins) / len(all_trades) * 100 if all_trades else 0

    # PnL
    pnls = [t.pnl_usd for t in all_trades]
    win_pnls = [t.pnl_usd for t in wins]
    loss_pnls = [t.pnl_usd for t in losses]

    score.total_pnl = round(sum(pnls), 2)
    score.avg_pnl_per_trade = round(float(np.mean(pnls)), 2) if pnls else 0
    score.avg_win = round(float(np.mean(win_pnls)), 2) if win_pnls else 0
    score.avg_loss = round(float(np.mean(loss_pnls)), 2) if loss_pnls else 0
    score.largest_win = round(max(pnls), 2) if pnls else 0
    score.largest_loss = round(min(pnls), 2) if pnls else 0

    # Profit factor
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    score.profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float(gross_profit > 0)

    # R-multiples
    r_mults = [t.r_multiple for t in all_trades]
    score.avg_r = round(float(np.mean(r_mults)), 2) if r_mults else 0
    score.avg_win_r = round(float(np.mean([t.r_multiple for t in wins])), 2) if wins else 0
    score.avg_loss_r = round(float(np.mean([t.r_multiple for t in losses])), 2) if losses else 0
    score.r_distribution = [round(r, 2) for r in r_mults]

    # Consecutive wins/losses
    score.max_consecutive_wins = _max_streak(all_trades, True)
    score.max_consecutive_losses = _max_streak(all_trades, False)

    # Equity curve + drawdown
    balance = starting_balance
    peak = balance
    max_dd_usd = 0
    max_dd_pct = 0
    curve = [{"balance": round(balance, 2), "time": score.first_trade_time}]

    for t in all_trades:
        balance += t.pnl_usd
        peak = max(peak, balance)
        dd_usd = peak - balance
        dd_pct = dd_usd / peak * 100 if peak > 0 else 0
        max_dd_usd = max(max_dd_usd, dd_usd)
        max_dd_pct = max(max_dd_pct, dd_pct)
        curve.append({
            "balance": round(balance, 2),
            "time": t.exit_time,
            "pnl": round(t.pnl_usd, 2),
        })

    score.equity_curve = curve
    score.max_drawdown_usd = round(max_dd_usd, 2)
    score.max_drawdown_pct = round(max_dd_pct, 1)

    # Sharpe ratio (annualized, using daily returns proxy)
    if len(pnls) >= 2:
        returns = np.array(pnls) / starting_balance
        mean_r = np.mean(returns)
        std_r = np.std(returns)
        if std_r > 0:
            # Annualize: assume ~1 trade per day on average
            days_span = (score.last_trade_time - score.first_trade_time) / 86400
            trades_per_day = len(all_trades) / max(1, days_span)
            annualize_factor = math.sqrt(252 * trades_per_day) if trades_per_day > 0 else math.sqrt(252)
            score.sharpe_ratio = round(float(mean_r / std_r * annualize_factor), 2)

    # Calmar ratio (annual return / max DD)
    if max_dd_pct > 0:
        days_span = (score.last_trade_time - score.first_trade_time) / 86400
        annual_return_pct = (score.total_pnl / starting_balance * 100) / max(1, days_span) * 365
        score.calmar_ratio = round(annual_return_pct / max_dd_pct, 2)

    # Hold time
    hold_times = [t.hold_hours for t in all_trades if t.hold_hours > 0]
    win_holds = [t.hold_hours for t in wins if t.hold_hours > 0]
    loss_holds = [t.hold_hours for t in losses if t.hold_hours > 0]
    score.avg_hold_hours = round(float(np.mean(hold_times)), 1) if hold_times else 0
    score.avg_win_hold_hours = round(float(np.mean(win_holds)), 1) if win_holds else 0
    score.avg_loss_hold_hours = round(float(np.mean(loss_holds)), 1) if loss_holds else 0

    # Exit reason breakdown
    for t in all_trades:
        reason = t.exit_reason
        if reason not in score.exit_by_reason:
            score.exit_by_reason[reason] = {"count": 0, "wins": 0, "pnl": 0}
        score.exit_by_reason[reason]["count"] += 1
        score.exit_by_reason[reason]["pnl"] += t.pnl_usd
        if t.is_win:
            score.exit_by_reason[reason]["wins"] += 1
    # Round PnL in exit reasons
    for v in score.exit_by_reason.values():
        v["pnl"] = round(v["pnl"], 2)

    # Regime breakdown
    score.by_regime = _breakdown(all_trades, lambda t: t.regime)
    score.by_symbol = _breakdown(all_trades, lambda t: t.symbol)
    score.by_direction = _breakdown(all_trades, lambda t: t.direction)

    return score


def _breakdown(trades: list, key_fn) -> dict:
    """Group trades by a key and compute stats per group."""
    groups: dict[str, list] = {}
    for t in trades:
        k = key_fn(t)
        if k not in groups:
            groups[k] = []
        groups[k].append(t)

    result = {}
    for k, group in groups.items():
        wins = sum(1 for t in group if t.is_win)
        pnl = sum(t.pnl_usd for t in group)
        result[k] = {
            "trades": len(group),
            "wins": wins,
            "losses": len(group) - wins,
            "win_rate": round(wins / len(group) * 100, 1) if group else 0,
            "pnl": round(pnl, 2),
            "avg_r": round(float(np.mean([t.r_multiple for t in group])), 2) if group else 0,
        }
    return result


def _max_streak(trades: list, is_win: bool) -> int:
    """Find maximum consecutive win/loss streak."""
    max_s = 0
    current = 0
    for t in trades:
        if t.is_win == is_win:
            current += 1
            max_s = max(max_s, current)
        else:
            current = 0
    return max_s


def write_odin_backtest_report(
    score: OdinBacktestScore,
    output_dir: Path,
) -> Path:
    """Write backtest score to JSON file for dashboard consumption."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "odin_backtest.json"

    report = {
        "overview": {
            "total_trades": score.total_trades,
            "win_count": score.win_count,
            "loss_count": score.loss_count,
            "win_rate": score.win_rate,
            "total_pnl": score.total_pnl,
            "profit_factor": score.profit_factor,
            "sharpe_ratio": score.sharpe_ratio,
            "calmar_ratio": score.calmar_ratio,
            "max_drawdown_pct": score.max_drawdown_pct,
            "max_drawdown_usd": score.max_drawdown_usd,
        },
        "pnl": {
            "avg_per_trade": score.avg_pnl_per_trade,
            "avg_win": score.avg_win,
            "avg_loss": score.avg_loss,
            "largest_win": score.largest_win,
            "largest_loss": score.largest_loss,
        },
        "r_multiples": {
            "avg_r": score.avg_r,
            "avg_win_r": score.avg_win_r,
            "avg_loss_r": score.avg_loss_r,
            "distribution": score.r_distribution,
        },
        "streaks": {
            "max_consecutive_wins": score.max_consecutive_wins,
            "max_consecutive_losses": score.max_consecutive_losses,
        },
        "timing": {
            "avg_hold_hours": score.avg_hold_hours,
            "avg_win_hold_hours": score.avg_win_hold_hours,
            "avg_loss_hold_hours": score.avg_loss_hold_hours,
        },
        "exit_analysis": score.exit_by_reason,
        "by_regime": score.by_regime,
        "by_symbol": score.by_symbol,
        "by_direction": score.by_direction,
        "equity_curve": score.equity_curve[-200:],  # Limit for dashboard
        "meta": {
            "symbols_tested": score.symbols_tested,
            "total_candles": score.total_candles,
            "signals_generated": score.signals_generated,
            "signals_filtered": score.signals_filtered,
            "elapsed_seconds": round(score.elapsed_seconds, 1),
            "first_trade": score.first_trade_time,
            "last_trade": score.last_trade_time,
        },
    }

    output_file.write_text(json.dumps(report, indent=2))
    log.info("[ODIN-BT] Wrote backtest report → %s (%d trades)", output_file, score.total_trades)
    return output_file
