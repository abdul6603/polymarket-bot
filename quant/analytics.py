"""Quant Analytics — Kelly sizing, Monte Carlo, CUSUM, diversity, decay.

Provides advanced analysis beyond basic backtesting:
- Kelly criterion for optimal binary market position sizing
- Monte Carlo simulation (10K runs) for risk assessment
- CUSUM edge decay detection for real-time strategy monitoring
- Indicator correlation/diversity analysis (detect redundant signals)
- Strategy decay detection with rolling WR monitoring
"""
from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

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
    # V2 additions
    per_asset: dict[str, dict] = field(default_factory=dict)
    per_timeframe: dict[str, dict] = field(default_factory=dict)
    expected_pnl_per_trade: float = 0.0
    edge_adjusted: bool = False


def compute_kelly(
    wins: int,
    losses: int,
    avg_edge: float,
    bankroll: float = 250.0,
    current_size: float = 10.0,
    trades: list[dict] | None = None,
) -> KellyResult:
    """Compute Kelly criterion optimal bet size for binary Polymarket markets.

    For Polymarket binary outcomes where you buy at market_price p_m:
      Win payout = 1.0 (receive $1 per share)
      Cost = p_m per share
      Profit on win = (1 - p_m)
      Loss on loss = p_m

    Kelly fraction: f* = (p * (1 - p_m) - (1 - p) * p_m) / (1 - p_m)
                       = (p - p_m) / (1 - p_m)
    where p = estimated true probability of winning.

    We use half-Kelly for safety (reduces bankroll variance by 75%).
    When trades are provided, also computes per-asset and per-timeframe Kelly.
    """
    total = wins + losses
    if total == 0:
        return KellyResult(bankroll=bankroll, current_size_usd=current_size)

    p = wins / total

    # Binary market Kelly: f* = (p - p_m) / (1 - p_m)
    # Use avg_edge as proxy for (p - p_m) since edge = estimated_prob - market_price
    avg_market_price = 0.50  # default to 50/50 market
    if avg_edge > 0:
        # edge = p - p_m, so p_m = p - edge (roughly)
        avg_market_price = max(0.01, min(0.99, p - avg_edge))

    payout_on_win = 1.0 - avg_market_price   # profit per share
    loss_on_loss = avg_market_price           # cost per share

    if payout_on_win <= 0:
        return KellyResult(bankroll=bankroll, current_size_usd=current_size, win_rate=round(p * 100, 1))

    # Kelly = (p * payout - (1-p) * loss) / payout
    full_kelly = (p * payout_on_win - (1 - p) * loss_on_loss) / payout_on_win
    full_kelly = max(0, min(full_kelly, 0.25))  # cap at 25% of bankroll

    half = full_kelly / 2
    quarter = full_kelly / 4
    recommended = half * bankroll

    # Expected PNL per trade
    expected_pnl = (p * payout_on_win - (1 - p) * loss_on_loss) * recommended

    result = KellyResult(
        win_rate=round(p * 100, 1),
        avg_win_return=round(payout_on_win * 100, 1),
        avg_loss_return=round(loss_on_loss * 100, 1),
        full_kelly=round(full_kelly * 100, 1),
        half_kelly=round(half * 100, 1),
        quarter_kelly=round(quarter * 100, 1),
        current_size_usd=current_size,
        recommended_usd=round(recommended, 2),
        bankroll=bankroll,
        expected_pnl_per_trade=round(expected_pnl, 4),
        edge_adjusted=True,
    )

    # Per-asset and per-timeframe Kelly breakdown
    if trades:
        result.per_asset = _kelly_breakdown(trades, "asset", bankroll)
        result.per_timeframe = _kelly_breakdown(trades, "timeframe", bankroll)

    return result


