"""Garves — Conviction-Based Dynamic Position Sizing Engine.

Scores conviction from 0-100 based on multiple evidence layers, then maps
conviction to position size. When conditions are exceptionally favorable
(all assets aligned, strong confirmations), sizes up. When uncertain, sizes down.

Research basis:
- Adaptive Kelly Criterion (fractional Kelly with Bayesian updating)
- Multi-indicator confluence scoring (consensus ratio + edge magnitude)
- Volatility regime adjustment (ATR-based regime detection)
- Cross-asset confirmation (BTC/ETH/SOL correlation detection)
- Temporal arbitrage strength (Binance lead-lag exploitation)

Integration: Called by Executor._dynamic_position_size() to replace the
simple quality*kelly formula with conviction-aware sizing.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from bot.bankroll import BankrollManager

log = logging.getLogger(__name__)

# Singleton bankroll manager
_bankroll_manager = BankrollManager()

DATA_DIR = Path(__file__).parent.parent / "data"
TRADES_FILE = DATA_DIR / "trades.jsonl"

# ── Position Size Tiers (USD) ──
# Conviction maps to these tiers with smooth interpolation within each band.
SIZE_TIERS = {
    # (min_conviction, max_conviction): (min_usd, max_usd)
    (0, 30):   (0.0, 0.0),      # DON'T TRADE — insufficient evidence
    (30, 50):  (8.0, 12.0),     # Small — tentative signal
    (50, 70):  (12.0, 20.0),    # Standard — solid consensus
    (70, 85):  (20.0, 28.0),    # Increased — strong multi-factor alignment
    (85, 100): (28.0, 35.0),    # Maximum conviction — nearly everything aligns
}

# ── Safety Rails ──
ABSOLUTE_MAX_PER_TRADE = 35.0       # Never exceed $35 per trade
ABSOLUTE_MAX_DAILY_LOSS = 50.0      # Stop trading if daily loss hits $50
LOSING_STREAK_THRESHOLD = 3         # Scale down after 3 consecutive losses
LOSING_STREAK_PENALTY = 0.6         # Multiply conviction by 0.6 during losing streak
MIN_ROLLING_WR_THRESHOLD = 0.45     # Scale down if rolling WR < 45%
LOW_WR_PENALTY = 0.7                # Multiply conviction by 0.7 if WR < 45%
ROLLING_WR_WINDOW = 20              # Check last 20 resolved trades
EXTREME_FEAR_PENALTY = 0.75         # Scale conviction down in extreme_fear regime

# ── Conviction Component Weights ──
# Each evidence layer contributes a weighted score to the total.
# Total weights sum to ~100 so raw score is already 0-100.
COMPONENT_WEIGHTS = {
    "consensus_ratio":      20,  # How many indicators agree (most reliable factor)
    "edge_magnitude":       15,  # How large the expected edge is
    "cross_asset_alignment": 12, # BTC/ETH/SOL all moving the same direction
    "volatility_clarity":   10,  # Clear trend vs noisy chop
    "streak_bonus":          8,  # On a hot streak = conditions are working
    "time_quality":          8,  # Are we in a historically good hour?
    "volume_confirmation":  10,  # Volume spike in direction of bet
    "temporal_arb_strength": 12, # Binance already confirmed the move
    "cross_timeframe":       5,  # 5m and 15m agree
}

# Good hours (ET) with historically high WR from 140+ trade analysis
# 00,02,10,12,16,17 = 79.5% WR combined
GOOD_HOURS_ET = {0, 2, 10, 12, 16, 17}
# Decent hours (not great, not terrible)
OKAY_HOURS_ET = {1, 3, 4, 8, 9, 11, 13, 14, 15}
# Bad hours already filtered by SignalEngine, but if a signal leaks through:
BAD_HOURS_ET = {5, 6, 7, 18, 19, 20, 21, 22, 23}

# ── All Assets Aligned Mode thresholds ──
ALL_ALIGNED_MIN_CONSENSUS = 7       # Each asset must have 7+ indicators agreeing
ALL_ALIGNED_MIN_ASSETS = 3          # 3 of 4 assets must agree
ALL_ALIGNED_SIZE = 35.0             # Max size when all-aligned fires


@dataclass
class ConvictionResult:
    """Output of the conviction scoring engine."""
    total_score: float              # 0-100 conviction score
    position_size_usd: float        # Recommended position size in USD
    all_assets_aligned: bool        # True if BTC/ETH/SOL all confirm same direction
    aligned_direction: str          # "up", "down", or "none"
    components: dict                # Breakdown of each scoring component
    safety_adjustments: list        # List of safety rails that were applied
    tier_label: str                 # "no_trade", "small", "standard", "increased", "max_conviction"

    def __repr__(self) -> str:
        return (
            f"Conviction({self.total_score:.0f}/100 -> ${self.position_size_usd:.2f} "
            f"[{self.tier_label}] aligned={self.all_assets_aligned})"
        )


@dataclass
class AssetSignalSnapshot:
    """Snapshot of signals for one asset at a point in time."""
    asset: str                       # "bitcoin", "ethereum", "solana"
    direction: str                   # "up" or "down"
    consensus_count: int             # How many indicators agree
    total_indicators: int            # How many indicators fired
    edge: float                      # Expected edge (fraction)
    confidence: float                # Signal confidence (0-1)
    has_volume_spike: bool           # Volume spike detected in signal direction
    has_temporal_arb: bool           # Temporal arb confirmed
    indicator_votes: dict            # indicator_name -> direction
    timestamp: float = field(default_factory=time.time)


class ConvictionEngine:
    """Scores conviction from 0-100 and maps to dynamic position size.

    Usage:
        engine = ConvictionEngine()
        # Register signals as they come in from each asset
        engine.register_signal(asset_snapshot_btc)
        engine.register_signal(asset_snapshot_eth)
        engine.register_signal(asset_snapshot_sol)

        # Score conviction for a specific trade
        result = engine.score(
            signal=signal,
            asset_snapshot=snapshot,
            regime=regime,
            atr_value=atr_val,
        )
        if result.position_size_usd > 0:
            # Execute at this size
            ...
    """

    def __init__(self):
        # Recent signals per asset: asset -> AssetSignalSnapshot
        # Used for cross-asset alignment detection
        self._asset_signals: dict[str, AssetSignalSnapshot] = {}
        self._SIGNAL_MAX_AGE = 120  # 2 minutes — signals expire quickly

        # Cross-timeframe cache: (asset, timeframe) -> (direction, timestamp)
        self._tf_signals: dict[tuple[str, str], tuple[str, float]] = {}
        self._TF_MAX_AGE = 600  # 10 minutes

        # Rolling performance cache (loaded from trades.jsonl)
        self._perf_cache: dict = {"loaded_at": 0.0}
        self._PERF_CACHE_TTL = 60  # Refresh every 60 seconds

    # ──────────────────────────────────────────────────────────────
    # Signal Registration (called for every signal across all assets)
    # ──────────────────────────────────────────────────────────────

    def register_signal(self, snapshot: AssetSignalSnapshot) -> None:
        """Register a signal snapshot for cross-asset alignment detection.

        Call this for EVERY signal generated (even ones that don't trade),
        so the engine knows what BTC/ETH/SOL are all doing.
        """
        self._asset_signals[snapshot.asset] = snapshot
        self._tf_signals[(snapshot.asset, "current")] = (
            snapshot.direction, snapshot.timestamp
        )
        log.debug(
            "Conviction: registered %s %s (%d/%d consensus, edge=%.1f%%)",
            snapshot.asset.upper(), snapshot.direction.upper(),
            snapshot.consensus_count, snapshot.total_indicators,
            snapshot.edge * 100,
        )

    def register_timeframe_signal(
        self, asset: str, timeframe: str, direction: str
    ) -> None:
        """Register a signal from a specific timeframe for cross-TF scoring."""
        self._tf_signals[(asset, timeframe)] = (direction, time.time())

    # ──────────────────────────────────────────────────────────────
    # Main Scoring Engine
    # ──────────────────────────────────────────────────────────────

    def score(
        self,
        signal,  # Signal dataclass from signals.py
        asset_snapshot: AssetSignalSnapshot,
        regime=None,  # RegimeAdjustment from regime.py
        atr_value: float | None = None,
    ) -> ConvictionResult:
        """Score conviction from 0-100 and map to position size.

        Args:
            signal: The Signal that passed all filters in SignalEngine.
            asset_snapshot: Detailed snapshot of indicators for this specific trade.
            regime: Current market regime (Fear & Greed based).
            atr_value: Current ATR as fraction of price.

        Returns:
            ConvictionResult with score, position size, and breakdown.
        """
        components = {}
        safety_adjustments = []

        # ── 1. Consensus Ratio (0-20 points) ──
        # How many of the total indicators agree on direction?
        # 7/13 = 54% -> base score; 13/13 = 100% -> max score
        # Minimum to even get here is 7 (MIN_CONSENSUS in signals.py)
        consensus_ratio = (
            asset_snapshot.consensus_count / max(asset_snapshot.total_indicators, 1)
        )
        # Scale: 54% consensus (7/13) = ~40% of points, 100% = 100%
        # Normalize so 7/13 = 0.4, 10/13 = 0.77, 13/13 = 1.0
        min_ratio = 7.0 / 13.0  # ~0.538
        normalized_consensus = max(0, (consensus_ratio - min_ratio) / (1.0 - min_ratio))
        # Also factor in raw count — 10 agreeing is stronger than 7
        raw_count_bonus = max(0, (asset_snapshot.consensus_count - 7)) / 6.0  # 0 at 7, 1.0 at 13
        consensus_score = (normalized_consensus * 0.6 + raw_count_bonus * 0.4)
        consensus_score = min(consensus_score, 1.0)
        components["consensus_ratio"] = consensus_score * COMPONENT_WEIGHTS["consensus_ratio"]

        # ── 2. Edge Magnitude (0-15 points) ──
        # How large is the expected edge after fees?
        # Data: 0-8% edge = 20% WR, 8-11% = 62.5% WR, 11%+ = very strong
        # Scale: 8% edge = 0.3, 12% = 0.7, 18%+ = 1.0
        edge_pct = signal.edge * 100  # convert to percentage
        if edge_pct <= 8:
            edge_score = 0.2  # Barely above minimum — weak
        elif edge_pct <= 12:
            edge_score = 0.2 + (edge_pct - 8) / 4.0 * 0.5  # 8->12% maps to 0.2->0.7
        elif edge_pct <= 18:
            edge_score = 0.7 + (edge_pct - 12) / 6.0 * 0.3  # 12->18% maps to 0.7->1.0
        else:
            edge_score = 1.0
        components["edge_magnitude"] = edge_score * COMPONENT_WEIGHTS["edge_magnitude"]

        # ── 3. Cross-Asset Alignment (0-12 points) ──
        # Do BTC, ETH, and SOL all agree on direction?
        aligned, aligned_dir, aligned_count, aligned_details = self._check_cross_asset_alignment(
            signal.direction
        )
        if aligned_count >= 3:
            cross_asset_score = 1.0  # All 3 agree — maximum
        elif aligned_count == 2:
            cross_asset_score = 0.5  # 2 of 3 agree — decent
        else:
            cross_asset_score = 0.0  # Only 1 or none — no cross-asset confirmation
        components["cross_asset_alignment"] = cross_asset_score * COMPONENT_WEIGHTS["cross_asset_alignment"]

        # ── 4. Volatility Clarity (0-10 points) ──
        # Is the market trending clearly or is it noisy chop?
        # High ATR = trending (good for directional bets)
        # Very low ATR = flat/noisy (bad — random outcomes)
        # Very high ATR = extreme vol (chaotic — unreliable)
        if atr_value is not None:
            if atr_value < 0.0005:
                vol_score = 0.1  # Too flat — near random
            elif atr_value < 0.002:
                # Normal range — scale linearly
                vol_score = 0.3 + (atr_value - 0.0005) / 0.0015 * 0.5
            elif atr_value < 0.005:
                vol_score = 0.8  # Good trending vol
            elif atr_value < 0.01:
                vol_score = 0.6  # Getting choppy
            else:
                vol_score = 0.3  # Extreme vol — unreliable
        else:
            vol_score = 0.4  # Unknown — assume middling
        components["volatility_clarity"] = vol_score * COMPONENT_WEIGHTS["volatility_clarity"]

        # ── 5. Streak Bonus (0-8 points) ──
        # Are we on a winning streak? (conditions are proven favorable)
        perf = self._get_rolling_performance()
        streak = perf.get("current_streak", 0)
        if streak >= 5:
            streak_score = 1.0   # 5+ win streak — conditions are excellent
        elif streak >= 3:
            streak_score = 0.7   # 3-4 win streak — good momentum
        elif streak >= 1:
            streak_score = 0.3   # Just won last one — mild confidence
        elif streak == 0:
            streak_score = 0.15  # Break-even territory
        else:
            streak_score = 0.0   # Losing streak — no bonus (penalty applied in safety)
        components["streak_bonus"] = streak_score * COMPONENT_WEIGHTS["streak_bonus"]

        # ── 6. Time-of-Day Quality (0-8 points) ──
        # Are we in a historically high-WR hour?
        from zoneinfo import ZoneInfo
        current_hour = datetime.now(ZoneInfo("America/New_York")).hour
        if current_hour in GOOD_HOURS_ET:
            time_score = 1.0   # Prime time — 79.5% combined WR
        elif current_hour in OKAY_HOURS_ET:
            time_score = 0.4   # Acceptable hours
        else:
            time_score = 0.0   # Bad hours (should be filtered already, but just in case)
        components["time_quality"] = time_score * COMPONENT_WEIGHTS["time_quality"]

        # ── 7. Volume Confirmation (0-10 points) ──
        # Did we detect a volume spike in the direction of the bet?
        if asset_snapshot.has_volume_spike:
            vol_confirm_score = 1.0  # Volume spike confirms direction — strong
        elif "volume_spike" in asset_snapshot.indicator_votes:
            # Volume spike indicator fired but maybe not in our direction
            vs_dir = asset_snapshot.indicator_votes.get("volume_spike", "")
            if vs_dir == signal.direction:
                vol_confirm_score = 0.8  # Directional volume — good
            else:
                vol_confirm_score = 0.1  # Volume against us — bad sign
        else:
            vol_confirm_score = 0.3  # No volume data — neutral (don't penalize too much)
        components["volume_confirmation"] = vol_confirm_score * COMPONENT_WEIGHTS["volume_confirmation"]

        # ── 8. Temporal Arb Strength (0-12 points) ──
        # Has Binance already confirmed the move? (The Gabagool strategy)
        # This is the highest-edge single indicator in the system.
        if asset_snapshot.has_temporal_arb:
            # Temporal arb fired AND agrees with our direction
            ta_dir = asset_snapshot.indicator_votes.get("temporal_arb", "")
            if ta_dir == signal.direction:
                arb_score = 1.0  # Binance confirmed — very strong
            else:
                arb_score = 0.0  # Arb disagrees — concerning
        elif "temporal_arb" in asset_snapshot.indicator_votes:
            ta_dir = asset_snapshot.indicator_votes.get("temporal_arb", "")
            if ta_dir == signal.direction:
                arb_score = 0.6  # Arb present but not flagged as strong
            else:
                arb_score = 0.0
        else:
            arb_score = 0.2  # No arb data — slight neutral
        components["temporal_arb_strength"] = arb_score * COMPONENT_WEIGHTS["temporal_arb_strength"]

        # ── 9. Cross-Timeframe Agreement (0-5 points) ──
        # Do the 5m and 15m timeframes agree?
        ctf_score = self._check_cross_timeframe(signal.asset, signal.direction)
        components["cross_timeframe"] = ctf_score * COMPONENT_WEIGHTS["cross_timeframe"]

        # ── Sum Raw Score ──
        raw_score = sum(components.values())

        # ── Apply Safety Rails (multiply conviction down) ──
        multiplier = 1.0

        # Safety 1: Losing streak penalty
        if streak <= -LOSING_STREAK_THRESHOLD:
            multiplier *= LOSING_STREAK_PENALTY
            safety_adjustments.append(
                f"losing_streak={streak} (penalty {LOSING_STREAK_PENALTY}x)"
            )

        # Safety 2: Low rolling win rate
        rolling_wr = perf.get("rolling_wr")
        if rolling_wr is not None and rolling_wr < MIN_ROLLING_WR_THRESHOLD:
            multiplier *= LOW_WR_PENALTY
            safety_adjustments.append(
                f"low_WR={rolling_wr:.1%} < {MIN_ROLLING_WR_THRESHOLD:.0%} "
                f"(penalty {LOW_WR_PENALTY}x)"
            )

        # Safety 3: Extreme fear regime — indicators unreliable in panics
        if regime is not None and regime.label == "extreme_fear":
            multiplier *= EXTREME_FEAR_PENALTY
            safety_adjustments.append(
                f"extreme_fear_regime FnG={regime.fng_value} "
                f"(penalty {EXTREME_FEAR_PENALTY}x)"
            )

        # Safety 4: Daily loss limit check
        daily_loss = perf.get("daily_pnl", 0.0)
        if daily_loss <= -ABSOLUTE_MAX_DAILY_LOSS:
            multiplier = 0.0
            safety_adjustments.append(
                f"daily_loss=${daily_loss:.2f} >= ${ABSOLUTE_MAX_DAILY_LOSS} STOP"
            )

        # Safety 5: SOL asset penalty — consistently underperforming (33% WR, negative P/L)
        if signal.asset and signal.asset.lower() in ("solana", "sol"):
            multiplier *= 0.4
            safety_adjustments.append("sol_penalty (0.4x — consistently low WR)")

        # Apply multiplier to raw score
        final_score = max(0.0, min(100.0, raw_score * multiplier))

        # ── Check All-Assets-Aligned Mode ──
        all_aligned = self._check_all_assets_aligned(signal.direction)

        # ── Map Conviction to Position Size ──
        if all_aligned and multiplier > 0:
            # Override: ALL assets aligned — max size on all three
            position_size = ALL_ALIGNED_SIZE
            tier_label = "all_aligned"
            safety_adjustments.append(
                "ALL_ASSETS_ALIGNED: BTC+ETH+SOL confirm "
                f"{signal.direction.upper()}, sizing to ${ALL_ALIGNED_SIZE}"
            )
        else:
            position_size = self._conviction_to_size(final_score)
            tier_label = self._get_tier_label(final_score)

        # Apply bankroll multiplier (auto-compounding)
        bankroll_mult = _bankroll_manager.get_multiplier()
        if bankroll_mult != 1.0:
            position_size *= bankroll_mult
            safety_adjustments.append(
                f"bankroll_mult={bankroll_mult:.2f}x"
            )

        # Apply regime size multiplier even in all-aligned mode
        if regime is not None:
            position_size *= regime.size_multiplier
            if regime.size_multiplier != 1.0:
                safety_adjustments.append(
                    f"regime_size_mult={regime.size_multiplier:.1f}x ({regime.label})"
                )

        # Hard cap — NEVER exceed absolute max
        position_size = min(position_size, ABSOLUTE_MAX_PER_TRADE)

        result = ConvictionResult(
            total_score=final_score,
            position_size_usd=round(position_size, 2),
            all_assets_aligned=all_aligned,
            aligned_direction=aligned_dir if aligned else "none",
            components=components,
            safety_adjustments=safety_adjustments,
            tier_label=tier_label,
        )

        log.info(
            "CONVICTION: %s %s/%s score=%.0f/100 -> $%.2f [%s] | "
            "consensus=%.1f edge=%.1f cross_asset=%.1f vol_clarity=%.1f "
            "streak=%.1f time=%.1f volume=%.1f arb=%.1f ctf=%.1f%s",
            signal.asset.upper(), signal.timeframe, signal.direction.upper(),
            final_score, position_size, tier_label,
            components.get("consensus_ratio", 0),
            components.get("edge_magnitude", 0),
            components.get("cross_asset_alignment", 0),
            components.get("volatility_clarity", 0),
            components.get("streak_bonus", 0),
            components.get("time_quality", 0),
            components.get("volume_confirmation", 0),
            components.get("temporal_arb_strength", 0),
            components.get("cross_timeframe", 0),
            f" | safety: {', '.join(safety_adjustments)}" if safety_adjustments else "",
        )

        return result

    # ──────────────────────────────────────────────────────────────
    # Component Helpers
    # ──────────────────────────────────────────────────────────────

    def _check_cross_asset_alignment(
        self, target_direction: str
    ) -> tuple[bool, str, int, dict]:
        """Check how many of BTC/ETH/SOL agree on the same direction.

        Returns:
            (all_aligned, direction, count, details)
        """
        now = time.time()
        aligned_count = 0
        details = {}

        for asset in ("bitcoin", "ethereum", "solana", "xrp"):
            snap = self._asset_signals.get(asset)
            if snap is None or (now - snap.timestamp) > self._SIGNAL_MAX_AGE:
                details[asset] = "stale/missing"
                continue

            if snap.direction == target_direction:
                aligned_count += 1
                details[asset] = f"{snap.direction} ({snap.consensus_count}/{snap.total_indicators})"
            else:
                details[asset] = f"{snap.direction} (DISAGREE)"

        all_aligned = aligned_count >= 3  # 3 of 4 assets is sufficient
        return all_aligned, target_direction, aligned_count, details

    def _check_all_assets_aligned(self, direction: str) -> bool:
        """Full check for the 'All Assets Aligned' special mode.

        Requirements:
        - All 3 assets (BTC, ETH, SOL) show same direction
        - Each has 7+ indicator consensus
        - At least one has volume confirmation
        - At least one has temporal arb confirmation
        """
        now = time.time()
        aligned_assets = []
        has_volume = False
        has_arb = False

        for asset in ("bitcoin", "ethereum", "solana", "xrp"):
            snap = self._asset_signals.get(asset)
            if snap is None or (now - snap.timestamp) > self._SIGNAL_MAX_AGE:
                continue  # Missing data — skip this asset, don't block
            if snap.direction != direction:
                continue  # Disagrees — skip
            if snap.consensus_count < ALL_ALIGNED_MIN_CONSENSUS:
                continue  # Consensus too weak

            aligned_assets.append(asset)
            if snap.has_volume_spike:
                has_volume = True
            if snap.has_temporal_arb:
                has_arb = True

        if len(aligned_assets) < ALL_ALIGNED_MIN_ASSETS:
            return False

        # Need at least volume OR temporal arb confirmation (don't require both)
        if not (has_volume or has_arb):
            return False

        aligned_names = "+".join(a.upper()[:3] for a in aligned_assets)
        log.info(
            "ALL ASSETS ALIGNED: %s all %s with 7+ consensus | "
            "volume_confirmed=%s arb_confirmed=%s",
            aligned_names, direction.upper(), has_volume, has_arb,
        )
        return True

    def _check_cross_timeframe(self, asset: str, direction: str) -> float:
        """Score cross-timeframe agreement (0-1).

        Checks if both 5m and 15m signals agree for this asset.
        """
        now = time.time()
        agreements = 0
        total_checked = 0

        for tf in ("5m", "15m"):
            cached = self._tf_signals.get((asset, tf))
            if cached is not None:
                cached_dir, cached_ts = cached
                if (now - cached_ts) < self._TF_MAX_AGE:
                    total_checked += 1
                    if cached_dir == direction:
                        agreements += 1

        if total_checked == 0:
            return 0.3  # No cross-TF data — slight neutral
        elif total_checked == 1:
            return 0.5 if agreements == 1 else 0.1
        else:
            # Both 5m and 15m available
            if agreements == 2:
                return 1.0  # Both agree — excellent
            elif agreements == 1:
                return 0.3  # Split — mediocre
            else:
                return 0.0  # Both disagree — terrible

    def _get_rolling_performance(self) -> dict:
        """Load rolling performance metrics from trades.jsonl.

        Returns dict with:
        - rolling_wr: Win rate over last ROLLING_WR_WINDOW resolved trades
        - current_streak: Positive = wins, negative = losses, 0 = mixed
        - daily_pnl: Estimated P&L for today (based on $10 base size, win = +$size, loss = -$size)
        - total_resolved: Total number of resolved trades
        """
        now = time.time()

        # Use cached data if fresh
        if (
            self._perf_cache.get("loaded_at", 0)
            and (now - self._perf_cache["loaded_at"]) < self._PERF_CACHE_TTL
        ):
            return self._perf_cache

        result = {
            "rolling_wr": None,
            "current_streak": 0,
            "daily_pnl": 0.0,
            "total_resolved": 0,
            "loaded_at": now,
        }

        if not TRADES_FILE.exists():
            self._perf_cache = result
            return result

        try:
            resolved = []
            with open(TRADES_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if rec.get("resolved") and rec.get("outcome") in ("up", "down"):
                        resolved.append(rec)

            result["total_resolved"] = len(resolved)

            if not resolved:
                self._perf_cache = result
                return result

            # Rolling win rate (last N trades)
            recent = resolved[-ROLLING_WR_WINDOW:]
            wins = sum(1 for r in recent if r.get("won"))
            result["rolling_wr"] = wins / len(recent) if recent else None

            # Current streak (count from most recent backwards)
            streak = 0
            for r in reversed(resolved):
                won = r.get("won", False)
                if streak == 0:
                    streak = 1 if won else -1
                elif streak > 0 and won:
                    streak += 1
                elif streak < 0 and not won:
                    streak -= 1
                else:
                    break
            result["current_streak"] = streak

            # Daily P&L estimate (simplified: each trade is ~$10 base)
            # Count today's wins and losses
            from zoneinfo import ZoneInfo
            _et = ZoneInfo("America/New_York")
            today_str = datetime.now(_et).strftime("%Y-%m-%d")
            daily_wins = 0
            daily_losses = 0
            for r in resolved:
                ts = r.get("resolve_time") or r.get("timestamp", 0)
                trade_date = datetime.fromtimestamp(ts, tz=_et).strftime("%Y-%m-%d")
                if trade_date == today_str:
                    if r.get("won"):
                        daily_wins += 1
                    else:
                        daily_losses += 1

            # Rough P&L: wins earn ~$8-12 (depending on odds), losses = -$size
            # Use conservative estimate: win = +$8, loss = -$10
            result["daily_pnl"] = daily_wins * 8.0 - daily_losses * 10.0

        except Exception:
            log.debug("Failed to load rolling performance, using defaults")

        self._perf_cache = result
        return result

    # ──────────────────────────────────────────────────────────────
    # Size Mapping
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _conviction_to_size(score: float) -> float:
        """Map a 0-100 conviction score to a USD position size.

        Uses smooth linear interpolation within each tier band.
        """
        if score < 30:
            return 0.0  # Don't trade

        for (lo, hi), (min_usd, max_usd) in SIZE_TIERS.items():
            if lo <= score < hi:
                # Linear interpolation within the band
                t = (score - lo) / (hi - lo)
                return min_usd + t * (max_usd - min_usd)

        # Score is exactly 100
        return 35.0

    @staticmethod
    def _get_tier_label(score: float) -> str:
        """Human-readable tier label."""
        if score < 30:
            return "no_trade"
        elif score < 50:
            return "small"
        elif score < 70:
            return "standard"
        elif score < 85:
            return "increased"
        else:
            return "max_conviction"

    # ──────────────────────────────────────────────────────────────
    # Utility: Build AssetSignalSnapshot from Signal + votes
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def build_snapshot(
        signal,
        indicator_votes: dict,
        up_count: int,
        down_count: int,
        total_indicators: int,
    ) -> AssetSignalSnapshot:
        """Build an AssetSignalSnapshot from a Signal and its indicator votes.

        This is a convenience method to bridge SignalEngine output into
        the format ConvictionEngine expects.

        Args:
            signal: Signal dataclass from signals.py
            indicator_votes: dict of indicator_name -> IndicatorVote (active votes)
            up_count: Number of indicators voting UP
            down_count: Number of indicators voting DOWN
            total_indicators: Total number of active indicators
        """
        # Check if volume spike fired in signal direction
        has_volume = False
        vs_vote = indicator_votes.get("volume_spike")
        if vs_vote is not None:
            if hasattr(vs_vote, "direction"):
                has_volume = vs_vote.direction == signal.direction
            elif isinstance(vs_vote, str):
                has_volume = vs_vote == signal.direction

        # Check if temporal arb fired in signal direction
        has_arb = False
        ta_vote = indicator_votes.get("temporal_arb")
        if ta_vote is not None:
            if hasattr(ta_vote, "direction"):
                has_arb = ta_vote.direction == signal.direction
            elif isinstance(ta_vote, str):
                has_arb = ta_vote == signal.direction

        # Flatten votes to direction strings for storage
        flat_votes = {}
        for name, vote in indicator_votes.items():
            if hasattr(vote, "direction"):
                flat_votes[name] = vote.direction
            elif isinstance(vote, str):
                flat_votes[name] = vote

        return AssetSignalSnapshot(
            asset=signal.asset,
            direction=signal.direction,
            consensus_count=max(up_count, down_count),
            total_indicators=total_indicators,
            edge=signal.edge,
            confidence=signal.confidence,
            has_volume_spike=has_volume,
            has_temporal_arb=has_arb,
            indicator_votes=flat_votes,
        )

    # ──────────────────────────────────────────────────────────────
    # Cleanup
    # ──────────────────────────────────────────────────────────────

    def expire_stale_signals(self) -> None:
        """Remove signals older than the max age. Call once per tick."""
        now = time.time()
        stale = [
            asset for asset, snap in self._asset_signals.items()
            if (now - snap.timestamp) > self._SIGNAL_MAX_AGE
        ]
        for asset in stale:
            del self._asset_signals[asset]

        stale_tf = [
            key for key, (_, ts) in self._tf_signals.items()
            if (now - ts) > self._TF_MAX_AGE
        ]
        for key in stale_tf:
            del self._tf_signals[key]

    def get_status(self) -> dict:
        """Return current conviction engine state for dashboard/monitoring."""
        now = time.time()
        perf = self._get_rolling_performance()

        asset_states = {}
        for asset in ("bitcoin", "ethereum", "solana", "xrp"):
            snap = self._asset_signals.get(asset)
            if snap and (now - snap.timestamp) <= self._SIGNAL_MAX_AGE:
                asset_states[asset] = {
                    "direction": snap.direction,
                    "consensus": f"{snap.consensus_count}/{snap.total_indicators}",
                    "edge": f"{snap.edge*100:.1f}%",
                    "volume_spike": snap.has_volume_spike,
                    "temporal_arb": snap.has_temporal_arb,
                    "age_s": round(now - snap.timestamp, 0),
                }
            else:
                asset_states[asset] = {"status": "no_signal"}

        return {
            "asset_signals": asset_states,
            "rolling_wr": f"{perf['rolling_wr']:.1%}" if perf.get("rolling_wr") else "N/A",
            "current_streak": perf.get("current_streak", 0),
            "daily_pnl": f"${perf.get('daily_pnl', 0):.2f}",
            "total_resolved": perf.get("total_resolved", 0),
        }
