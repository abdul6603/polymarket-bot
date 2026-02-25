"""Sentiment + OnChain Fusion — multi-source signal for 15-60 min predictions.

Fuses: Atlas news sentiment, CoinGlass on-chain flow,
wallet tracking data, and Fear & Greed index.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("odin.skills.sentiment_fusion")

ATLAS_SENTIMENT_FILE = Path.home() / "atlas" / "data" / "news_sentiment.json"
ATLAS_KB_FILE = Path.home() / "atlas" / "data" / "knowledge_base.json"


@dataclass
class SentimentSignal:
    """Fused sentiment signal for a symbol."""
    symbol: str
    direction: str          # LONG, SHORT, NEUTRAL
    strength: float         # 0-100
    horizon_minutes: int    # 15, 30, 60
    sources: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    confidence: float = 0.0  # 0-1.0


class SentimentFusion:
    """Multi-source sentiment analysis and fusion."""

    def __init__(self):
        self._last_signals: dict[str, SentimentSignal] = {}
        self._scan_count = 0

    def analyze(
        self,
        symbol: str,
        coinglass_data: dict | None = None,
        atlas_override: dict | None = None,
    ) -> SentimentSignal:
        """Fuse all sentiment sources for a symbol.

        Sources weighted:
          - Atlas news sentiment:    30%
          - CoinGlass flow data:     25%
          - Fear & Greed:            20%
          - Wallet/exchange flow:    15%
          - Social momentum:         10%
        """
        self._scan_count += 1
        sources: dict[str, dict] = {}
        composite = 0.0  # -1 (bearish) to +1 (bullish)
        reasons: list[str] = []

        # 1. Atlas news sentiment (30%)
        atlas = self._get_atlas_sentiment(symbol, atlas_override)
        sources["atlas"] = atlas
        atlas_score = atlas.get("score", 0)
        composite += atlas_score * 0.30
        if abs(atlas_score) > 0.3:
            reasons.append(f"Atlas: {'bullish' if atlas_score > 0 else 'bearish'} ({atlas_score:+.2f})")

        # 2. CoinGlass on-chain flow (25%)
        if coinglass_data:
            chain = self._score_onchain(coinglass_data)
            sources["onchain"] = chain
            composite += chain["score"] * 0.25
            if chain["reasons"]:
                reasons.extend(chain["reasons"][:2])

        # 3. Fear & Greed index (20%)
        fg = self._get_fear_greed(coinglass_data)
        sources["fear_greed"] = fg
        composite += fg["score"] * 0.20
        if fg.get("value"):
            reasons.append(f"F&G: {fg['value']} ({fg.get('label', '')})")

        # 4. Wallet/exchange flow from CoinGlass (15%)
        if coinglass_data:
            flow = self._score_exchange_flow(coinglass_data)
            sources["exchange_flow"] = flow
            composite += flow["score"] * 0.15
            if flow["reasons"]:
                reasons.extend(flow["reasons"][:1])

        # 5. Social momentum from Atlas KB (10%)
        social = self._get_social_momentum(symbol)
        sources["social"] = social
        composite += social["score"] * 0.10

        # Convert composite (-1 to +1) to direction/strength
        if composite > 0.15:
            direction = "LONG"
        elif composite < -0.15:
            direction = "SHORT"
        else:
            direction = "NEUTRAL"

        strength = min(abs(composite) * 100, 100)
        confidence = min(abs(composite), 1.0)

        # Horizon based on signal strength
        if strength > 70:
            horizon = 60
        elif strength > 40:
            horizon = 30
        else:
            horizon = 15

        signal = SentimentSignal(
            symbol=symbol,
            direction=direction,
            strength=round(strength, 1),
            horizon_minutes=horizon,
            sources=sources,
            reasons=reasons,
            confidence=round(confidence, 3),
        )

        self._last_signals[symbol] = signal

        log.info(
            "[SENTIMENT] %s: %s str=%.0f conf=%.2f (%d sources, %dmin horizon)",
            symbol, direction, strength, confidence, len(sources), horizon,
        )
        return signal

    def _get_atlas_sentiment(self, symbol: str, override: dict | None = None) -> dict:
        """Read Atlas news sentiment file."""
        if override:
            return override

        asset = symbol.replace("USDT", "")
        try:
            if ATLAS_SENTIMENT_FILE.exists():
                data = json.loads(ATLAS_SENTIMENT_FILE.read_text())
                for key in [asset, asset.lower(), f"{asset}USD"]:
                    if key in data:
                        return data[key]
        except Exception:
            pass
        return {"score": 0, "direction": "NEUTRAL", "headlines": []}

    def _score_onchain(self, cg: dict) -> dict:
        """Score CoinGlass on-chain metrics."""
        score = 0.0
        reasons = []

        # Funding rate signal
        funding = cg.get("funding_rate", 0)
        if funding > 0.005:
            score -= 0.4
            reasons.append(f"High funding {funding:.4f} (bearish)")
        elif funding < -0.003:
            score += 0.3
            reasons.append(f"Negative funding {funding:.4f} (bullish)")

        # OI change signal
        oi_change = cg.get("oi_change_1h", 0)
        if oi_change > 5:
            score -= 0.2  # Rapid OI build = potential flush
            reasons.append(f"OI surge +{oi_change:.1f}%")
        elif oi_change < -5:
            score += 0.2  # Leverage flushed = healthier
            reasons.append(f"OI flushed {oi_change:.1f}%")

        # L/S ratio
        long_ratio = cg.get("long_ratio", 0.5)
        if long_ratio > 0.60:
            score -= 0.3
            reasons.append(f"Longs crowded {long_ratio:.0%}")
        elif long_ratio < 0.40:
            score += 0.3
            reasons.append(f"Shorts crowded {1-long_ratio:.0%}")

        return {"score": max(-1, min(1, score)), "reasons": reasons}

    def _get_fear_greed(self, cg: dict | None) -> dict:
        """Get Fear & Greed index."""
        if cg and "fear_greed" in cg:
            fg = cg["fear_greed"]
            value = fg.get("value", 50)
            # Normalize to -1 to +1 (0=extreme fear=-1, 100=extreme greed=+1)
            score = (value - 50) / 50
            label = (
                "Extreme Fear" if value < 25
                else "Fear" if value < 45
                else "Neutral" if value < 55
                else "Greed" if value < 75
                else "Extreme Greed"
            )
            return {"score": score, "value": value, "label": label}
        return {"score": 0, "value": None, "label": "N/A"}

    def _score_exchange_flow(self, cg: dict) -> dict:
        """Score exchange inflow/outflow."""
        score = 0.0
        reasons = []

        net_flow = cg.get("exchange_net_flow", 0)
        if net_flow > 0:
            score -= 0.3  # Inflow = selling pressure
            reasons.append(f"Exchange inflow (sell pressure)")
        elif net_flow < 0:
            score += 0.3  # Outflow = accumulation
            reasons.append(f"Exchange outflow (accumulation)")

        return {"score": score, "reasons": reasons}

    def _get_social_momentum(self, symbol: str) -> dict:
        """Read social momentum from Atlas KB."""
        try:
            if ATLAS_KB_FILE.exists():
                data = json.loads(ATLAS_KB_FILE.read_text())
                for entry in data.get("entries", [])[-20:]:
                    if symbol.replace("USDT", "").lower() in entry.get("content", "").lower():
                        sentiment = entry.get("sentiment", 0)
                        return {"score": sentiment, "source": "atlas_kb"}
        except Exception:
            pass
        return {"score": 0, "source": "none"}

    def get_status(self) -> dict:
        return {
            "scans": self._scan_count,
            "signals": {
                sym: {
                    "direction": s.direction,
                    "strength": s.strength,
                    "horizon": s.horizon_minutes,
                }
                for sym, s in self._last_signals.items()
            },
        }