def _kelly_breakdown(trades: list[dict], group_key: str, bankroll: float) -> dict[str, dict]:
    """Compute Kelly fraction for each unique value of group_key."""
    groups: dict[str, dict] = {}
    for t in trades:
        if not t.get("resolved"):
            continue
        key = t.get(group_key, "unknown")
        if key not in groups:
            groups[key] = {"wins": 0, "losses": 0, "edges": []}
        if t.get("won"):
            groups[key]["wins"] += 1
        else:
            groups[key]["losses"] += 1
        groups[key]["edges"].append(t.get("edge", 0))

    breakdown: dict[str, dict] = {}
    for key, data in groups.items():
        total = data["wins"] + data["losses"]
        if total < 5:
            continue
        p = data["wins"] / total
        avg_edge = sum(data["edges"]) / len(data["edges"]) if data["edges"] else 0
        avg_mp = max(0.01, min(0.99, p - avg_edge))
        payout = 1.0 - avg_mp
        loss = avg_mp
        if payout > 0:
            fk = max(0, min(0.25, (p * payout - (1 - p) * loss) / payout))
        else:
            fk = 0
        breakdown[key] = {
            "win_rate": round(p * 100, 1),
            "trades": total,
            "full_kelly_pct": round(fk * 100, 1),
            "half_kelly_pct": round(fk * 50, 1),
            "recommended_usd": round(fk / 2 * bankroll, 2),
        }
    return breakdown


# ─── Monte Carlo Risk Engine ───

@dataclass
class MonteCarloResult:
    """Results from Monte Carlo simulation of strategy performance."""
    n_simulations: int = 0
    n_trades_per_sim: int = 0
    # Drawdown statistics
    avg_max_drawdown_pct: float = 0.0
    median_max_drawdown_pct: float = 0.0
    worst_max_drawdown_pct: float = 0.0    # 99th percentile
    drawdown_95th_pct: float = 0.0
    # Ruin probability
    ruin_probability: float = 0.0          # % of sims that hit ruin threshold
    ruin_threshold_pct: float = 50.0       # default: 50% drawdown = ruin
    # Return statistics
    avg_final_pnl: float = 0.0
    median_final_pnl: float = 0.0
    pnl_95th_lower: float = 0.0           # 5th percentile (worst case)
    pnl_95th_upper: float = 0.0           # 95th percentile (best case)
    # Sharpe ratio (annualized)
    avg_sharpe: float = 0.0
    # Profitable simulations
    profitable_pct: float = 0.0
    # PNL distribution
    pnl_percentiles: dict[str, float] = field(default_factory=dict)
    elapsed_seconds: float = 0.0


