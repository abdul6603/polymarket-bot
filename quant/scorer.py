"""Score backtest results for ranking parameter combinations."""
from __future__ import annotations

import math

from quant.backtester import BacktestResult


def score_result(result: BacktestResult, min_trades: int = 20) -> float:
    """Score a backtest result on 0-100 scale.

    Components:
      40% — win rate (0-100 mapped to 0-40)
      20% — profit factor (capped at 3.0, mapped to 0-20)
      15% — average edge (capped at 15%, mapped to 0-15)
      15% — frequency / signal volume (want enough trades, log-scaled)
      10% — consistency (penalize losing streaks)

    Returns 0.0 if fewer than min_trades signals.
    """
    if result.total_signals < min_trades:
        return 0.0

    # Win rate component (40%)
    wr_score = min(result.win_rate, 100.0) / 100.0 * 40.0

    # Profit factor component (20%) — capped at 3.0
    pf = min(result.profit_factor, 3.0) / 3.0 * 20.0

    # Average edge component (15%) — higher edge = more profitable per trade
    # Capped at 15% edge (0.15)
    edge_raw = min(result.avg_edge / 0.15, 1.0) if result.avg_edge > 0 else 0.0
    edge_score = edge_raw * 15.0

    # Frequency component (15%) — more signals = better (log scale)
    # 20 trades = ~40%, 50 = ~70%, 100 = ~85%, 150+ = ~100%
    freq_raw = min(math.log(result.total_signals + 1) / math.log(160), 1.0)
    freq_score = freq_raw * 15.0

    # Consistency component (10%) — penalize losing streaks
    # 0 consec losses = full 10, 5+ = 0
    max_streak = result.max_consecutive_losses
    consec_penalty = min(max_streak / 5.0, 1.0)
    consistency_score = (1.0 - consec_penalty) * 10.0

    return wr_score + pf + edge_score + freq_score + consistency_score
