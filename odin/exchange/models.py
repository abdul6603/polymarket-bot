"""Data models for exchange objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeSide(Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"


class OrderType(Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class Direction(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class MarginMode(Enum):
    CROSS = "CROSS"
    ISOLATED = "ISOLATED"


@dataclass
class Candle:
    """OHLCV candlestick data."""

    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float
    symbol: str = ""
    interval: str = ""

    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)

    @property
    def total_range(self) -> float:
        return self.high - self.low

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def dt(self) -> datetime:
        return datetime.utcfromtimestamp(self.timestamp / 1000)


@dataclass
class Order:
    """Order to be placed on exchange."""

    symbol: str
    side: Side
    price: float
    qty: float
    order_type: OrderType = OrderType.LIMIT
    trade_side: TradeSide = TradeSide.OPEN
    position_id: str = ""
    client_order_id: str = ""

    # Response fields (filled after placement)
    order_id: str = ""
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: float = 0.0
    filled_price: float = 0.0
    fee: float = 0.0
    created_at: float = 0.0

    def to_dict(self) -> dict:
        """Serialize for logging/storage."""
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "qty": self.qty,
            "price": self.price,
            "order_type": self.order_type.value,
            "trade_side": self.trade_side.value,
        }


@dataclass
class TPSLOrder:
    """Take-profit / stop-loss order."""

    symbol: str
    position_id: str

    # Take profit
    tp_price: float = 0.0
    tp_qty: float = 0.0
    tp_stop_type: str = "MARK_PRICE"
    tp_order_type: str = "LIMIT"
    tp_order_price: float = 0.0

    # Stop loss
    sl_price: float = 0.0
    sl_qty: float = 0.0
    sl_stop_type: str = "MARK_PRICE"
    sl_order_type: str = "LIMIT"
    sl_order_price: float = 0.0

    def to_dict(self) -> dict:
        """Serialize for logging/storage."""
        return {
            "symbol": self.symbol,
            "position_id": self.position_id,
            "tp_price": self.tp_price,
            "sl_price": self.sl_price,
        }


@dataclass
class Position:
    """Open position on exchange."""

    position_id: str
    symbol: str
    direction: Direction
    qty: float
    entry_price: float
    mark_price: float = 0.0
    liquidation_price: float = 0.0
    leverage: int = 1
    margin: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    margin_mode: MarginMode = MarginMode.ISOLATED
    created_at: float = 0.0

    @property
    def notional_value(self) -> float:
        return self.qty * self.mark_price if self.mark_price else self.qty * self.entry_price

    @property
    def pnl_pct(self) -> float:
        if self.margin == 0:
            return 0.0
        return (self.unrealized_pnl / self.margin) * 100


@dataclass
class TradeResult:
    """Completed trade record for journal."""

    trade_id: str
    symbol: str
    side: str                    # "LONG" or "SHORT"
    entry_price: float
    exit_price: float
    qty: float
    leverage: int
    pnl_usd: float
    pnl_pct: float
    fees: float
    entry_time: float
    exit_time: float
    hold_duration_hours: float = 0.0

    # Context
    entry_signal: str = ""
    exit_reason: str = ""
    exit_reason_detail: str = ""
    macro_regime: str = ""
    macro_score: int = 0
    confluence_score: float = 0.0

    # Risk
    risk_pct: float = 0.0
    rr_ratio: float = 0.0

    # Execution quality (discipline layer)
    conviction_score: float = 0.0
    conviction_tier: str = ""
    signal_timestamp: float = 0.0     # When signal was generated
    fill_timestamp: float = 0.0       # When order was filled
    expected_rr: float = 0.0          # Target R:R at entry
    actual_rr: float = 0.0            # Realized R:R at exit
    slippage_pct: float = 0.0         # (fill_price - signal_price) / signal_price
    stop_loss_price: float = 0.0      # SL at entry for attribution

    # Partial close tracking
    partial_closes: list = field(default_factory=list)
    is_partial: bool = False

    @property
    def is_win(self) -> bool:
        return self.pnl_usd > 0


@dataclass
class AccountBalance:
    """Exchange account state."""

    total_balance: float = 0.0
    available_balance: float = 0.0
    margin_used: float = 0.0
    unrealized_pnl: float = 0.0
    timestamp: float = 0.0
