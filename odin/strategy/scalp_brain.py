"""Scalp Brain — rule-based momentum engine for instant scalp decisions.

No LLM call. Pure price action + indicators on short timeframes.
Decision in <0.1 second across any coin.

Entry rules (ALL must pass):
  1. Momentum: price moved in trigger direction (confirmed by sniper)
  2. Volume: last candle volume > 1.3x avg (someone's buying/selling)
  3. Trend: price on right side of 20 EMA on 5m (don't fight the trend)
  4. RSI: not overbought/oversold against our direction (14-period on 5m)
  5. Spread: SL distance 0.3-1.5% (tight enough for scalp)

Exit profile (handled by exit_manager.py):
  - 50% at 0.7R, full at 1.2R, 20 min max hold
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger("odin.scalp_brain")


@dataclass
class ScalpDecision:
    """Output of scalp brain analysis."""
    trade: bool                # Take the trade?
    direction: str = "FLAT"    # LONG / SHORT / FLAT
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    risk_usd: float = 0.0
    confidence: float = 0.0    # 0.0-1.0
    conviction_score: float = 0.0  # 0-100
    reasons: list[str] = None

    def __post_init__(self):
        if self.reasons is None:
            self.reasons = []


class ScalpBrain:
    """Rule-based scalp engine. Zero LLM calls. Instant decisions."""

    def __init__(
        self,
        ema_period: int = 20,
        rsi_period: int = 14,
        volume_mult: float = 1.3,
        sl_min_pct: float = 0.2,
        sl_max_pct: float = 1.5,
        rsi_long_max: float = 75.0,   # Don't long above this RSI
        rsi_short_min: float = 25.0,  # Don't short below this RSI
        min_score: int = 60,          # Minimum score to trade (0-100)
        base_risk_usd: float = 30.0,  # Base risk for scalps
    ):
        self._ema_period = ema_period
        self._rsi_period = rsi_period
        self._volume_mult = volume_mult
        self._sl_min_pct = sl_min_pct
        self._sl_max_pct = sl_max_pct
        self._rsi_long_max = rsi_long_max
        self._rsi_short_min = rsi_short_min
        self._min_score = min_score
        self._base_risk_usd = base_risk_usd

    def analyze(
        self,
        ltf_df: pd.DataFrame,
        direction_hint: str,
        current_price: float,
        move_pct: float = 0.0,
    ) -> ScalpDecision:
        """Instant scalp decision from short-timeframe candles.

        Args:
            ltf_df: 5m or 15m candles (need at least 25 bars)
            direction_hint: "LONG" or "SHORT" from sniper trigger
            current_price: latest WS price
            move_pct: how much the coin just moved (from sniper)

        Returns:
            ScalpDecision with trade=True/False
        """
        if ltf_df is None or len(ltf_df) < 25:
            return ScalpDecision(trade=False, reasons=["not enough candles"])

        close = ltf_df["close"].astype(float).values
        high = ltf_df["high"].astype(float).values
        low = ltf_df["low"].astype(float).values
        volume = ltf_df["volume"].astype(float).values

        score = 0
        reasons = []

        # ── 1. EMA Trend (25 points) ──
        ema = self._ema(close, self._ema_period)
        if ema[-1] <= 0:
            return ScalpDecision(trade=False, reasons=["EMA calc failed"])

        if direction_hint == "LONG" and current_price > ema[-1]:
            score += 25
            reasons.append(f"above EMA20 ({current_price:.4f} > {ema[-1]:.4f})")
        elif direction_hint == "SHORT" and current_price < ema[-1]:
            score += 25
            reasons.append(f"below EMA20 ({current_price:.4f} < {ema[-1]:.4f})")
        else:
            reasons.append(f"wrong side of EMA20")

        # ── 2. Volume Confirmation (20 points) ──
        avg_vol = np.mean(volume[-20:-1]) if len(volume) >= 21 else np.mean(volume[:-1])
        last_vol = volume[-1]
        if avg_vol > 0 and last_vol >= avg_vol * self._volume_mult:
            score += 20
            vol_ratio = last_vol / avg_vol
            reasons.append(f"volume {vol_ratio:.1f}x avg")
        elif avg_vol > 0 and last_vol >= avg_vol * 0.8:
            # Acceptable volume — partial credit
            score += 10
            reasons.append(f"volume OK ({last_vol / avg_vol:.1f}x)")
        else:
            reasons.append(f"low volume ({last_vol / max(avg_vol, 1):.1f}x)")

        # ── 3. RSI Check (20 points) ──
        rsi = self._rsi(close, self._rsi_period)
        if rsi > 0:
            if direction_hint == "LONG" and rsi < self._rsi_long_max:
                score += 20
                reasons.append(f"RSI={rsi:.0f} (room to run)")
            elif direction_hint == "SHORT" and rsi > self._rsi_short_min:
                score += 20
                reasons.append(f"RSI={rsi:.0f} (room to fall)")
            elif direction_hint == "LONG" and rsi < 80:
                score += 10
                reasons.append(f"RSI={rsi:.0f} (borderline high)")
            elif direction_hint == "SHORT" and rsi > 20:
                score += 10
                reasons.append(f"RSI={rsi:.0f} (borderline low)")
            else:
                reasons.append(f"RSI={rsi:.0f} (exhausted)")

        # ── 4. Momentum Strength (20 points) ──
        # Check last 3 candles — are they confirming the direction?
        last3_move = (close[-1] - close[-4]) / close[-4] * 100 if len(close) >= 4 else 0
        if direction_hint == "LONG" and last3_move > 0.2:
            score += 20
            reasons.append(f"momentum +{last3_move:.1f}% (3 bars)")
        elif direction_hint == "SHORT" and last3_move < -0.2:
            score += 20
            reasons.append(f"momentum {last3_move:.1f}% (3 bars)")
        elif abs(last3_move) > 0.1:
            score += 10
            reasons.append(f"weak momentum {last3_move:.1f}%")
        else:
            reasons.append(f"no momentum ({last3_move:.1f}%)")

        # ── 5. Trigger Strength Bonus (15 points) ──
        # Bigger sniper trigger = more confidence
        if move_pct >= 1.0:
            score += 15
            reasons.append(f"strong trigger ({move_pct:.1f}%)")
        elif move_pct >= 0.6:
            score += 10
            reasons.append(f"decent trigger ({move_pct:.1f}%)")
        else:
            score += 5
            reasons.append(f"mild trigger ({move_pct:.1f}%)")

        # ── Decision ──
        if score < self._min_score:
            log.info("[SCALP_BRAIN] %s score=%d SKIP: %s",
                     direction_hint, score, " | ".join(reasons))
            return ScalpDecision(
                trade=False,
                direction="FLAT",
                confidence=score / 100,
                conviction_score=score,
                reasons=reasons,
            )

        # ── Calculate SL/TP ──
        # SL: use recent swing low/high or ATR-based
        atr = self._atr(high, low, close, 14)
        if atr <= 0:
            atr = current_price * 0.005  # Fallback: 0.5%

        if direction_hint == "LONG":
            # SL below recent low or 1 ATR below entry
            recent_low = np.min(low[-5:])
            sl_atr = current_price - atr * 1.2
            stop_loss = max(recent_low * 0.999, sl_atr)

            sl_dist_pct = (current_price - stop_loss) / current_price * 100
            if sl_dist_pct < self._sl_min_pct:
                stop_loss = current_price * (1 - self._sl_min_pct / 100)
            elif sl_dist_pct > self._sl_max_pct:
                stop_loss = current_price * (1 - self._sl_max_pct / 100)

            sl_dist = current_price - stop_loss
            take_profit = current_price + sl_dist * 1.5  # 1.5 R:R
        else:
            recent_high = np.max(high[-5:])
            sl_atr = current_price + atr * 1.2
            stop_loss = min(recent_high * 1.001, sl_atr)

            sl_dist_pct = (stop_loss - current_price) / current_price * 100
            if sl_dist_pct < self._sl_min_pct:
                stop_loss = current_price * (1 + self._sl_min_pct / 100)
            elif sl_dist_pct > self._sl_max_pct:
                stop_loss = current_price * (1 + self._sl_max_pct / 100)

            sl_dist = stop_loss - current_price
            take_profit = current_price - sl_dist * 1.5

        # Risk: scale with score (higher score = more risk)
        risk_mult = 0.6 + (score - self._min_score) / 80  # 0.6x to 1.1x
        risk_usd = round(self._base_risk_usd * min(risk_mult, 1.2), 2)

        confidence = score / 100
        log.info("[SCALP_BRAIN] %s score=%d TRADE: %s | risk=$%.0f | %s",
                 direction_hint, score, direction_hint, risk_usd,
                 " | ".join(reasons))

        return ScalpDecision(
            trade=True,
            direction=direction_hint,
            entry_price=current_price,
            stop_loss=round(stop_loss, 6),
            take_profit=round(take_profit, 6),
            risk_usd=risk_usd,
            confidence=confidence,
            conviction_score=score,
            reasons=reasons,
        )

    # ── Indicator helpers (numpy, no dependencies) ──

    @staticmethod
    def _ema(data: np.ndarray, period: int) -> np.ndarray:
        """Exponential Moving Average."""
        if len(data) < period:
            return np.zeros_like(data)
        alpha = 2 / (period + 1)
        ema = np.zeros_like(data, dtype=float)
        ema[period - 1] = np.mean(data[:period])
        for i in range(period, len(data)):
            ema[i] = alpha * data[i] + (1 - alpha) * ema[i - 1]
        return ema

    @staticmethod
    def _rsi(close: np.ndarray, period: int = 14) -> float:
        """RSI (last value only)."""
        if len(close) < period + 1:
            return 50.0  # Neutral default
        deltas = np.diff(close[-(period + 1):])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)

    @staticmethod
    def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
        """Average True Range (last value)."""
        if len(high) < period + 1:
            return 0.0
        tr = np.maximum(
            high[-period:] - low[-period:],
            np.maximum(
                np.abs(high[-period:] - close[-period - 1:-1]),
                np.abs(low[-period:] - close[-period - 1:-1]),
            ),
        )
        return float(np.mean(tr))
