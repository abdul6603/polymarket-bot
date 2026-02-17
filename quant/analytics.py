"""Quant Analytics — Kelly sizing, indicator diversity, strategy decay detection.

Provides advanced analysis beyond basic backtesting:
- Kelly criterion for optimal position sizing
- Indicator correlation/diversity analysis (detect redundant signals)
- Strategy decay detection with rolling WR monitoring
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)


# ─── Kelly Criterion Position Sizing ───

@dataclass
class KellyResult:
    """Kelly criterion position sizing recommendation."""
    win_rate: float = 0.0
    avg_win_return: float = 0.0
    avg_loss_return: float = 0.0
    full_kelly: float = 0.0      # optimal fraction of bankroll
    half_kelly: float = 0.0      # conservative (half Kelly)
    quarter_kelly: float = 0.0   # ultra-conservative
    current_size_usd: float = 10.0
    recommended_usd: float = 0.0
    bankroll: float = 250.0


def compute_kelly(
    wins: int,
    losses: int,
    avg_edge: float,
    bankroll: float = 250.0,
    current_size: float = 10.0,
) -> KellyResult:
    """Compute Kelly criterion optimal bet size for binary Polymarket markets.

    Kelly fraction = (bp - q) / b
    where:
      b = payout odds (profit per dollar risked on a win)
      p = probability of winning
      q = 1 - p

    For Polymarket binary outcomes at ~50/50 odds:
      b ≈ 1.0 (bet $0.50, get $1.00 if win = $0.50 profit per $0.50 risked)
      So Kelly simplifies to: f* = p - q = 2p - 1

    We use half-Kelly for safety (less bankroll variance).
    """
    total = wins + losses
    if total == 0:
        return KellyResult(bankroll=bankroll, current_size_usd=current_size)

    p = wins / total
    q = 1 - p

    # For near-50/50 binary markets, payout odds b ≈ 1.0
    # Kelly = (b*p - q) / b = p - q/b ≈ p - q = 2p - 1
    b = 1.0  # even money binary markets
    full_kelly = (b * p - q) / b
    full_kelly = max(0, min(full_kelly, 0.25))  # cap at 25% of bankroll

    half = full_kelly / 2
    quarter = full_kelly / 4

    recommended = half * bankroll  # half-Kelly dollar amount

    return KellyResult(
        win_rate=round(p * 100, 1),
        avg_win_return=round(avg_edge * 100, 1),
        avg_loss_return=100.0,
        full_kelly=round(full_kelly * 100, 1),
        half_kelly=round(half * 100, 1),
        quarter_kelly=round(quarter * 100, 1),
        current_size_usd=current_size,
        recommended_usd=round(recommended, 2),
        bankroll=bankroll,
    )


# ─── Indicator Diversity / Correlation Analysis ───

@dataclass
class DiversityResult:
    """Indicator diversity and correlation analysis."""
    n_indicators: int = 0
    avg_pairwise_agreement: float = 0.0  # high = redundant
    correlation_matrix: dict[str, dict[str, float]] = field(default_factory=dict)
    redundant_pairs: list[dict] = field(default_factory=list)  # pairs > 80% agreement
    independent_indicators: list[str] = field(default_factory=list)  # low avg correlation
    diversity_score: float = 0.0  # 0-100, higher = better diversity


def analyze_indicator_diversity(trades: list[dict]) -> DiversityResult:
    """Analyze indicator vote correlations to find redundant pairs.

    If two indicators always agree, having both inflates consensus count
    without adding real information. True diversity means indicators
    capture different aspects of the market.
    """
    result = DiversityResult()

    # Extract vote matrices: indicator -> list of directions per trade
    vote_matrix: dict[str, list[str]] = defaultdict(list)
    valid_trades = []

    for trade in trades:
        votes = trade.get("indicator_votes", {})
        if not votes:
            continue
        valid_trades.append(trade)
        for ind, direction in votes.items():
            vote_matrix[ind].append(direction)

    if not valid_trades or len(vote_matrix) < 2:
        return result

    indicators = sorted(vote_matrix.keys())
    result.n_indicators = len(indicators)

    # Build pairwise agreement matrix
    n_trades = len(valid_trades)
    agreement_matrix: dict[str, dict[str, float]] = {}
    all_agreements = []
    redundant = []

    for i, ind_a in enumerate(indicators):
        agreement_matrix[ind_a] = {}
        for j, ind_b in enumerate(indicators):
            if i == j:
                agreement_matrix[ind_a][ind_b] = 1.0
                continue
            if j < i:
                agreement_matrix[ind_a][ind_b] = agreement_matrix[ind_b][ind_a]
                continue

            # Count agreement
            agree_count = 0
            compare_count = 0
            for trade in valid_trades:
                votes = trade.get("indicator_votes", {})
                if ind_a in votes and ind_b in votes:
                    compare_count += 1
                    if votes[ind_a] == votes[ind_b]:
                        agree_count += 1

            agreement = agree_count / compare_count if compare_count > 0 else 0
            agreement_matrix[ind_a][ind_b] = round(agreement, 3)
            all_agreements.append(agreement)

            if agreement > 0.80 and compare_count >= 20:
                redundant.append({
                    "indicator_a": ind_a,
                    "indicator_b": ind_b,
                    "agreement": round(agreement * 100, 1),
                    "compared": compare_count,
                    "suggestion": f"Consider removing one — they agree {agreement*100:.0f}% of the time",
                })

    result.correlation_matrix = agreement_matrix
    result.redundant_pairs = sorted(redundant, key=lambda x: -x["agreement"])
    result.avg_pairwise_agreement = round(
        sum(all_agreements) / len(all_agreements) * 100, 1
    ) if all_agreements else 0.0

    # Find independent indicators (avg agreement < 60%)
    for ind in indicators:
        others = [agreement_matrix[ind].get(other, 0) for other in indicators if other != ind]
        avg = sum(others) / len(others) if others else 0
        if avg < 0.60:
            result.independent_indicators.append(ind)

    # Diversity score: lower avg pairwise agreement = better diversity
    # Perfect diversity = 50% agreement (random), perfect redundancy = 100%
    # Score = 100 * (1 - (avg_agreement - 0.5) / 0.5) clamped to [0, 100]
    avg_agr = result.avg_pairwise_agreement / 100
    diversity = max(0, min(100, 100 * (1 - (avg_agr - 0.5) / 0.5)))
    result.diversity_score = round(diversity, 1)

    return result


# ─── Strategy Decay Detection ───

@dataclass
class DecayResult:
    """Strategy decay detection — rolling win rate analysis."""
    is_decaying: bool = False
    current_rolling_wr: float = 0.0
    peak_rolling_wr: float = 0.0
    decay_amount: float = 0.0
    rolling_window: int = 20
    trend_direction: str = "stable"  # "improving", "stable", "decaying"
    rolling_history: list[dict] = field(default_factory=list)
    alert_message: str = ""


def detect_strategy_decay(
    trades: list[dict],
    rolling_window: int = 20,
    decay_threshold: float = 10.0,  # pp drop triggers alert
) -> DecayResult:
    """Detect strategy degradation by monitoring rolling win rate.

    Sorts trades by time, computes rolling WR, checks for declining trend.
    Alerts if current WR is >10pp below peak rolling WR.
    """
    result = DecayResult(rolling_window=rolling_window)

    # Sort by timestamp
    sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", 0))

    if len(sorted_trades) < rolling_window:
        result.alert_message = f"Need {rolling_window}+ trades for decay detection (have {len(sorted_trades)})"
        return result

    # Compute rolling win rate
    rolling_history = []
    outcomes = []
    peak_wr = 0.0

    for i, trade in enumerate(sorted_trades):
        won = 1 if trade.get("won") else 0
        outcomes.append(won)

        if i >= rolling_window - 1:
            window = outcomes[i - rolling_window + 1:i + 1]
            wr = sum(window) / len(window) * 100

            if wr > peak_wr:
                peak_wr = wr

            rolling_history.append({
                "trade_index": i,
                "timestamp": trade.get("timestamp", 0),
                "rolling_wr": round(wr, 1),
                "window_wins": sum(window),
                "window_losses": len(window) - sum(window),
            })

    if not rolling_history:
        return result

    current_wr = rolling_history[-1]["rolling_wr"]
    result.current_rolling_wr = current_wr
    result.peak_rolling_wr = round(peak_wr, 1)
    result.decay_amount = round(peak_wr - current_wr, 1)
    result.rolling_history = rolling_history

    # Trend detection: compare last 1/3 vs first 1/3
    n = len(rolling_history)
    if n >= 6:
        first_third = rolling_history[:n // 3]
        last_third = rolling_history[-(n // 3):]
        avg_first = sum(r["rolling_wr"] for r in first_third) / len(first_third)
        avg_last = sum(r["rolling_wr"] for r in last_third) / len(last_third)

        if avg_last > avg_first + 5:
            result.trend_direction = "improving"
        elif avg_last < avg_first - 5:
            result.trend_direction = "decaying"
        else:
            result.trend_direction = "stable"

    # Decay alert
    if result.decay_amount >= decay_threshold:
        result.is_decaying = True
        result.alert_message = (
            f"Strategy decay detected: rolling WR dropped {result.decay_amount:.0f}pp "
            f"from peak {peak_wr:.0f}% to {current_wr:.0f}%"
        )
    elif result.trend_direction == "decaying":
        result.is_decaying = True
        result.alert_message = (
            f"Declining trend: recent WR trending down (current {current_wr:.0f}%)"
        )
    else:
        result.alert_message = (
            f"Strategy healthy: {result.trend_direction} (current {current_wr:.0f}%)"
        )

    return result
