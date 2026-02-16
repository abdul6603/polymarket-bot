from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

from bot.config import Config
from bot.regime import RegimeAdjustment
from bot.risk import Position, PositionTracker
from bot.signals import Signal

log = logging.getLogger(__name__)

TRADES_FILE = Path(__file__).parent.parent / "data" / "trades.jsonl"

# Kelly Criterion constants
KELLY_MIN_RESOLVED = 10      # Need at least this many resolved trades
KELLY_FRACTION = 0.25        # Quarter-Kelly for safety
KELLY_MIN_SIZE_FRAC = 0.10   # Never size below 10% of base
KELLY_MAX_SIZE_FRAC = 2.50   # Never size above 250% of base

# Dynamic sizing: Garves determines $10-$20 per trade based on signal quality
TRADE_MIN_USD = 10.0
TRADE_MAX_USD = 20.0


class Executor:
    """Order placement and management via py-clob-client."""

    def __init__(self, cfg: Config, client: ClobClient | None, tracker: PositionTracker):
        self.cfg = cfg
        self.client = client
        self.tracker = tracker
        self.regime: RegimeAdjustment | None = None

    def _dynamic_position_size(self, signal: Signal) -> float:
        """Calculate position size between $10-$20 based on signal quality + Kelly.

        Sizing logic:
        - Base: confidence/edge quality score maps $10 → $20
        - Overlay: Kelly Criterion adjusts further once we have enough trade data
        - Final: always clamped to [TRADE_MIN_USD, TRADE_MAX_USD]
        """
        # ── Confidence + Edge Quality Score (0.0 → 1.0) ──
        # Confidence is 0-1, edge typically 0.03-0.20
        conf_score = min(signal.confidence / 0.6, 1.0)   # 0.6 confidence → max
        edge_score = min(signal.edge / 0.12, 1.0)        # 12% edge → max
        quality = conf_score * 0.5 + edge_score * 0.5     # blend

        # Map quality to $10-$20 range
        size = TRADE_MIN_USD + quality * (TRADE_MAX_USD - TRADE_MIN_USD)

        # ── Kelly Overlay (once we have enough resolved trades) ──
        kelly_mult = 1.0
        if TRADES_FILE.exists():
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

                if len(resolved) >= KELLY_MIN_RESOLVED:
                    wins = sum(1 for r in resolved if r.get("won"))
                    win_rate = wins / len(resolved)

                    payouts = []
                    for r in resolved:
                        prob = r.get("probability", 0.5)
                        if 0.01 < prob < 0.99:
                            payouts.append((1.0 / prob) - 1.0)
                        else:
                            payouts.append(1.0)
                    avg_payout = sum(payouts) / len(payouts) if payouts else 1.0

                    if avg_payout > 0:
                        kelly_full = (win_rate * avg_payout - (1 - win_rate)) / avg_payout
                        kelly_frac = kelly_full * KELLY_FRACTION
                        if kelly_frac > 0:
                            kelly_mult = max(KELLY_MIN_SIZE_FRAC, min(KELLY_MAX_SIZE_FRAC, kelly_frac))
                            log.info(
                                "Kelly overlay: WR=%.1f%% (%d/%d) payout=%.2f kelly=%.3f mult=%.2f",
                                win_rate * 100, wins, len(resolved), avg_payout, kelly_frac, kelly_mult,
                            )
                        else:
                            kelly_mult = 0.8  # negative Kelly → size down
            except Exception:
                log.debug("Kelly calculation failed, using quality-only sizing")

        size *= kelly_mult

        # ── Hard clamp to $10-$20 ──
        size = max(TRADE_MIN_USD, min(TRADE_MAX_USD, size))

        log.info(
            "Dynamic sizing: conf=%.2f edge=%.1f%% quality=%.2f kelly=%.2fx -> $%.2f",
            signal.confidence, signal.edge * 100, quality, kelly_mult, size,
        )
        return size

    def place_order(
        self, signal: Signal, market_id: str, conviction_size: float | None = None
    ) -> str | None:
        """Place a GTC limit order for the given signal.

        Args:
            signal: Signal from the ensemble engine.
            market_id: Polymarket market ID.
            conviction_size: If provided, use this position size (from ConvictionEngine)
                instead of the legacy _dynamic_position_size() calculation.

        Returns:
            Order ID if placed, None otherwise.
        """
        if conviction_size is not None and conviction_size > 0:
            # ConvictionEngine already applied regime, safety rails, and hard caps
            order_size_usd = conviction_size
        else:
            order_size_usd = self._dynamic_position_size(signal)
            # Apply regime size multiplier, then hard clamp to $10-$20 range
            if self.regime:
                order_size_usd *= self.regime.size_multiplier
            order_size_usd = max(TRADE_MIN_USD, min(TRADE_MAX_USD, order_size_usd))
        size = order_size_usd / signal.probability
        price = round(signal.probability, 2)
        # Clamp price to valid range
        price = max(0.01, min(0.99, price))

        log.info(
            "Order: %s %s | size=%.2f tokens @ $%.2f | edge=%.1f%% | market=%s",
            signal.direction.upper(),
            signal.token_id[:16],
            size,
            price,
            signal.edge * 100,
            market_id,
        )

        if self.cfg.dry_run:
            order_id = f"dry-run-{market_id[:8]}"
            log.info("[DRY RUN] Simulated order: %s", order_id)
            pos = Position(
                market_id=market_id,
                token_id=signal.token_id,
                direction=signal.direction,
                size_usd=order_size_usd,
                entry_price=price,
                order_id=order_id,
            )
            self.tracker.add(pos)
            return order_id

        if not self.client:
            log.error("No CLOB client available for live trading")
            return None

        try:
            order_args = OrderArgs(
                price=price,
                size=size,
                side=BUY,
                token_id=signal.token_id,
            )
            signed_order = self.client.create_order(order_args)
            resp = self.client.post_order(signed_order, OrderType.GTC)

            order_id = resp.get("orderID") or resp.get("id", "unknown")
            log.info("Order placed: %s", order_id)

            pos = Position(
                market_id=market_id,
                token_id=signal.token_id,
                direction=signal.direction,
                size_usd=order_size_usd,
                entry_price=price,
                order_id=order_id,
            )
            self.tracker.add(pos)
            return order_id

        except Exception:
            log.exception("Failed to place order")
            return None

    def check_fills(self) -> None:
        """Poll order status and remove filled/expired positions."""
        if self.cfg.dry_run:
            # Expire dry-run positions after 5 minutes (market resolution)
            now = time.time()
            for pos in list(self.tracker.open_positions):
                age = now - pos.opened_at
                if age > 300:  # 5 min
                    log.info("[DRY RUN] Position expired after %.0fs: %s", age, pos.order_id)
                    self.tracker.remove(pos.order_id)
            return

        if not self.client:
            return

        for pos in list(self.tracker.open_positions):
            try:
                order = self.client.get_order(pos.order_id)
                status = order.get("status", "").lower()
                if status in ("matched", "filled", "canceled", "expired"):
                    log.info("Order %s status: %s", pos.order_id, status)
                    self.tracker.remove(pos.order_id)
            except Exception:
                log.debug("Could not check order %s", pos.order_id)

    def cancel_all_open(self) -> None:
        """Cancel all open orders for shutdown cleanup."""
        if self.cfg.dry_run:
            log.info("[DRY RUN] Would cancel %d open orders", self.tracker.count)
            for pos in list(self.tracker.open_positions):
                self.tracker.remove(pos.order_id)
            return

        if not self.client:
            return

        for pos in list(self.tracker.open_positions):
            try:
                self.client.cancel(pos.order_id)
                log.info("Cancelled order %s", pos.order_id)
                self.tracker.remove(pos.order_id)
            except Exception:
                log.exception("Failed to cancel order %s", pos.order_id)
