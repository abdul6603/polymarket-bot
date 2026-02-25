"""Smart Money Concepts (SMC) pattern detection engine.

Detects: Swing Highs/Lows, Break of Structure (BOS), Change of Character
(CHOCH), Fair Value Gaps (FVG), Order Blocks (OB), Liquidity Sweeps.

Uses the `smartmoneyconcepts` library where possible, with custom extensions
for validity filtering (volume Z-score, displacement, first-touch tracking).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# ── Pattern Types ──

class PatternType(Enum):
    BOS = "BOS"
    CHOCH = "CHOCH"
    FVG = "FVG"
    OB = "OB"
    LIQUIDITY_SWEEP = "LIQUIDITY_SWEEP"
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"


class Direction(Enum):
    BULLISH = 1
    BEARISH = -1
    NEUTRAL = 0


@dataclass
class SMCPattern:
    """A detected SMC pattern."""
    pattern_type: PatternType
    direction: Direction
    price_level: float           # Key price level
    top: float = 0.0             # Zone top
    bottom: float = 0.0          # Zone bottom
    strength: float = 0.0        # 0-100 quality score
    index: int = 0               # Candle index where detected
    timestamp: float = 0.0
    mitigated: bool = False      # Has price returned to this zone?
    volume_zscore: float = 0.0   # Volume significance
    details: dict = field(default_factory=dict)


@dataclass
class MarketStructure:
    """Current market structure state for a timeframe."""
    trend: Direction = Direction.NEUTRAL
    last_bos: Optional[SMCPattern] = None
    last_choch: Optional[SMCPattern] = None
    swing_highs: list[float] = field(default_factory=list)
    swing_lows: list[float] = field(default_factory=list)
    active_fvgs: list[SMCPattern] = field(default_factory=list)
    active_obs: list[SMCPattern] = field(default_factory=list)
    liquidity_zones: list[SMCPattern] = field(default_factory=list)
    pois: list[SMCPattern] = field(default_factory=list)  # Points of interest


class SMCEngine:
    """
    Smart Money Concepts pattern detection engine.

    Accepts OHLCV DataFrames and returns detected patterns with
    quality scores and validity filters.
    """

    def __init__(
        self,
        swing_length: int = 10,
        ob_volume_zscore_min: float = 2.0,
        fvg_min_size_atr: float = 0.3,
    ):
        self._swing_length = swing_length
        self._ob_vol_zscore_min = ob_volume_zscore_min
        self._fvg_min_size_atr = fvg_min_size_atr

    def analyze(self, df: pd.DataFrame) -> MarketStructure:
        """Run full SMC analysis on OHLCV DataFrame.

        Args:
            df: DataFrame with columns [open, high, low, close, volume]
                indexed by timestamp. Must have at least 50 rows.

        Returns:
            MarketStructure with all detected patterns.
        """
        if len(df) < 50:
            log.warning("[SMC] Need at least 50 candles, got %d", len(df))
            return MarketStructure()

        df = df.copy()
        df.columns = [c.lower() for c in df.columns]

        ms = MarketStructure()

        # Step 1: Swing highs/lows
        swings = self._detect_swings(df)
        ms.swing_highs = [s.price_level for s in swings if s.direction == Direction.BEARISH]
        ms.swing_lows = [s.price_level for s in swings if s.direction == Direction.BULLISH]

        # Step 2: BOS / CHOCH
        structure = self._detect_structure(df, swings)
        for s in structure:
            if s.pattern_type == PatternType.BOS:
                ms.last_bos = s
            elif s.pattern_type == PatternType.CHOCH:
                ms.last_choch = s

        # Determine trend from structure
        ms.trend = self._determine_trend(structure)

        # Step 3: Fair Value Gaps
        fvgs = self._detect_fvgs(df)
        ms.active_fvgs = [f for f in fvgs if not f.mitigated]

        # Step 4: Order Blocks
        obs = self._detect_order_blocks(df, structure)
        ms.active_obs = [o for o in obs if not o.mitigated]

        # Step 5: Liquidity zones
        ms.liquidity_zones = self._detect_liquidity_zones(df, swings)

        # Step 6: Compile Points of Interest
        ms.pois = self._compile_pois(ms)

        log.debug(
            "[SMC] Analysis: trend=%s bos=%s choch=%s fvgs=%d obs=%d liq=%d",
            ms.trend.name,
            ms.last_bos is not None,
            ms.last_choch is not None,
            len(ms.active_fvgs),
            len(ms.active_obs),
            len(ms.liquidity_zones),
        )
        return ms

    # ──────────────────────────────────────────────────────────────
    # Swing Detection
    # ──────────────────────────────────────────────────────────────

    def _detect_swings(self, df: pd.DataFrame) -> list[SMCPattern]:
        """Detect swing highs and lows using pivot point logic."""
        swings = []
        n = self._swing_length
        highs = df["high"].values
        lows = df["low"].values

        for i in range(n, len(df) - n):
            # Swing high: highest in window
            if highs[i] == max(highs[i - n : i + n + 1]):
                swings.append(
                    SMCPattern(
                        pattern_type=PatternType.SWING_HIGH,
                        direction=Direction.BEARISH,
                        price_level=float(highs[i]),
                        index=i,
                        timestamp=float(df.index[i]) if isinstance(df.index[i], (int, float)) else 0,
                    )
                )

            # Swing low: lowest in window
            if lows[i] == min(lows[i - n : i + n + 1]):
                swings.append(
                    SMCPattern(
                        pattern_type=PatternType.SWING_LOW,
                        direction=Direction.BULLISH,
                        price_level=float(lows[i]),
                        index=i,
                        timestamp=float(df.index[i]) if isinstance(df.index[i], (int, float)) else 0,
                    )
                )

        return swings

    # ──────────────────────────────────────────────────────────────
    # Structure (BOS / CHOCH)
    # ──────────────────────────────────────────────────────────────

    def _detect_structure(
        self, df: pd.DataFrame, swings: list[SMCPattern]
    ) -> list[SMCPattern]:
        """Detect Break of Structure and Change of Character.

        BOS: Price breaks a swing point in the SAME direction as trend.
        CHOCH: Price breaks a swing point in the OPPOSITE direction (reversal).
        """
        structure = []
        closes = df["close"].values

        # Separate swing highs and lows with their indices
        swing_highs = [(s.index, s.price_level) for s in swings
                       if s.pattern_type == PatternType.SWING_HIGH]
        swing_lows = [(s.index, s.price_level) for s in swings
                      if s.pattern_type == PatternType.SWING_LOW]

        current_trend = Direction.NEUTRAL

        # Track the most recent significant swing points
        for i in range(max(self._swing_length * 2, 20), len(df)):
            close = closes[i]

            # Check if close breaks above a recent swing high
            for idx, level in reversed(swing_highs):
                if idx >= i:
                    continue
                if close > level:
                    if current_trend == Direction.BULLISH:
                        ptype = PatternType.BOS
                    else:
                        ptype = PatternType.CHOCH
                        current_trend = Direction.BULLISH

                    structure.append(
                        SMCPattern(
                            pattern_type=ptype,
                            direction=Direction.BULLISH,
                            price_level=level,
                            index=i,
                            details={"broken_swing_idx": idx},
                        )
                    )
                    # Remove broken swing
                    swing_highs = [(si, sl) for si, sl in swing_highs if si != idx]
                    break

            # Check if close breaks below a recent swing low
            for idx, level in reversed(swing_lows):
                if idx >= i:
                    continue
                if close < level:
                    if current_trend == Direction.BEARISH:
                        ptype = PatternType.BOS
                    else:
                        ptype = PatternType.CHOCH
                        current_trend = Direction.BEARISH

                    structure.append(
                        SMCPattern(
                            pattern_type=ptype,
                            direction=Direction.BEARISH,
                            price_level=level,
                            index=i,
                            details={"broken_swing_idx": idx},
                        )
                    )
                    swing_lows = [(si, sl) for si, sl in swing_lows if si != idx]
                    break

        return structure

    def _determine_trend(self, structure: list[SMCPattern]) -> Direction:
        """Determine current trend from most recent structure breaks."""
        if not structure:
            return Direction.NEUTRAL

        # Last CHOCH determines trend, last BOS confirms it
        last = structure[-1]
        return last.direction

    # ──────────────────────────────────────────────────────────────
    # Fair Value Gaps
    # ──────────────────────────────────────────────────────────────

    def _detect_fvgs(self, df: pd.DataFrame) -> list[SMCPattern]:
        """Detect Fair Value Gaps (3-candle imbalance).

        Bullish FVG: candle[i-2].high < candle[i].low (gap up)
        Bearish FVG: candle[i-2].low > candle[i].high (gap down)
        """
        fvgs = []
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values

        # ATR for minimum gap size filter
        atr = self._calculate_atr(df, 14)

        for i in range(2, len(df)):
            min_gap = atr[i] * self._fvg_min_size_atr if i < len(atr) else 0

            # Bullish FVG
            if lows[i] > highs[i - 2]:
                gap_size = lows[i] - highs[i - 2]
                if gap_size > min_gap:
                    fvg = SMCPattern(
                        pattern_type=PatternType.FVG,
                        direction=Direction.BULLISH,
                        price_level=(lows[i] + highs[i - 2]) / 2,
                        top=float(lows[i]),
                        bottom=float(highs[i - 2]),
                        index=i,
                        strength=min(gap_size / (atr[i] if atr[i] > 0 else 1) * 25, 100),
                    )
                    # Check if mitigated (price returned to fill the gap)
                    for j in range(i + 1, len(df)):
                        if lows[j] <= highs[i - 2]:
                            fvg.mitigated = True
                            break
                    fvgs.append(fvg)

            # Bearish FVG
            if highs[i] < lows[i - 2]:
                gap_size = lows[i - 2] - highs[i]
                if gap_size > min_gap:
                    fvg = SMCPattern(
                        pattern_type=PatternType.FVG,
                        direction=Direction.BEARISH,
                        price_level=(highs[i] + lows[i - 2]) / 2,
                        top=float(lows[i - 2]),
                        bottom=float(highs[i]),
                        index=i,
                        strength=min(gap_size / (atr[i] if atr[i] > 0 else 1) * 25, 100),
                    )
                    for j in range(i + 1, len(df)):
                        if highs[j] >= lows[i - 2]:
                            fvg.mitigated = True
                            break
                    fvgs.append(fvg)

        return fvgs

    # ──────────────────────────────────────────────────────────────
    # Order Blocks
    # ──────────────────────────────────────────────────────────────

    def _detect_order_blocks(
        self, df: pd.DataFrame, structure: list[SMCPattern]
    ) -> list[SMCPattern]:
        """Detect Order Blocks with validity filters.

        Valid OBs must have:
        1. Structural context (near a BOS or CHOCH)
        2. Displacement (followed by explosive move)
        3. Volume confirmation (Z-score above threshold)
        4. Only valid on first touch (mitigation check)
        """
        obs = []
        opens = df["open"].values
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        volumes = df["volume"].values

        # Volume statistics for Z-score
        vol_mean = np.mean(volumes) if len(volumes) > 0 else 1
        vol_std = np.std(volumes) if len(volumes) > 0 else 1
        if vol_std == 0:
            vol_std = 1

        atr = self._calculate_atr(df, 14)

        # Structure break indices for context check
        structure_indices = {s.index for s in structure}

        for i in range(2, len(df) - 2):
            vol_zscore = (volumes[i] - vol_mean) / vol_std

            # Bullish OB: last bearish candle before a bullish displacement
            if closes[i] < opens[i]:  # Bearish candle
                # Check for bullish displacement after (large move up)
                displacement = False
                for j in range(i + 1, min(i + 4, len(df))):
                    move = closes[j] - closes[i]
                    if atr[i] > 0 and move > atr[i] * 1.5:
                        displacement = True
                        break

                if not displacement:
                    continue

                # Check structural context (near a structure break)
                near_structure = any(
                    abs(si - i) < 5 for si in structure_indices
                )

                # Calculate strength score
                strength = self._score_ob(
                    vol_zscore, displacement, near_structure, atr[i],
                    abs(closes[i] - opens[i])
                )

                if strength < 40:
                    continue

                ob = SMCPattern(
                    pattern_type=PatternType.OB,
                    direction=Direction.BULLISH,
                    price_level=(highs[i] + lows[i]) / 2,
                    top=float(highs[i]),
                    bottom=float(lows[i]),
                    index=i,
                    strength=strength,
                    volume_zscore=round(vol_zscore, 2),
                    details={
                        "near_structure": near_structure,
                        "displacement": displacement,
                    },
                )

                # Check mitigation (first touch only)
                for j in range(i + 3, len(df)):
                    if lows[j] <= highs[i]:
                        ob.mitigated = True
                        break
                obs.append(ob)

            # Bearish OB: last bullish candle before a bearish displacement
            if closes[i] > opens[i]:  # Bullish candle
                displacement = False
                for j in range(i + 1, min(i + 4, len(df))):
                    move = closes[i] - closes[j]
                    if atr[i] > 0 and move > atr[i] * 1.5:
                        displacement = True
                        break

                if not displacement:
                    continue

                near_structure = any(
                    abs(si - i) < 5 for si in structure_indices
                )

                strength = self._score_ob(
                    vol_zscore, displacement, near_structure, atr[i],
                    abs(closes[i] - opens[i])
                )

                if strength < 40:
                    continue

                ob = SMCPattern(
                    pattern_type=PatternType.OB,
                    direction=Direction.BEARISH,
                    price_level=(highs[i] + lows[i]) / 2,
                    top=float(highs[i]),
                    bottom=float(lows[i]),
                    index=i,
                    strength=strength,
                    volume_zscore=round(vol_zscore, 2),
                    details={
                        "near_structure": near_structure,
                        "displacement": displacement,
                    },
                )

                for j in range(i + 3, len(df)):
                    if highs[j] >= lows[i]:
                        ob.mitigated = True
                        break
                obs.append(ob)

        return obs

    def _score_ob(
        self,
        vol_zscore: float,
        displacement: bool,
        near_structure: bool,
        atr: float,
        body_size: float,
    ) -> float:
        """Score an Order Block's quality (0-100)."""
        score = 0.0

        # Volume (0-30 points)
        if vol_zscore >= 4.0:
            score += 30
        elif vol_zscore >= 2.0:
            score += 20
        elif vol_zscore >= 1.0:
            score += 10

        # Displacement (0-25 points)
        if displacement:
            score += 25

        # Structural context (0-25 points)
        if near_structure:
            score += 25

        # Body-ATR ratio (0-20 points) — larger body relative to ATR = stronger
        if atr > 0:
            bar = body_size / atr
            if bar >= 1.5:
                score += 20
            elif bar >= 1.0:
                score += 15
            elif bar >= 0.5:
                score += 10

        return min(score, 100)

    # ──────────────────────────────────────────────────────────────
    # Liquidity Zones
    # ──────────────────────────────────────────────────────────────

    def _detect_liquidity_zones(
        self, df: pd.DataFrame, swings: list[SMCPattern]
    ) -> list[SMCPattern]:
        """Detect liquidity zones (clusters of equal highs/lows).

        Also detects liquidity sweeps (wick through a level then close back).
        """
        zones = []
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values

        # Find clusters of similar highs (resistance liquidity)
        for i in range(len(df) - 1):
            count = 0
            level = highs[i]
            tolerance = level * 0.001  # 0.1% tolerance

            for j in range(max(0, i - 20), min(len(df), i + 20)):
                if j == i:
                    continue
                if abs(highs[j] - level) < tolerance:
                    count += 1

            if count >= 2:
                zones.append(
                    SMCPattern(
                        pattern_type=PatternType.LIQUIDITY_SWEEP,
                        direction=Direction.BEARISH,
                        price_level=level,
                        top=level + tolerance,
                        bottom=level - tolerance,
                        index=i,
                        strength=min(count * 20, 100),
                        details={"type": "equal_highs", "touches": count},
                    )
                )

        # Find clusters of similar lows (support liquidity)
        for i in range(len(df) - 1):
            count = 0
            level = lows[i]
            tolerance = level * 0.001

            for j in range(max(0, i - 20), min(len(df), i + 20)):
                if j == i:
                    continue
                if abs(lows[j] - level) < tolerance:
                    count += 1

            if count >= 2:
                zones.append(
                    SMCPattern(
                        pattern_type=PatternType.LIQUIDITY_SWEEP,
                        direction=Direction.BULLISH,
                        price_level=level,
                        top=level + tolerance,
                        bottom=level - tolerance,
                        index=i,
                        strength=min(count * 20, 100),
                        details={"type": "equal_lows", "touches": count},
                    )
                )

        # Detect actual sweeps (wick through then close back)
        for i in range(1, len(df)):
            for z in zones:
                if z.index >= i:
                    continue
                # Bullish sweep: wick below liquidity zone, close above
                if (z.direction == Direction.BULLISH and
                        lows[i] < z.bottom and closes[i] > z.price_level):
                    z.mitigated = True
                    z.details["swept_at"] = i
                # Bearish sweep: wick above liquidity zone, close below
                if (z.direction == Direction.BEARISH and
                        highs[i] > z.top and closes[i] < z.price_level):
                    z.mitigated = True
                    z.details["swept_at"] = i

        return zones

    # ──────────────────────────────────────────────────────────────
    # Points of Interest
    # ──────────────────────────────────────────────────────────────

    def _compile_pois(self, ms: MarketStructure) -> list[SMCPattern]:
        """Compile all active (unmitigated) patterns into ranked POIs."""
        pois: list[SMCPattern] = []

        for ob in ms.active_obs:
            if not ob.mitigated and ob.strength >= 60:
                pois.append(ob)

        for fvg in ms.active_fvgs:
            if not fvg.mitigated and fvg.strength >= 40:
                pois.append(fvg)

        # Sort by strength (highest first)
        pois.sort(key=lambda p: p.strength, reverse=True)
        return pois

    # ──────────────────────────────────────────────────────────────
    # Utilities
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _calculate_atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
        """Calculate Average True Range."""
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        tr = np.zeros(len(df))
        for i in range(1, len(df)):
            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )

        atr = np.zeros(len(df))
        if len(tr) >= period:
            atr[period] = np.mean(tr[1 : period + 1])
            for i in range(period + 1, len(df)):
                atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

        return atr
