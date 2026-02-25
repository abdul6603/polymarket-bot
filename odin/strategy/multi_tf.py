"""Multi-timeframe analysis pipeline: Daily → 4H → 15m.

HTF (Daily)  — Overall bias, key levels, major OBs
MTF (4H)     — Structure confirmation, entry zones
LTF (15m)    — Precise entry trigger with tight stops

Rule: If LTF signal fights HTF bias → SKIP or reduce size.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from odin.strategy.smc_engine import (
    Direction,
    MarketStructure,
    PatternType,
    SMCEngine,
    SMCPattern,
)

log = logging.getLogger(__name__)


@dataclass
class TimeframeAnalysis:
    """Analysis result for a single timeframe."""
    timeframe: str
    structure: MarketStructure
    bias: Direction = Direction.NEUTRAL
    key_levels: list[float] = field(default_factory=list)
    entry_zones: list[SMCPattern] = field(default_factory=list)


@dataclass
class MultiTFSignal:
    """Combined multi-timeframe trading signal."""
    direction: Direction = Direction.NEUTRAL
    confidence: float = 0.0          # 0.0-1.0
    entry_zone_top: float = 0.0
    entry_zone_bottom: float = 0.0
    stop_loss: float = 0.0
    take_profit_1: float = 0.0       # Conservative TP
    take_profit_2: float = 0.0       # Extended TP
    risk_reward: float = 0.0

    htf: Optional[TimeframeAnalysis] = None
    mtf: Optional[TimeframeAnalysis] = None
    ltf: Optional[TimeframeAnalysis] = None

    reasons: list[str] = field(default_factory=list)

    @property
    def tradeable(self) -> bool:
        return self.confidence >= 0.6 and self.direction != Direction.NEUTRAL


class MultiTimeframeAnalyzer:
    """
    Multi-timeframe SMC analysis pipeline.

    Signal Flow:
        HTF trend bias → MTF structure confirms → MTF OB/FVG entry zone
        → LTF trigger (BOS/CHOCH on LTF) → Enter with tight SL

    Only trades when HTF and MTF agree on direction.
    LTF is used purely for entry timing.
    """

    def __init__(
        self,
        htf_label: str = "1D",
        mtf_label: str = "4H",
        ltf_label: str = "15m",
    ):
        self._htf_label = htf_label
        self._mtf_label = mtf_label
        self._ltf_label = ltf_label
        self._smc = SMCEngine()

    def analyze(
        self,
        htf_df: pd.DataFrame,
        mtf_df: pd.DataFrame,
        ltf_df: pd.DataFrame,
        current_price: float = 0.0,
    ) -> MultiTFSignal:
        """Run full multi-timeframe analysis.

        Args:
            htf_df: Daily OHLCV DataFrame
            mtf_df: 4H OHLCV DataFrame
            ltf_df: 15m OHLCV DataFrame
            current_price: Current market price

        Returns:
            MultiTFSignal with direction, confidence, entry zone, SL/TP
        """
        signal = MultiTFSignal()

        # Step 1: HTF analysis (bias)
        htf = self._analyze_tf(htf_df, self._htf_label)
        signal.htf = htf

        if htf.bias == Direction.NEUTRAL:
            signal.reasons.append("HTF neutral — no clear bias")
            return signal

        # Step 2: MTF analysis (structure + entry zones)
        mtf = self._analyze_tf(mtf_df, self._mtf_label)
        signal.mtf = mtf

        # Check HTF-MTF alignment
        if mtf.structure.trend != htf.bias and mtf.structure.trend != Direction.NEUTRAL:
            signal.reasons.append(
                f"HTF ({htf.bias.name}) vs MTF ({mtf.structure.trend.name}) conflict"
            )
            signal.confidence = 0.2
            return signal

        # Step 3: LTF analysis (entry trigger)
        ltf = self._analyze_tf(ltf_df, self._ltf_label)
        signal.ltf = ltf

        # Step 4: Find entry zone from MTF POIs
        entry_zone = self._find_best_entry_zone(
            mtf.structure, htf.bias, current_price
        )

        if not entry_zone:
            signal.reasons.append("No valid MTF entry zone near current price")
            signal.direction = htf.bias
            signal.confidence = 0.3
            return signal

        # Step 5: Check LTF trigger
        ltf_triggered = self._check_ltf_trigger(ltf.structure, htf.bias)

        # Step 6: Build final signal
        signal.direction = htf.bias
        signal.entry_zone_top = entry_zone.top
        signal.entry_zone_bottom = entry_zone.bottom

        # Calculate SL and TP
        self._set_sl_tp(signal, entry_zone, htf.structure, current_price)

        # Calculate confidence
        confidence = 0.0
        # HTF alignment (0.25)
        confidence += 0.25
        signal.reasons.append(f"HTF bias: {htf.bias.name}")

        # MTF confirmation (0.25)
        if mtf.structure.trend == htf.bias:
            confidence += 0.25
            signal.reasons.append(f"MTF confirms: {mtf.structure.trend.name}")
        elif mtf.structure.trend == Direction.NEUTRAL:
            confidence += 0.10
            signal.reasons.append("MTF neutral (partial confirm)")

        # Entry zone quality (0.25)
        zone_quality = entry_zone.strength / 100
        confidence += 0.25 * zone_quality
        signal.reasons.append(
            f"Entry zone: {entry_zone.pattern_type.value} "
            f"strength={entry_zone.strength:.0f}"
        )

        # LTF trigger (0.25)
        if ltf_triggered:
            confidence += 0.25
            signal.reasons.append("LTF trigger confirmed")
        else:
            signal.reasons.append("LTF trigger NOT yet confirmed — wait")

        signal.confidence = round(min(confidence, 1.0), 3)

        # Calculate R:R
        if signal.stop_loss > 0 and signal.entry_zone_bottom > 0:
            if signal.direction == Direction.BULLISH:
                risk = signal.entry_zone_bottom - signal.stop_loss
                reward = signal.take_profit_1 - signal.entry_zone_top
            else:
                risk = signal.stop_loss - signal.entry_zone_top
                reward = signal.entry_zone_bottom - signal.take_profit_1

            if risk > 0:
                signal.risk_reward = round(reward / risk, 2)

        return signal

    def _analyze_tf(
        self, df: pd.DataFrame, label: str
    ) -> TimeframeAnalysis:
        """Analyze a single timeframe."""
        ms = self._smc.analyze(df)

        # Determine bias from structure
        bias = ms.trend
        if ms.last_choch:
            bias = ms.last_choch.direction

        # Key levels from swing points
        key_levels = sorted(set(ms.swing_highs[-5:] + ms.swing_lows[-5:]))

        # Entry zones from POIs
        entry_zones = ms.pois[:5]  # Top 5 by strength

        return TimeframeAnalysis(
            timeframe=label,
            structure=ms,
            bias=bias,
            key_levels=key_levels,
            entry_zones=entry_zones,
        )

    def _find_best_entry_zone(
        self,
        mtf_structure: MarketStructure,
        bias: Direction,
        current_price: float,
    ) -> Optional[SMCPattern]:
        """Find the best entry zone from MTF that aligns with HTF bias.

        For longs: look for bullish OBs/FVGs below current price (within 3%)
        For shorts: look for bearish OBs/FVGs above current price (within 3%)

        Zone must be within 3% of current price to be actionable.
        """
        max_distance = 0.03  # Zone must be within 3% of current price
        candidates = []

        for poi in mtf_structure.pois:
            if poi.mitigated:
                continue

            # Check zone is within proximity of current price
            zone_mid = (poi.top + poi.bottom) / 2
            distance = abs(zone_mid - current_price) / current_price
            if distance > max_distance:
                continue

            if bias == Direction.BULLISH and poi.direction == Direction.BULLISH:
                if poi.top <= current_price * 1.005:
                    candidates.append(poi)

            elif bias == Direction.BEARISH and poi.direction == Direction.BEARISH:
                if poi.bottom >= current_price * 0.995:
                    candidates.append(poi)

        if not candidates:
            return None

        # Return strongest
        candidates.sort(key=lambda p: p.strength, reverse=True)
        return candidates[0]

    def _check_ltf_trigger(
        self, ltf_structure: MarketStructure, bias: Direction
    ) -> bool:
        """Check if LTF has triggered an entry signal.

        For longs: LTF should show bullish BOS or CHOCH
        For shorts: LTF should show bearish BOS or CHOCH
        """
        if ltf_structure.last_bos and ltf_structure.last_bos.direction == bias:
            return True
        if ltf_structure.last_choch and ltf_structure.last_choch.direction == bias:
            return True
        return False

    def _set_sl_tp(
        self,
        signal: MultiTFSignal,
        entry_zone: SMCPattern,
        htf_structure: MarketStructure,
        current_price: float,
    ) -> None:
        """Calculate stop-loss and take-profit levels.

        SL: Beyond entry zone with minimum 0.5% buffer from entry.
        TP1: Always 2R from entry. TP2: 3R from entry.
        """
        min_sl_pct = 0.005  # 0.5% minimum SL distance

        if signal.direction == Direction.BULLISH:
            zone_height = entry_zone.top - entry_zone.bottom
            raw_sl = entry_zone.bottom - max(zone_height * 0.5, 1.0)
            min_sl_price = current_price * (1 - min_sl_pct)
            signal.stop_loss = round(min(raw_sl, min_sl_price), 2)

            # TP at fixed 2R and 3R
            risk = current_price - signal.stop_loss
            signal.take_profit_1 = round(current_price + risk * 2, 2)
            signal.take_profit_2 = round(current_price + risk * 3, 2)

        elif signal.direction == Direction.BEARISH:
            zone_height = entry_zone.top - entry_zone.bottom
            raw_sl = entry_zone.top + max(zone_height * 0.5, 1.0)
            min_sl_price = current_price * (1 + min_sl_pct)
            signal.stop_loss = round(max(raw_sl, min_sl_price), 2)

            # TP at fixed 2R and 3R
            risk = signal.stop_loss - current_price
            signal.take_profit_1 = round(current_price - risk * 2, 2)
            signal.take_profit_2 = round(current_price - risk * 3, 2)