def monte_carlo_simulate(
    trades: list[dict],
    n_simulations: int = 10_000,
    n_trades_per_sim: int | None = None,
    bankroll: float = 250.0,
    bet_size: float = 15.0,
    ruin_threshold_pct: float = 50.0,
    seed: int = 42,
) -> MonteCarloResult:
    """Run Monte Carlo simulation on historical trade outcomes.

    Resamples trades with replacement, simulates N equity curves,
    computes drawdown, ruin probability, and PNL distribution.

    Each simulation:
      1. Start with bankroll
      2. Pick n_trades_per_sim random trades (with replacement)
      3. For each trade: win → +edge*bet_size, loss → -loss_rate*bet_size
      4. Track max drawdown, final PNL
    """
    t0 = time.time()

    resolved = [t for t in trades if t.get("resolved") and t.get("won") is not None]
    if len(resolved) < 10:
        log.warning("Monte Carlo: only %d resolved trades (need 10+)", len(resolved))
        return MonteCarloResult(rejection_reason="Insufficient trades") if hasattr(MonteCarloResult, 'rejection_reason') else MonteCarloResult()

    if n_trades_per_sim is None:
        n_trades_per_sim = len(resolved)

    rng = np.random.RandomState(seed)

    # Pre-compute trade outcomes as numpy arrays for speed
    outcomes = []
    for t in resolved:
        edge = t.get("edge", 0.05)
        won = t.get("won", False)
        # Approximate market price from edge and win probability
        implied_price = t.get("implied_up_price", 0.5)
        if implied_price is None:
            implied_price = 0.5
        implied_price = max(0.01, min(0.99, implied_price))

        if won:
            pnl = (1.0 - implied_price) * bet_size * 0.98  # 2% winner fee
        else:
            pnl = -implied_price * bet_size
        outcomes.append(pnl)

    outcomes_arr = np.array(outcomes)

    # Run simulations
    max_drawdowns = np.zeros(n_simulations)
    final_pnls = np.zeros(n_simulations)
    ruin_count = 0
    sharpes = []

    for i in range(n_simulations):
        # Random trade sequence
        indices = rng.randint(0, len(outcomes_arr), size=n_trades_per_sim)
        trade_pnls = outcomes_arr[indices]

        # Equity curve
        equity = np.cumsum(trade_pnls)
        cummax = np.maximum.accumulate(equity)
        drawdowns = cummax - equity

        max_dd = float(drawdowns.max())
        max_dd_pct = (max_dd / bankroll) * 100 if bankroll > 0 else 0
        max_drawdowns[i] = max_dd_pct

        final_pnl = float(equity[-1])
        final_pnls[i] = final_pnl

        # Ruin check: did equity drop below ruin threshold?
        min_equity = float(equity.min())
        if min_equity < -(bankroll * ruin_threshold_pct / 100):
            ruin_count += 1

        # Per-sim Sharpe (annualized, assuming 3 trades/day, 365 days)
        if len(trade_pnls) > 1 and trade_pnls.std() > 0:
            daily_return = trade_pnls.mean() * 3  # 3 trades/day
            daily_vol = trade_pnls.std() * math.sqrt(3)
            sharpe = (daily_return / daily_vol) * math.sqrt(365)
            sharpes.append(sharpe)

    result = MonteCarloResult(
        n_simulations=n_simulations,
        n_trades_per_sim=n_trades_per_sim,
        avg_max_drawdown_pct=round(float(max_drawdowns.mean()), 1),
        median_max_drawdown_pct=round(float(np.median(max_drawdowns)), 1),
        worst_max_drawdown_pct=round(float(np.percentile(max_drawdowns, 99)), 1),
        drawdown_95th_pct=round(float(np.percentile(max_drawdowns, 95)), 1),
        ruin_probability=round(ruin_count / n_simulations * 100, 2),
        ruin_threshold_pct=ruin_threshold_pct,
        avg_final_pnl=round(float(final_pnls.mean()), 2),
        median_final_pnl=round(float(np.median(final_pnls)), 2),
        pnl_95th_lower=round(float(np.percentile(final_pnls, 5)), 2),
        pnl_95th_upper=round(float(np.percentile(final_pnls, 95)), 2),
        avg_sharpe=round(float(np.mean(sharpes)), 2) if sharpes else 0.0,
        profitable_pct=round(float((final_pnls > 0).sum()) / n_simulations * 100, 1),
        pnl_percentiles={
            "p5": round(float(np.percentile(final_pnls, 5)), 2),
            "p25": round(float(np.percentile(final_pnls, 25)), 2),
            "p50": round(float(np.percentile(final_pnls, 50)), 2),
            "p75": round(float(np.percentile(final_pnls, 75)), 2),
            "p95": round(float(np.percentile(final_pnls, 95)), 2),
        },
        elapsed_seconds=round(time.time() - t0, 3),
    )

    log.info("Monte Carlo (%d sims, %d trades): avg DD=%.1f%%, ruin=%.2f%%, "
             "avg PNL=$%.2f, Sharpe=%.2f, profitable=%.1f%% [%.3fs]",
             n_simulations, n_trades_per_sim,
             result.avg_max_drawdown_pct, result.ruin_probability,
             result.avg_final_pnl, result.avg_sharpe, result.profitable_pct,
             result.elapsed_seconds)

    return result


# ─── CUSUM Edge Decay Detection ───

@dataclass
class CUSUMResult:
    """CUSUM change-point detection for strategy edge decay."""
    change_detected: bool = False
    # CUSUM statistics
    cusum_pos: float = 0.0         # positive CUSUM (detects decline)
    cusum_neg: float = 0.0         # negative CUSUM (detects improvement)
    threshold: float = 5.0
    # Change point info
    change_point_index: int = -1
    change_point_timestamp: float = 0.0
    trades_since_change: int = 0
    # Pre/post change comparison
    pre_change_wr: float = 0.0
    post_change_wr: float = 0.0
    wr_drop_pp: float = 0.0
    # Current state
    current_rolling_wr: float = 0.0
    target_wr: float = 0.0
    severity: str = "none"         # "none", "warning", "critical"
    alert_message: str = ""
    # History for visualization
    cusum_history: list[dict] = field(default_factory=list)


