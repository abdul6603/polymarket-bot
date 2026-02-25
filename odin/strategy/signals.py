"""Signal generation — combines SMC multi-TF analysis with macro filter."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from odin.strategy.smc_engine import Direction

log = logging.getLogger(__name__)


@dataclass
class TradeSignal:
    """Final actionable trade signal."""
    symbol: str
    direction: str                  # "LONG" or "SHORT"
    confidence: float               # 0.0-1.0

    # Entry zone
    entry_price: float = 0.0       # Ideal entry (OB/FVG midpoint)
    entry_zone_top: float = 0.0
    entry_zone_bottom: float = 0.0

    # Risk levels
    stop_loss: float = 0.0
    take_profit_1: float = 0.0
    take_profit_2: float = 0.0
    risk_reward: float = 0.0

    # Sizing inputs
    macro_multiplier: float = 1.0   # 0.0-1.0 from macro filter
    macro_regime: str = ""
    macro_score: int = 0

    # Position scaling
    scale_ob_pct: float = 0.50      # % at OB level
    scale_fvg_pct: float = 0.30     # % at FVG midpoint
    scale_extreme_pct: float = 0.20 # % at zone extreme

    # Conviction (filled by conviction engine)
    conviction_score: float = 0.0                          # 0-100
    conviction_breakdown: dict = field(default_factory=dict)
    risk_multiplier: float = 1.0                           # 0.0-1.0
    llm_risk_usd: float = 0.0                              # LLM-decided risk (0 = use default)
    decision_id: str = ""                                  # journal tracking ID

    # Exit management context
    atr: float = 0.0                # ATR at entry time (for trailing stops)

    # Context
    entry_reason: str = ""
    timeframe_alignment: str = ""
    smc_patterns: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    @property
    def tradeable(self) -> bool:
        # If conviction engine scored this, use conviction threshold
        if self.conviction_score > 0:
            return (
                self.conviction_score >= 20
                and self.risk_reward >= 1.5
                and self.stop_loss > 0
            )
        return (
            self.confidence >= 0.50
            and self.macro_multiplier > 0.0
            and self.risk_reward >= 1.5
            and self.stop_loss > 0
        )

    @property
    def effective_confidence(self) -> float:
        """Confidence adjusted by macro filter."""
        return round(self.confidence * self.macro_multiplier, 3)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "confidence": self.confidence,
            "effective_confidence": self.effective_confidence,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit_1": self.take_profit_1,
            "take_profit_2": self.take_profit_2,
            "risk_reward": self.risk_reward,
            "macro_regime": self.macro_regime,
            "macro_score": self.macro_score,
            "macro_multiplier": self.macro_multiplier,
            "conviction_score": self.conviction_score,
            "conviction_breakdown": self.conviction_breakdown,
            "risk_multiplier": self.risk_multiplier,
            "decision_id": self.decision_id,
            "tradeable": self.tradeable,
            "smc_patterns": self.smc_patterns,
            "reasons": self.reasons,
            "timestamp": self.timestamp,
        }
