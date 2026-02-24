"""PNL Impact Estimator — project dollar impact of parameter changes.

Replays historical trades with current vs proposed parameters to calculate:
  - Net trade difference (gained / lost signals)
  - Expected PNL per trade, per day, per month
  - Per-asset and per-timeframe breakdown
  - Which individual parameter changes contribute most

Used by the dashboard and live_push to show concrete $ impact before applying.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass
class TradeImpact:
    """Impact analysis for a single trade under old vs new params."""
    trade_id: str = ""
    asset: str = ""
    timeframe: str = ""
    direction: str = ""
    won: bool = False
    pnl: float = 0.0
    edge: float = 0.0
    status: str = ""  # "kept", "gained", "lost"


@dataclass
class PNLImpact:
    """Full PNL impact analysis comparing two parameter sets."""
    # Trade-level
    trades_kept: int = 0        # passed both old and new filters
    trades_gained: int = 0      # blocked by old, passed by new
    trades_lost: int = 0        # passed by old, blocked by new
    net_trade_change: int = 0   # gained - lost
    # PNL
    baseline_pnl: float = 0.0       # total PNL under current params
    proposed_pnl: float = 0.0       # total PNL under proposed params
    pnl_delta: float = 0.0          # proposed - baseline
    pnl_per_trade: float = 0.0      # average PNL per signal
    daily_pnl: float = 0.0          # projected daily PNL
    monthly_pnl: float = 0.0        # projected monthly PNL
    # Win rates
    baseline_wr: float = 0.0
    proposed_wr: float = 0.0
    wr_delta: float = 0.0
    # Breakdown
    by_asset: dict[str, dict] = field(default_factory=dict)
    by_timeframe: dict[str, dict] = field(default_factory=dict)
    # Per-param attribution (which param change has most impact)
    param_attribution: list[dict] = field(default_factory=list)
    # Gained/lost trade details
    gained_trades: list[dict] = field(default_factory=list)
    lost_trades: list[dict] = field(default_factory=list)
    # Metadata
    trades_analyzed: int = 0
    elapsed_seconds: float = 0.0


def estimate_pnl_impact(
    trades: list[dict],
    current_params: dict | None = None,
    proposed_params: dict | None = None,
    avg_bet_size: float = 10.0,
    trades_per_day: float = 3.0,
) -> PNLImpact:
    """Estimate PNL impact of switching from current to proposed parameters.

    Replays all historical trades through both parameter sets and compares.

    Args:
        trades: Historical trade records.
        current_params: Current live params (loads from file if None).
        proposed_params: Proposed optimized params.
        avg_bet_size: Average bet size in USD for PNL calculation.
        trades_per_day: Estimated trades per day for projection.

    Returns:
        PNLImpact with full breakdown.
    """
    start = time.time()
    result = PNLImpact(trades_analyzed=len(trades))

    if not trades or not proposed_params:
        return result

    from quant.backtester import replay_historical_trades, BacktestParams
    from quant.optimizer import get_live_params

    # Build baseline params
    if current_params is None:
        base_bp = get_live_params()
        base_bp.label = "baseline"
        # Convert to dict for comparison and merging
        current_params = _backtest_params_to_dict(base_bp)
    else:
        base_bp = _dict_to_backtest_params(current_params, "baseline")

    prop_bp = _dict_to_backtest_params(
        {**current_params, **proposed_params}, "proposed"
    )

    # Replay with both param sets
    base_result = replay_historical_trades(trades, base_bp)
    prop_result = replay_historical_trades(trades, prop_bp)

    # Build per-trade signal maps for comparison
    base_signals = _get_signal_set(trades, base_bp)
    prop_signals = _get_signal_set(trades, prop_bp)

    # Classify each trade
    asset_stats: dict[str, dict] = {}
    tf_stats: dict[str, dict] = {}
    trade_pnl_map: dict[str, float] = {}  # trade_id -> estimated PNL

    for t in trades:
        tid = t.get("trade_id", "")
        in_base = tid in base_signals
        in_prop = tid in prop_signals
        won = t.get("won", False)
        pnl = t.get("pnl", 0.0)
        asset = t.get("asset", "unknown")
        tf = t.get("timeframe", "unknown")

        # Estimate PNL if not recorded (most Polymarket trades don't have pnl)
        if pnl == 0.0:
            edge = t.get("edge", 0.0)
            if edge > 0:
                pnl = avg_bet_size * edge if won else -avg_bet_size * (1 - edge)
            else:
                pnl = avg_bet_size * 0.05 if won else -avg_bet_size * 0.95
        trade_pnl_map[tid] = pnl

        if in_base and in_prop:
            result.trades_kept += 1
            status = "kept"
        elif not in_base and in_prop:
            result.trades_gained += 1
            status = "gained"
            result.gained_trades.append({
                "trade_id": tid[:16],
                "asset": asset,
                "timeframe": tf,
                "won": won,
                "pnl": round(pnl, 2),
            })
        elif in_base and not in_prop:
            result.trades_lost += 1
            status = "lost"
            result.lost_trades.append({
                "trade_id": tid[:16],
                "asset": asset,
                "timeframe": tf,
                "won": won,
                "pnl": round(pnl, 2),
            })
        else:
            continue  # filtered by both

        # Aggregate by asset
        if asset not in asset_stats:
            asset_stats[asset] = {
                "base_pnl": 0, "prop_pnl": 0,
                "base_wins": 0, "base_total": 0,
                "prop_wins": 0, "prop_total": 0,
            }
        if in_base:
            asset_stats[asset]["base_total"] += 1
            asset_stats[asset]["base_pnl"] += pnl
            if won:
                asset_stats[asset]["base_wins"] += 1
        if in_prop:
            asset_stats[asset]["prop_total"] += 1
            asset_stats[asset]["prop_pnl"] += pnl
            if won:
                asset_stats[asset]["prop_wins"] += 1

        # Aggregate by timeframe
        if tf not in tf_stats:
            tf_stats[tf] = {
                "base_pnl": 0, "prop_pnl": 0,
                "base_wins": 0, "base_total": 0,
                "prop_wins": 0, "prop_total": 0,
            }
        if in_base:
            tf_stats[tf]["base_total"] += 1
            tf_stats[tf]["base_pnl"] += pnl
            if won:
                tf_stats[tf]["base_wins"] += 1
        if in_prop:
            tf_stats[tf]["prop_total"] += 1
            tf_stats[tf]["prop_pnl"] += pnl
            if won:
                tf_stats[tf]["prop_wins"] += 1

    # Calculate totals
    result.net_trade_change = result.trades_gained - result.trades_lost
    result.baseline_wr = base_result.win_rate
    result.proposed_wr = prop_result.win_rate
    result.wr_delta = round(prop_result.win_rate - base_result.win_rate, 1)

    # PNL from estimated trade PNL
    result.baseline_pnl = round(
        sum(trade_pnl_map.get(tid, 0) for tid in base_signals),
        2,
    )
    result.proposed_pnl = round(
        sum(trade_pnl_map.get(tid, 0) for tid in prop_signals),
        2,
    )
    result.pnl_delta = round(result.proposed_pnl - result.baseline_pnl, 2)

    # Per-trade and projected PNL
    prop_total = result.trades_kept + result.trades_gained
    if prop_total > 0:
        result.pnl_per_trade = round(result.proposed_pnl / prop_total, 2)
    result.daily_pnl = round(result.pnl_per_trade * trades_per_day, 2)
    result.monthly_pnl = round(result.daily_pnl * 30, 2)

    # Build asset breakdown
    for asset, s in sorted(asset_stats.items()):
        delta = s["prop_pnl"] - s["base_pnl"]
        result.by_asset[asset] = {
            "base_signals": s["base_total"],
            "prop_signals": s["prop_total"],
            "base_wr": round(s["base_wins"] / s["base_total"] * 100, 1) if s["base_total"] else 0,
            "prop_wr": round(s["prop_wins"] / s["prop_total"] * 100, 1) if s["prop_total"] else 0,
            "pnl_delta": round(delta, 2),
        }

    # Build timeframe breakdown
    for tf, s in sorted(tf_stats.items()):
        delta = s["prop_pnl"] - s["base_pnl"]
        result.by_timeframe[tf] = {
            "base_signals": s["base_total"],
            "prop_signals": s["prop_total"],
            "base_wr": round(s["base_wins"] / s["base_total"] * 100, 1) if s["base_total"] else 0,
            "prop_wr": round(s["prop_wins"] / s["prop_total"] * 100, 1) if s["prop_total"] else 0,
            "pnl_delta": round(delta, 2),
        }

    # Per-param attribution: test each param change individually
    result.param_attribution = _attribute_params(
        trades, current_params, proposed_params, base_signals
    )

    # Limit gained/lost lists for dashboard
    result.gained_trades = result.gained_trades[:10]
    result.lost_trades = result.lost_trades[:10]

    result.elapsed_seconds = round(time.time() - start, 2)
    return result


def _backtest_params_to_dict(bp) -> dict:
    """Convert a BacktestParams object to a flat dict for comparison."""
    return {
        "min_confidence": bp.min_confidence,
        "up_confidence_premium": bp.up_confidence_premium,
        "min_edge_absolute": bp.min_edge_absolute,
        "min_consensus": bp.min_consensus,
        "weights": dict(bp.weights) if bp.weights else {},
    }


def _dict_to_backtest_params(params: dict, label: str = ""):
    """Convert a flat param dict to BacktestParams."""
    from quant.backtester import BacktestParams

    bp = BacktestParams(label=label)
    if "min_confidence" in params:
        bp.min_confidence = params["min_confidence"]
    if "up_confidence_premium" in params:
        bp.up_confidence_premium = params["up_confidence_premium"]
    if "min_edge_absolute" in params:
        bp.min_edge_absolute = params["min_edge_absolute"]
    if "min_consensus" in params or "consensus_floor" in params:
        bp.min_consensus = params.get("min_consensus", params.get("consensus_floor", bp.min_consensus))
    if "weights" in params:
        bp.weights = params["weights"]
    return bp


def _get_signal_set(trades: list[dict], params) -> set[str]:
    """Get set of trade IDs that would pass the given params filter."""
    from quant.backtester import replay_historical_trades

    result = replay_historical_trades(trades, params)
    # Build set from trades that generated signals
    passed = set()
    for t in trades:
        tid = t.get("trade_id", "")
        if not tid:
            continue
        # Check if this trade's asset/timeframe combo appears in signal stats
        # The backtester doesn't return per-trade pass/fail, so we re-check
        if _would_pass_filter(t, params):
            passed.add(tid)
    return passed


def _would_pass_filter(trade: dict, params) -> bool:
    """Check if a single trade would pass the filter with given params."""
    votes = trade.get("indicator_votes", {})
    direction = trade.get("direction", "")
    if not votes or not direction:
        return False

    # Count consensus
    agreeing = 0
    total_voting = 0
    for name, vote in votes.items():
        if isinstance(vote, dict):
            ind_dir = vote.get("direction", "")
            weight = vote.get("weight", 1.0)
        else:
            ind_dir = str(vote) if vote in ("up", "down") else ""
            weight = 1.0
        if ind_dir in ("up", "down"):
            total_voting += 1
            if ind_dir == direction:
                agreeing += 1

    if total_voting == 0:
        return False

    # Consensus check
    consensus = agreeing
    if consensus < params.min_consensus:
        return False

    # Confidence check
    confidence = trade.get("confidence", 0)
    min_conf = params.min_confidence
    if direction == "up":
        min_conf += params.up_confidence_premium
    if confidence < min_conf:
        return False

    # Edge check
    edge = trade.get("edge", 0)
    if edge < params.min_edge_absolute:
        return False

    return True


def _attribute_params(
    trades: list[dict],
    current: dict,
    proposed: dict,
    base_signals: set[str],
) -> list[dict]:
    """Attribute PNL impact to individual parameter changes.

    For each changed param, run replay with only that param changed
    and measure the marginal impact.
    """
    attributions = []
    changed = {k: v for k, v in proposed.items() if current.get(k) != v}

    # Build PNL lookup
    pnl_map: dict[str, float] = {}
    for t in trades:
        tid = t.get("trade_id", "")
        if not tid:
            continue
        pnl = t.get("pnl", 0.0)
        if pnl == 0.0:
            edge = t.get("edge", 0.0)
            won = t.get("won", False)
            if edge > 0:
                pnl = 10.0 * edge if won else -10.0 * (1 - edge)
            else:
                pnl = 0.5 if won else -9.5
        pnl_map[tid] = pnl

    for param_name, new_val in changed.items():
        # Create a param set with only this one param changed
        single_change = dict(current)
        single_change[param_name] = new_val
        bp = _dict_to_backtest_params(single_change, f"single_{param_name}")
        single_signals = _get_signal_set(trades, bp)

        gained = len(single_signals - base_signals)
        lost = len(base_signals - single_signals)

        # Calculate PNL of gained/lost trades
        gained_pnl = sum(pnl_map.get(tid, 0) for tid in (single_signals - base_signals))
        lost_pnl = sum(pnl_map.get(tid, 0) for tid in (base_signals - single_signals))

        attributions.append({
            "param": param_name,
            "old_value": current.get(param_name),
            "new_value": new_val,
            "trades_gained": gained,
            "trades_lost": lost,
            "net_trades": gained - lost,
            "pnl_impact": round(gained_pnl - lost_pnl, 2),
        })

    # Sort by absolute PNL impact (most impactful first)
    attributions.sort(key=lambda x: abs(x["pnl_impact"]), reverse=True)
    return attributions


def write_pnl_impact(impact: PNLImpact):
    """Write PNL impact report to disk for dashboard display."""
    from quant.reporter import _now_et

    output = {
        "trades_kept": impact.trades_kept,
        "trades_gained": impact.trades_gained,
        "trades_lost": impact.trades_lost,
        "net_trade_change": impact.net_trade_change,
        "baseline_pnl": impact.baseline_pnl,
        "proposed_pnl": impact.proposed_pnl,
        "pnl_delta": impact.pnl_delta,
        "pnl_per_trade": impact.pnl_per_trade,
        "daily_pnl": impact.daily_pnl,
        "monthly_pnl": impact.monthly_pnl,
        "baseline_wr": impact.baseline_wr,
        "proposed_wr": impact.proposed_wr,
        "wr_delta": impact.wr_delta,
        "by_asset": impact.by_asset,
        "by_timeframe": impact.by_timeframe,
        "param_attribution": impact.param_attribution,
        "gained_trades": impact.gained_trades,
        "lost_trades": impact.lost_trades,
        "trades_analyzed": impact.trades_analyzed,
        "elapsed_seconds": impact.elapsed_seconds,
        "updated": _now_et(),
    }
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "quant_pnl_impact.json").write_text(json.dumps(output, indent=2))
    log.info("Wrote quant_pnl_impact.json (delta=$%.2f/day, %+d trades, WR %+.1fpp)",
             impact.daily_pnl, impact.net_trade_change, impact.wr_delta)
