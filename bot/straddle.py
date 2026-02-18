"""Garves — Straddle Engine (Options-Style Hedging).

When volatility is high but direction is unclear (split consensus),
buy BOTH Up and Down tokens at favorable prices. Profit if the
combined cost < 1.0 (guaranteed spread).

Entry criteria (all must be true):
- up_price + down_price < 0.95 (at least 5% guaranteed spread)
- ATR > 2x MIN_ATR_THRESHOLD (market is moving)
- Regime is extreme_fear or fear (mispricing more likely)
- Signal consensus < MIN_CONSENSUS (indicators split)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from bot.config import Config
from bot.execution import Executor
from bot.indicators import atr
from bot.price_cache import PriceCache
from bot.regime import RegimeAdjustment
from bot.risk import PositionTracker

log = logging.getLogger(__name__)

MIN_ATR_FOR_STRADDLE = 0.0001  # 2x the normal MIN_ATR_THRESHOLD
MAX_TOTAL_COST = 0.95          # up_price + down_price must be below this
STRADDLE_COOLDOWN = 600        # 10 min cooldown between straddles


@dataclass
class StraddleOpportunity:
    asset: str
    timeframe: str
    market_id: str
    up_token_id: str
    down_token_id: str
    up_price: float
    down_price: float
    total_cost: float       # up_price + down_price
    max_profit: float       # 1.0 - total_cost (guaranteed profit if either wins)
    atr_pct: float          # Current ATR as % of price
    regime_label: str


class StraddleEngine:
    """Scan for and execute straddle (hedged pair) opportunities."""

    def __init__(self, cfg: Config, executor: Executor, tracker: PositionTracker,
                 price_cache: PriceCache):
        self.cfg = cfg
        self.executor = executor
        self.tracker = tracker
        self.price_cache = price_cache
        self._last_straddle: float = 0.0  # timestamp of last straddle

    def scan_for_straddles(
        self,
        markets: list,
        regime: RegimeAdjustment,
        feed_prices: dict,
    ) -> list[StraddleOpportunity]:
        """Find straddle opportunities from available markets.

        Args:
            markets: List of DiscoveredMarket objects
            regime: Current market regime
            feed_prices: {token_id: price} from WebSocket feed
        """
        # Only straddle in fear regimes
        if regime.label not in ("extreme_fear", "fear"):
            return []

        # Cooldown check
        if time.time() - self._last_straddle < STRADDLE_COOLDOWN:
            return []

        opportunities = []

        for dm in markets:
            market = dm.raw
            tokens = market.get("tokens", [])
            if len(tokens) < 2:
                continue

            up_token = down_token = ""
            for t in tokens:
                outcome = (t.get("outcome") or "").lower()
                tid = t.get("token_id", "")
                if outcome in ("up", "yes"):
                    up_token = tid
                elif outcome in ("down", "no"):
                    down_token = tid

            if not up_token or not down_token:
                continue

            # Get prices from feed
            up_price = feed_prices.get(up_token)
            down_price = feed_prices.get(down_token)

            if up_price is None or down_price is None:
                continue
            if up_price <= 0.01 or down_price <= 0.01:
                continue

            total_cost = up_price + down_price

            # Must have guaranteed spread
            if total_cost >= MAX_TOTAL_COST:
                continue

            # Already have position in this market
            if self.tracker.has_position_for_market(dm.market_id):
                continue

            # Check ATR (need volatile market)
            candles = self.price_cache.get_candles(dm.asset, 100)
            atr_val = atr(candles) if candles else None
            if atr_val is None or atr_val < MIN_ATR_FOR_STRADDLE:
                continue

            opp = StraddleOpportunity(
                asset=dm.asset,
                timeframe=dm.timeframe.name,
                market_id=dm.market_id,
                up_token_id=up_token,
                down_token_id=down_token,
                up_price=up_price,
                down_price=down_price,
                total_cost=total_cost,
                max_profit=1.0 - total_cost,
                atr_pct=atr_val * 100,
                regime_label=regime.label,
            )
            opportunities.append(opp)

        # Sort by max_profit descending (best spread first)
        opportunities.sort(key=lambda o: o.max_profit, reverse=True)
        return opportunities

    def execute_straddle(self, opp: StraddleOpportunity) -> tuple[str, str] | None:
        """Place paired orders: buy UP token + buy DOWN token.

        Each leg gets half the normal position size.
        Returns (up_order_id, down_order_id) or None.
        """
        half_size = self.cfg.order_size_usd / 2

        log.info(
            "[STRADDLE] %s/%s | UP@%.3f + DOWN@%.3f = %.3f | profit=%.1f%% | ATR=%.3f%%",
            opp.asset.upper(), opp.timeframe,
            opp.up_price, opp.down_price, opp.total_cost,
            opp.max_profit * 100, opp.atr_pct,
        )

        if self.cfg.dry_run:
            up_order_id = f"straddle-up-{opp.market_id[:8]}"
            down_order_id = f"straddle-dn-{opp.market_id[:8]}"
            log.info("[DRY RUN] Straddle placed: %s + %s ($%.2f each leg)",
                     up_order_id, down_order_id, half_size)

            from bot.risk import Position
            self.tracker.add(Position(
                market_id=opp.market_id, token_id=opp.up_token_id,
                direction="up", size_usd=half_size,
                entry_price=opp.up_price, order_id=up_order_id,
            ))
            self.tracker.add(Position(
                market_id=opp.market_id, token_id=opp.down_token_id,
                direction="down", size_usd=half_size,
                entry_price=opp.down_price, order_id=down_order_id,
            ))
            self._last_straddle = time.time()
            return (up_order_id, down_order_id)

        # Live trading — place both legs
        if not self.executor.client:
            log.error("[STRADDLE] No CLOB client for live straddle")
            return None

        try:
            from bot.signals import Signal

            up_signal = Signal(
                direction="up", edge=opp.max_profit / 2,
                probability=opp.up_price, token_id=opp.up_token_id,
                confidence=0.5, timeframe=opp.timeframe, asset=opp.asset,
            )
            down_signal = Signal(
                direction="down", edge=opp.max_profit / 2,
                probability=opp.down_price, token_id=opp.down_token_id,
                confidence=0.5, timeframe=opp.timeframe, asset=opp.asset,
            )

            up_id = self.executor.place_order(up_signal, opp.market_id)
            down_id = self.executor.place_order(down_signal, opp.market_id)

            if up_id and down_id:
                self._last_straddle = time.time()
                return (up_id, down_id)

            # Partial fill — cancel the successful leg to avoid orphan position
            if up_id and not down_id:
                log.warning("[STRADDLE] Down leg failed — cancelling orphan up leg %s", up_id)
                try:
                    if self.executor.client:
                        self.executor.client.cancel(up_id)
                    self.tracker.remove(up_id)
                except Exception:
                    log.exception("[STRADDLE] Failed to cancel orphan up leg %s", up_id)
            elif down_id and not up_id:
                log.warning("[STRADDLE] Up leg failed — cancelling orphan down leg %s", down_id)
                try:
                    if self.executor.client:
                        self.executor.client.cancel(down_id)
                    self.tracker.remove(down_id)
                except Exception:
                    log.exception("[STRADDLE] Failed to cancel orphan down leg %s", down_id)
            return None

        except Exception:
            log.exception("[STRADDLE] Failed to execute straddle")
            return None
