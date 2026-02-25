"""Self-Learning Module — Quant learns from actual Garves V2/Odin live performance.

Instead of only learning from backtests, this module:
  1. Tracks which Quant recommendations were actually applied
  2. Measures their real-world outcomes (did WR improve?)
  3. Adjusts confidence in future recommendations based on track record
  4. Loads and analyzes Odin trade data (not just Garves V2)

Feedback loop: Quant recommends → params applied → trades happen →
outcomes measured → Quant adjusts future recommendations.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ODIN_DATA_DIR = Path.home() / "odin" / "data"
LEARNING_FILE = DATA_DIR / "quant_learning.json"
ODIN_TRADES_FILE = ODIN_DATA_DIR / "odin_trades.jsonl"


@dataclass
class RecommendationOutcome:
    """Tracks a single recommendation and its real-world result."""
    rec_id: str = ""
    param_name: str = ""
    old_value: object = None
    new_value: object = None
    applied_at: float = 0.0
    # Pre-application performance (baseline)
    pre_wr: float = 0.0
    pre_trades: int = 0
    # Post-application performance (actual result)
    post_wr: float = 0.0
    post_trades: int = 0
    # Was this recommendation successful?
    wr_change_pp: float = 0.0
    successful: bool = False
    measured_at: float = 0.0


@dataclass
class LearningState:
    """Persistent learning state for Quant's self-improvement."""
    # Track record
    total_recommendations: int = 0
    applied_recommendations: int = 0
    successful_recommendations: int = 0
    accuracy: float = 0.0              # % of applied recs that improved WR
    # Confidence adjustments per param type
    param_confidence: dict[str, float] = field(default_factory=dict)
    # Recent outcomes
    recent_outcomes: list[dict] = field(default_factory=list)
    # Odin integration
    odin_trades_analyzed: int = 0
    odin_win_rate: float = 0.0
    odin_avg_pnl: float = 0.0
    # Combined intelligence
    garves_odin_combined_wr: float = 0.0
    cross_learnings: list[str] = field(default_factory=list)
    # Timestamps
    last_learning_cycle: float = 0.0
    updated: str = ""


