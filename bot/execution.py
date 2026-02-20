from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

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

# Dynamic sizing: V3 Kelly-backed — $25-$50 per trade, consensus=7 + conf=0.55 filter
TRADE_MIN_USD = 25.0
TRADE_MAX_USD = 50.0

# Stop-Loss: sell positions when token price drops below this fraction of entry
STOP_LOSS_THRESHOLD = 0.50  # Sell if current price < 50% of entry price
STOP_LOSS_MIN_AGE_S = 60    # Don't stop-loss in first 60 seconds (let order settle)


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
            order_id = f"dry-run-{market_id[:8]}-{int(time.time())}"
            log.info("[DRY RUN] Simulated order: %s", order_id)
            pos = Position(
                market_id=market_id,
                token_id=signal.token_id,
                direction=signal.direction,
                size_usd=order_size_usd,
                entry_price=price,
                order_id=order_id,
                timeframe=getattr(signal, "timeframe", ""),
                asset=getattr(signal, "asset", ""),
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
                asset=getattr(signal, "asset", ""),
            )
            self.tracker.add(pos)
            return order_id

        except Exception:
            log.exception("Failed to place order")
            return None

    def check_fills(self) -> None:
        """Poll order status and remove filled/expired positions."""
        if self.cfg.dry_run:
            # Expire dry-run positions based on market timeframe
            TF_EXPIRE_S = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "weekly": 604800}
            now = time.time()
            for pos in list(self.tracker.open_positions):
                # Parse timeframe from order_id or default to 15m (900s)
                expire_s = TF_EXPIRE_S.get(getattr(pos, "timeframe", ""), 900)
                age = now - pos.opened_at
                if age > expire_s:
                    log.info("[DRY RUN] Position expired after %.0fs (tf limit %ds): %s",
                             age, expire_s, pos.order_id)
                    self.tracker.remove(pos.order_id)
            return

        if not self.client:
            return

        for pos in list(self.tracker.open_positions):
            try:
                order = self.client.get_order(pos.order_id)
                status = order.get("status", "").lower()
                if status in ("canceled", "expired"):
                    # Order never filled — remove from tracker (no position held)
                    log.info("Order %s %s — removing from tracker (no fill)", pos.order_id, status)
                    self.tracker.remove(pos.order_id)
                elif status in ("matched", "filled"):
                    # Order filled — position is ACTIVE. Keep in tracker for risk management.
                    log.info("Order %s FILLED — position active, keeping in tracker ($%.2f)",
                             pos.order_id, pos.size_usd)
            except Exception:
                log.debug("Could not check order %s", pos.order_id)

    def check_stop_losses(self) -> int:
        """Check filled positions and sell any where token price collapsed.

        Queries the CLOB API for current market prices. If a position's token
        price has dropped below STOP_LOSS_THRESHOLD of entry, sell to recover
        partial stake instead of losing 100%.

        Returns number of positions stopped out.
        """
        if self.cfg.dry_run:
            # In dry-run, simulate stop-loss by checking market mid-price
            return self._check_stop_losses_dry_run()

        if not self.client:
            return 0

        from bot.http_session import get_session

        stopped = 0
        now = time.time()

        for pos in list(self.tracker.open_positions):
            # Skip very new positions (let them settle)
            if now - pos.opened_at < STOP_LOSS_MIN_AGE_S:
                continue

            # Skip straddle positions (different exit logic)
            if pos.strategy == "straddle":
                continue

            try:
                # Fetch current market price for this token
                resp = get_session().get(
                    f"{self.cfg.clob_host}/book?token_id={pos.token_id}",
                    timeout=5,
                )
                if resp.status_code != 200:
                    continue

                book = resp.json()
                # Best bid is what we'd sell at
                bids = book.get("bids", [])

                # NO BIDS = danger signal. Track consecutive no-bid ticks.
                # If empty book persists for 3+ checks (~15s), treat as collapsing.
                if not bids:
                    no_bid_key = f"_no_bid_{pos.order_id}"
                    prev = getattr(self, no_bid_key, 0)
                    setattr(self, no_bid_key, prev + 1)
                    if prev + 1 >= 3:
                        # 3 consecutive no-bid checks — liquidity gone, sell at any price
                        log.warning(
                            "STOP-LOSS: %s no bids for %d checks — emergency sell at $0.01",
                            pos.order_id[:16], prev + 1,
                        )
                        try:
                            shares = pos.shares if pos.shares > 0 else pos.size_usd / pos.entry_price
                            sell_args = OrderArgs(
                                price=0.01, size=shares, side=SELL, token_id=pos.token_id,
                            )
                            signed_order = self.client.create_order(sell_args)
                            sell_resp = self.client.post_order(signed_order, OrderType.GTC)
                            sell_id = sell_resp.get("orderID") or sell_resp.get("id", "unknown")
                            log.info("STOP-LOSS EMERGENCY SOLD: %s | shares=%.1f @ $0.01", sell_id, shares)
                            self.tracker.remove(pos.order_id)
                            stopped += 1
                            self._last_stop_loss = {
                                "direction": pos.direction, "entry_price": pos.entry_price,
                                "bid": 0.01, "recovery": 0.01 * shares,
                                "size_usd": pos.size_usd, "loss_saved": 0.0,
                            }
                        except Exception:
                            log.exception("Failed emergency sell for %s", pos.order_id)
                    else:
                        log.info("STOP-LOSS: %s no bids (%d/3 checks)", pos.order_id[:16], prev + 1)
                    continue
                else:
                    # Reset no-bid counter when bids exist
                    no_bid_key = f"_no_bid_{pos.order_id}"
                    if hasattr(self, no_bid_key):
                        delattr(self, no_bid_key)

                best_bid = float(bids[0].get("price", 0))
                if best_bid <= 0:
                    continue

                # Token essentially worthless (market resolved or near-zero)
                if best_bid < 0.02:
                    if best_bid < 0.005:
                        log.info("STOP-LOSS: %s token worthless (bid=$%.3f), removing from tracker",
                                 pos.order_id[:16], best_bid)
                        self.tracker.remove(pos.order_id)
                        stopped += 1
                    continue

                # Check if price has collapsed below threshold
                stop_price = pos.entry_price * STOP_LOSS_THRESHOLD
                if best_bid < stop_price:
                    log.warning(
                        "STOP-LOSS triggered: %s | entry=$%.3f → bid=$%.3f (%.0f%% of entry, threshold=%.0f%%)",
                        pos.order_id[:16], pos.entry_price, best_bid,
                        (best_bid / pos.entry_price) * 100, STOP_LOSS_THRESHOLD * 100,
                    )

                    # Sell the position
                    try:
                        shares = pos.shares if pos.shares > 0 else pos.size_usd / pos.entry_price
                        sell_price = max(0.01, min(0.99, round(best_bid, 2)))
                        sell_args = OrderArgs(
                            price=sell_price,
                            size=shares,
                            side=SELL,
                            token_id=pos.token_id,
                        )
                        signed_order = self.client.create_order(sell_args)
                        sell_resp = self.client.post_order(signed_order, OrderType.GTC)
                        sell_id = sell_resp.get("orderID") or sell_resp.get("id", "unknown")
                        recovery = best_bid * shares
                        loss_saved = pos.size_usd - recovery

                        log.info(
                            "STOP-LOSS SOLD: %s | recovered $%.2f of $%.2f (saved $%.2f vs full loss)",
                            sell_id, recovery, pos.size_usd, loss_saved,
                        )
                        self.tracker.remove(pos.order_id)
                        stopped += 1

                        # Store stop-loss event for Telegram alert from main loop
                        self._last_stop_loss = {
                            "direction": pos.direction,
                            "entry_price": pos.entry_price,
                            "bid": best_bid,
                            "recovery": recovery,
                            "size_usd": pos.size_usd,
                            "loss_saved": loss_saved,
                        }

                    except Exception:
                        log.exception("Failed to execute stop-loss sell for %s", pos.order_id)

            except Exception:
                log.debug("Stop-loss check failed for %s", pos.order_id)

        if stopped:
            log.info("Stop-loss: %d position(s) exited early", stopped)
        return stopped

    def _check_stop_losses_dry_run(self) -> int:
        """Dry-run stop-loss: check mid-price and simulate exit."""
        from bot.http_session import get_session

        stopped = 0
        now = time.time()

        for pos in list(self.tracker.open_positions):
            if now - pos.opened_at < STOP_LOSS_MIN_AGE_S:
                continue
            if pos.strategy == "straddle":
                continue

            try:
                resp = get_session().get(
                    f"{self.cfg.clob_host}/book?token_id={pos.token_id}",
                    timeout=5,
                )
                if resp.status_code != 200:
                    continue

                book = resp.json()
                bids = book.get("bids", [])
                if not bids:
                    continue

                best_bid = float(bids[0].get("price", 0))
                if best_bid <= 0:
                    continue

                stop_price = pos.entry_price * STOP_LOSS_THRESHOLD
                if best_bid < stop_price:
                    shares = pos.size_usd / pos.entry_price
                    recovery = best_bid * shares
                    log.warning(
                        "[DRY RUN] STOP-LOSS: %s | entry=$%.3f → bid=$%.3f | "
                        "would recover $%.2f of $%.2f",
                        pos.order_id[:16], pos.entry_price, best_bid,
                        recovery, pos.size_usd,
                    )
                    self.tracker.remove(pos.order_id)
                    stopped += 1
            except Exception:
                pass

        return stopped

    def cancel_all_open(self) -> None:
        """Cancel unfilled open orders for shutdown cleanup.

        Only cancels orders that haven't been filled yet.
        Filled positions (active holdings) stay in tracker.
        """
        if self.cfg.dry_run:
            log.info("[DRY RUN] Clearing %d dry-run positions on shutdown", self.tracker.count)
            for pos in list(self.tracker.open_positions):
                self.tracker.remove(pos.order_id)
            return

        if not self.client:
            return

        for pos in list(self.tracker.open_positions):
            try:
                order = self.client.get_order(pos.order_id)
                status = order.get("status", "").lower()
                if status in ("matched", "filled"):
                    log.info("Order %s already filled — keeping in tracker", pos.order_id)
                    continue
                self.client.cancel(pos.order_id)
                log.info("Cancelled open order %s", pos.order_id)
                self.tracker.remove(pos.order_id)
            except Exception:
                log.exception("Failed to cancel order %s", pos.order_id)