def cusum_edge_decay(
    trades: list[dict],
    target_wr: float | None = None,
    threshold: float = 5.0,
    drift: float = 0.5,
    rolling_window: int = 30,
) -> CUSUMResult:
    """Detect strategy edge decay using Cumulative Sum (CUSUM) algorithm.

    CUSUM tracks cumulative deviations from a target win rate.
    When the cumulative sum exceeds a threshold, it signals a change point
    (the strategy has shifted from its expected behavior).

    Parameters:
      target_wr: Expected win rate (auto-estimated from first half if None)
      threshold: CUSUM alarm threshold (higher = fewer false alarms)
      drift: Allowable drift before accumulating (dampens noise)
      rolling_window: Window for current WR calculation

    Returns CUSUMResult with change detection info and severity.
    """
    result = CUSUMResult(threshold=threshold)

    sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", 0))
    resolved = [t for t in sorted_trades if t.get("resolved") and t.get("won") is not None]

    if len(resolved) < 20:
        result.alert_message = f"Need 20+ resolved trades for CUSUM (have {len(resolved)})"
        return result

    # Auto-estimate target WR from first half of data
    if target_wr is None:
        first_half = resolved[:len(resolved) // 2]
        wins_first = sum(1 for t in first_half if t.get("won"))
        target_wr = wins_first / len(first_half) * 100
    result.target_wr = round(target_wr, 1)

    # Convert trades to binary outcomes: 1=win, 0=loss
    outcomes = [1 if t.get("won") else 0 for t in resolved]
    target_frac = target_wr / 100.0

    # Run two-sided CUSUM
    cusum_pos = 0.0  # detects WR decline (positive accumulation of negative deviations)
    cusum_neg = 0.0  # detects WR improvement
    change_idx = -1
    cusum_history = []

    for i, outcome in enumerate(outcomes):
        deviation = outcome - target_frac

        # Upper CUSUM (detects decline: outcome consistently below target)
        cusum_pos = max(0, cusum_pos - deviation - drift / 100)
        # Lower CUSUM (detects improvement)
        cusum_neg = max(0, cusum_neg + deviation - drift / 100)

        # Record for visualization
        if i % max(1, len(outcomes) // 50) == 0 or i == len(outcomes) - 1:
            cusum_history.append({
                "index": i,
                "timestamp": resolved[i].get("timestamp", 0),
                "cusum_pos": round(cusum_pos, 3),
                "cusum_neg": round(cusum_neg, 3),
                "outcome": outcome,
            })

        # Check for change point (decline)
        if cusum_pos > threshold and change_idx == -1:
            change_idx = i

    result.cusum_pos = round(cusum_pos, 3)
    result.cusum_neg = round(cusum_neg, 3)
    result.cusum_history = cusum_history

    # Current rolling WR
    recent = outcomes[-rolling_window:] if len(outcomes) >= rolling_window else outcomes
    result.current_rolling_wr = round(sum(recent) / len(recent) * 100, 1)

    # Change point analysis
    if change_idx >= 0:
        result.change_detected = True
        result.change_point_index = change_idx
        result.change_point_timestamp = resolved[change_idx].get("timestamp", 0)
        result.trades_since_change = len(resolved) - change_idx

        # Pre/post change comparison
        pre = outcomes[:change_idx]
        post = outcomes[change_idx:]
        if pre:
            result.pre_change_wr = round(sum(pre) / len(pre) * 100, 1)
        if post:
            result.post_change_wr = round(sum(post) / len(post) * 100, 1)
        result.wr_drop_pp = round(result.pre_change_wr - result.post_change_wr, 1)

        # Severity classification
        if result.wr_drop_pp >= 15:
            result.severity = "critical"
            result.alert_message = (
                f"CRITICAL: Strategy edge collapsed {result.wr_drop_pp:.0f}pp "
                f"({result.pre_change_wr:.0f}% → {result.post_change_wr:.0f}%) "
                f"at trade #{change_idx}. Recommend pausing live trading."
            )
        elif result.wr_drop_pp >= 8:
            result.severity = "warning"
            result.alert_message = (
                f"WARNING: Edge decay detected ({result.wr_drop_pp:.0f}pp drop). "
                f"WR declined from {result.pre_change_wr:.0f}% to {result.post_change_wr:.0f}%. "
                f"Reduce position sizes."
            )
        else:
            result.severity = "warning"
            result.alert_message = (
                f"Mild edge decay: {result.wr_drop_pp:.0f}pp drop detected. Monitoring."
            )
    else:
        result.alert_message = f"Strategy stable: no significant decay detected (WR={result.current_rolling_wr:.0f}%)"

    log.info("CUSUM: change=%s pos=%.2f neg=%.2f target=%.1f%% current=%.1f%% severity=%s",
             result.change_detected, cusum_pos, cusum_neg,
             target_wr, result.current_rolling_wr, result.severity)

    return result


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