def load_odin_trades(since_timestamp: float = 0) -> list[dict]:
    """Load Odin's closed trades from odin_trades.jsonl.

    Returns normalized trade dicts compatible with Quant's analysis format.
    """
    if not ODIN_TRADES_FILE.exists():
        return []

    trades = []
    try:
        with open(ODIN_TRADES_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    t = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_time = t.get("entry_time", 0)
                if entry_time < since_timestamp:
                    continue

                # Normalize Odin trade format → Quant-compatible
                symbol = t.get("symbol", "")
                asset = symbol.replace("USDT", "").lower()
                # Map to Garves V2-style asset names
                asset_map = {
                    "btc": "bitcoin", "eth": "ethereum", "sol": "solana",
                    "xrp": "xrp", "ltc": "litecoin", "bch": "bitcoin_cash",
                    "doge": "dogecoin",
                }
                asset = asset_map.get(asset, asset)

                side = t.get("side", "").lower()
                direction = "up" if side == "long" else "down"

                trades.append({
                    "source": "odin",
                    "trade_id": t.get("trade_id", ""),
                    "asset": asset,
                    "symbol": symbol,
                    "direction": direction,
                    "won": t.get("is_win", False),
                    "resolved": True,
                    "pnl": t.get("pnl_usd", 0),
                    "pnl_pct": t.get("pnl_pct", 0),
                    "timestamp": entry_time,
                    "exit_time": t.get("exit_time", 0),
                    "hold_hours": t.get("hold_hours", 0),
                    "exit_reason": t.get("exit_reason", ""),
                    "confluence": t.get("confluence", 0),
                    "macro_regime": t.get("macro_regime", ""),
                    "leverage": t.get("leverage", 1),
                    "mode": t.get("mode", "paper"),
                })
    except Exception:
        log.exception("Failed to load Odin trades")

    return trades


def _load_learning_state() -> LearningState:
    """Load persistent learning state from disk."""
    if not LEARNING_FILE.exists():
        return LearningState()
    try:
        data = json.loads(LEARNING_FILE.read_text())
        state = LearningState()
        for key, value in data.items():
            if hasattr(state, key):
                setattr(state, key, value)
        return state
    except Exception:
        return LearningState()


def _save_learning_state(state: LearningState):
    """Save learning state to disk."""
    DATA_DIR.mkdir(exist_ok=True)
    output = {
        "total_recommendations": state.total_recommendations,
        "applied_recommendations": state.applied_recommendations,
        "successful_recommendations": state.successful_recommendations,
        "accuracy": state.accuracy,
        "param_confidence": state.param_confidence,
        "recent_outcomes": state.recent_outcomes[-20:],  # keep last 20
        "odin_trades_analyzed": state.odin_trades_analyzed,
        "odin_win_rate": state.odin_win_rate,
        "odin_avg_pnl": state.odin_avg_pnl,
        "garves_odin_combined_wr": state.garves_odin_combined_wr,
        "cross_learnings": state.cross_learnings[-10:],
        "last_learning_cycle": state.last_learning_cycle,
        "updated": datetime.now(ET).strftime("%Y-%m-%d %I:%M %p ET"),
    }
    LEARNING_FILE.write_text(json.dumps(output, indent=2))


def track_recommendation(
    param_name: str,
    old_value: object,
    new_value: object,
    pre_wr: float,
    pre_trades: int,
):
    """Record that a recommendation was applied. Called after live push."""
    state = _load_learning_state()
    state.total_recommendations += 1
    state.applied_recommendations += 1

    outcome = {
        "rec_id": f"rec_{int(time.time())}",
        "param_name": param_name,
        "old_value": old_value,
        "new_value": new_value,
        "applied_at": time.time(),
        "pre_wr": pre_wr,
        "pre_trades": pre_trades,
        "post_wr": None,  # filled in later by measure_outcomes()
        "post_trades": 0,
        "measured": False,
    }
    state.recent_outcomes.append(outcome)
    _save_learning_state(state)
    log.info("Tracked recommendation: %s = %s → %s (pre WR=%.1f%%)",
             param_name, old_value, new_value, pre_wr)


def measure_outcomes(
    garves_trades: list[dict],
    min_trades_after: int = 15,
) -> list[RecommendationOutcome]:
    """Measure real-world outcomes of past recommendations.

    For each tracked recommendation that hasn't been measured yet:
    - Count trades AFTER the recommendation was applied
    - If enough trades (min_trades_after), compute post-application WR
    - Determine if the recommendation was successful (WR improved)
    - Update confidence for that parameter type
    """
    state = _load_learning_state()
    measured = []

    for outcome in state.recent_outcomes:
        if outcome.get("measured"):
            continue

        applied_at = outcome.get("applied_at", 0)
        if applied_at == 0:
            continue

        # Find trades after this recommendation was applied
        post_trades = [
            t for t in garves_trades
            if t.get("timestamp", 0) > applied_at and t.get("resolved")
        ]

        if len(post_trades) < min_trades_after:
            continue  # Not enough data yet

        # Compute post-application WR
        post_wins = sum(1 for t in post_trades if t.get("won"))
        post_wr = post_wins / len(post_trades) * 100

        pre_wr = outcome.get("pre_wr", 50.0)
        wr_change = post_wr - pre_wr
        successful = wr_change > 0

        outcome["post_wr"] = round(post_wr, 1)
        outcome["post_trades"] = len(post_trades)
        outcome["wr_change_pp"] = round(wr_change, 1)
        outcome["successful"] = successful
        outcome["measured"] = True
        outcome["measured_at"] = time.time()

        # Update param-level confidence
        param = outcome.get("param_name", "unknown")
        if param not in state.param_confidence:
            state.param_confidence[param] = 0.5  # neutral starting point

        # Exponential moving average of success
        alpha = 0.3  # learning rate
        success_val = 1.0 if successful else 0.0
        state.param_confidence[param] = round(
            state.param_confidence[param] * (1 - alpha) + success_val * alpha, 3
        )

        if successful:
            state.successful_recommendations += 1

        measured.append(RecommendationOutcome(
            rec_id=outcome.get("rec_id", ""),
            param_name=param,
            old_value=outcome.get("old_value"),
            new_value=outcome.get("new_value"),
            applied_at=applied_at,
            pre_wr=pre_wr,
            pre_trades=outcome.get("pre_trades", 0),
            post_wr=post_wr,
            post_trades=len(post_trades),
            wr_change_pp=wr_change,
            successful=successful,
            measured_at=time.time(),
        ))

        log.info("Measured rec %s (%s): pre=%.1f%% → post=%.1f%% (%+.1fpp) %s",
                 outcome.get("rec_id"), param, pre_wr, post_wr, wr_change,
                 "SUCCESS" if successful else "FAIL")

    # Update overall accuracy
    total_measured = sum(1 for o in state.recent_outcomes if o.get("measured"))
    total_success = sum(1 for o in state.recent_outcomes if o.get("successful"))
    state.accuracy = round(total_success / total_measured * 100, 1) if total_measured > 0 else 0.0

    _save_learning_state(state)
    return measured


def analyze_odin_performance(
    odin_trades: list[dict] | None = None,
) -> dict:
    """Analyze Odin's trading performance for cross-learning.

    Returns insights that can help Garves V2 (and vice versa).
    """
    if odin_trades is None:
        odin_trades = load_odin_trades()

    if not odin_trades:
        return {"status": "no_odin_trades", "insights": []}

    resolved = [t for t in odin_trades if t.get("resolved")]
    if not resolved:
        return {"status": "no_resolved_trades", "insights": []}

    wins = sum(1 for t in resolved if t.get("won"))
    total = len(resolved)
    wr = wins / total * 100
    avg_pnl = sum(t.get("pnl", 0) for t in resolved) / total
    total_pnl = sum(t.get("pnl", 0) for t in resolved)

    # Analyze by exit reason
    exit_reasons: dict[str, dict] = {}
    for t in resolved:
        reason = t.get("exit_reason", "unknown")
        if reason not in exit_reasons:
            exit_reasons[reason] = {"wins": 0, "losses": 0, "pnl": 0}
        if t.get("won"):
            exit_reasons[reason]["wins"] += 1
        else:
            exit_reasons[reason]["losses"] += 1
        exit_reasons[reason]["pnl"] += t.get("pnl", 0)

    # Analyze by asset
    by_asset: dict[str, dict] = {}
    for t in resolved:
        asset = t.get("asset", "unknown")
        if asset not in by_asset:
            by_asset[asset] = {"wins": 0, "losses": 0, "pnl": 0}
        if t.get("won"):
            by_asset[asset]["wins"] += 1
        else:
            by_asset[asset]["losses"] += 1
        by_asset[asset]["pnl"] += t.get("pnl", 0)

    # Analyze by macro regime
    by_regime: dict[str, dict] = {}
    for t in resolved:
        regime = t.get("macro_regime", "unknown")
        if regime not in by_regime:
            by_regime[regime] = {"wins": 0, "losses": 0}
        if t.get("won"):
            by_regime[regime]["wins"] += 1
        else:
            by_regime[regime]["losses"] += 1

    # Generate cross-learning insights
    insights = []

    # Asset insights: which assets does Odin trade well?
    for asset, stats in by_asset.items():
        a_total = stats["wins"] + stats["losses"]
        if a_total < 3:
            continue
        a_wr = stats["wins"] / a_total * 100
        if a_wr > 65:
            insights.append(
                f"Odin strong on {asset}: {a_wr:.0f}% WR ({a_total} trades) — "
                f"Garves V2 could increase {asset} conviction"
            )
        elif a_wr < 35:
            insights.append(
                f"Odin weak on {asset}: {a_wr:.0f}% WR — "
                f"Garves V2 should reduce {asset} exposure or add caution"
            )

    # Regime insights
    for regime, stats in by_regime.items():
        r_total = stats["wins"] + stats["losses"]
        if r_total < 3:
            continue
        r_wr = stats["wins"] / r_total * 100
        if r_wr > 65:
            insights.append(
                f"Odin performs well in '{regime}' regime ({r_wr:.0f}% WR) — "
                f"Garves V2 can be more aggressive in this regime"
            )
        elif r_wr < 35:
            insights.append(
                f"Both traders should reduce size in '{regime}' regime "
                f"(Odin WR={r_wr:.0f}%)"
            )

    # Exit reason insights
    for reason, stats in exit_reasons.items():
        r_total = stats["wins"] + stats["losses"]
        if r_total < 3:
            continue
        r_wr = stats["wins"] / r_total * 100
        if reason == "SL" and r_wr < 30:
            insights.append(
                f"Odin SL exits have {r_wr:.0f}% WR — consider wider stops or "
                f"better entry timing"
            )

    # Save Odin analysis
    state = _load_learning_state()
    state.odin_trades_analyzed = len(resolved)
    state.odin_win_rate = round(wr, 1)
    state.odin_avg_pnl = round(avg_pnl, 2)
    state.cross_learnings = insights
    _save_learning_state(state)

    return {
        "status": "analyzed",
        "total_trades": total,
        "win_rate": round(wr, 1),
        "avg_pnl": round(avg_pnl, 2),
        "total_pnl": round(total_pnl, 2),
        "by_asset": {
            k: {**v, "wr": round(v["wins"] / (v["wins"] + v["losses"]) * 100, 1)}
            for k, v in by_asset.items()
            if v["wins"] + v["losses"] >= 3
        },
        "by_regime": {
            k: {**v, "wr": round(v["wins"] / (v["wins"] + v["losses"]) * 100, 1)}
            for k, v in by_regime.items()
            if v["wins"] + v["losses"] >= 3
        },
        "by_exit_reason": {
            k: {**v, "wr": round(v["wins"] / (v["wins"] + v["losses"]) * 100, 1)}
            for k, v in exit_reasons.items()
            if v["wins"] + v["losses"] >= 3
        },
        "insights": insights,
    }


def run_learning_cycle(
    garves_trades: list[dict],
    odin_trades: list[dict] | None = None,
) -> dict:
    """Run a complete self-learning cycle.

    1. Measure outcomes of past recommendations
    2. Analyze Odin performance for cross-learning
    3. Compute combined Garves V2+Odin intelligence
    4. Return summary for logging/dashboard

    Called during each Quant main cycle.
    """
    state = _load_learning_state()
    state.last_learning_cycle = time.time()

    # 1. Measure past recommendations
    outcomes = measure_outcomes(garves_trades)

    # 2. Load and analyze Odin trades
    if odin_trades is None:
        odin_trades = load_odin_trades()
    odin_analysis = analyze_odin_performance(odin_trades)

    # 3. Combined Garves V2 + Odin WR
    garves_resolved = [t for t in garves_trades if t.get("resolved") and t.get("won") is not None]
    garves_wins = sum(1 for t in garves_resolved if t.get("won"))
    garves_total = len(garves_resolved)

    odin_resolved = [t for t in odin_trades if t.get("resolved") and t.get("won") is not None]
    odin_wins = sum(1 for t in odin_resolved if t.get("won"))
    odin_total = len(odin_resolved)

    combined_total = garves_total + odin_total
    combined_wins = garves_wins + odin_wins
    combined_wr = (combined_wins / combined_total * 100) if combined_total > 0 else 0

    state.garves_odin_combined_wr = round(combined_wr, 1)
    _save_learning_state(state)

    summary = {
        "outcomes_measured": len(outcomes),
        "recommendation_accuracy": state.accuracy,
        "param_confidence": state.param_confidence,
        "garves_wr": round(garves_wins / garves_total * 100, 1) if garves_total else 0,
        "garves_trades": garves_total,
        "odin_wr": round(odin_wins / odin_total * 100, 1) if odin_total else 0,
        "odin_trades": odin_total,
        "combined_wr": round(combined_wr, 1),
        "combined_trades": combined_total,
        "odin_insights": odin_analysis.get("insights", []),
        "successful_outcomes": [
            {
                "param": o.param_name,
                "change": f"{o.pre_wr:.1f}% → {o.post_wr:.1f}%",
                "improvement": f"{o.wr_change_pp:+.1f}pp",
            }
            for o in outcomes if o.successful
        ],
        "failed_outcomes": [
            {
                "param": o.param_name,
                "change": f"{o.pre_wr:.1f}% → {o.post_wr:.1f}%",
                "degradation": f"{o.wr_change_pp:+.1f}pp",
            }
            for o in outcomes if not o.successful
        ],
    }

    log.info("Learning cycle: %d outcomes measured (accuracy=%.0f%%), "
             "Garves V2 WR=%.1f%% (%d), Odin WR=%.1f%% (%d), Combined=%.1f%%",
             len(outcomes), state.accuracy,
             summary["garves_wr"], garves_total,
             summary["odin_wr"], odin_total,
             combined_wr)

    return summary


def get_adjusted_confidence(param_name: str, base_confidence: float = 0.5) -> float:
    """Get learning-adjusted confidence for a parameter recommendation.

    If Quant's past recommendations for this param have been successful,
    confidence is boosted. If they've failed, confidence is reduced.

    Used by live_push to decide whether to apply a recommendation.
    """
    state = _load_learning_state()
    learned = state.param_confidence.get(param_name, base_confidence)
    return learned
