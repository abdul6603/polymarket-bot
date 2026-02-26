"""Order manager — places, tracks, and manages orders with paper mode.

Handles:
- Market and limit order placement at entry zones
- Scaled entries (2-3 tranches across OB zone)
- Pending order lifecycle (place, track, fill, expire)
- TP/SL placement after fill
- Position scaling (50/30/20 tranches)
- Paper trading simulation
- Trade logging to JSONL
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Optional

from odin.exchange.hyperliquid_client import HyperliquidClient
from odin.exchange.models import TradeResult
from odin.execution.exit_manager import (
    ExitAction, ExitDecision, ExitManager, PositionExitState,
)
from odin.risk.position_sizer import PositionSize
from odin.strategy.signals import TradeSignal

log = logging.getLogger(__name__)


class OrderManager:
    """
    Manages order lifecycle — entry, TP/SL, exit, paper mode.

    In paper mode: simulates fills at signal prices, tracks PnL.
    In live mode: places market orders on Hyperliquid with TP/SL triggers.
    """

    def __init__(
        self,
        client: Optional[HyperliquidClient],
        dry_run: bool = True,
        data_dir: Path = Path("data"),
        exit_manager: Optional[ExitManager] = None,
        paper_fee_rate: float = 0.0004,
    ):
        self._client = client
        self._dry_run = dry_run
        self._paper_fee_rate = paper_fee_rate
        self._trades_file = data_dir / "odin_trades.jsonl"
        self._signals_file = data_dir / "odin_signals.jsonl"

        # Active paper positions
        self._paper_positions: dict[str, dict] = {}

        # Active live positions (tracked locally for restart recovery + close detection)
        self._live_positions: dict[str, dict] = {}
        self._live_positions_file = data_dir / "odin_live_positions.json"
        if not dry_run:
            self._load_live_positions()

        # Pending limit orders (paper + live)
        self._pending_orders: dict[str, dict] = {}

        # Exit management
        self._exit_mgr = exit_manager or ExitManager()
        self._exit_states: dict[str, PositionExitState] = {}

    def execute_signal(
        self,
        signal: TradeSignal,
        size: PositionSize,
        balance: float = 0.0,
    ) -> Optional[str]:
        """Execute a trade signal. Returns position/order ID or None."""
        if not signal.tradeable:
            log.info("[EXEC] Signal not tradeable: conf=%.2f rr=%.1f",
                     signal.confidence, signal.risk_reward)
            return None

        # Min notional guard ($10 for Hyperliquid)
        if size.notional_usd < 10:
            log.info("[EXEC] Position too small: $%.2f (min $10)", size.notional_usd)
            return None

        # Balance guard for live trading
        min_balance = float(os.environ.get("ODIN_MIN_BALANCE", "50"))
        if not self._dry_run and balance > 0 and balance < min_balance:
            log.warning("[EXEC] Balance $%.2f below minimum $%.0f — rejecting trade",
                        balance, min_balance)
            return None

        # Log signal
        self._log_signal(signal, size)

        if self._dry_run:
            return self._paper_entry(signal, size)
        return self._live_entry(signal, size)

    # ── Paper Trading ──

    def _paper_entry(self, signal: TradeSignal, size: PositionSize) -> str:
        """Simulate a trade entry in paper mode."""
        pos_id = f"paper_{uuid.uuid4().hex[:8]}"

        entry_price = signal.entry_price or (
            (signal.entry_zone_top + signal.entry_zone_bottom) / 2
        )

        self._paper_positions[pos_id] = {
            "id": pos_id,
            "symbol": signal.symbol,
            "direction": signal.direction,
            "entry_price": entry_price,
            "qty": size.qty,
            "notional": size.notional_usd,
            "margin": size.margin_usd,
            "risk_usd": size.risk_usd,
            "leverage": size.leverage,
            "stop_loss": signal.stop_loss,
            "take_profit_1": signal.take_profit_1,
            "take_profit_2": signal.take_profit_2,
            "entry_time": time.time(),
            "confidence": signal.confidence,
            "conviction_score": getattr(signal, "conviction_score", 0),
            "conviction_tier": getattr(signal, "conviction_tier", ""),
            "macro_regime": signal.macro_regime,
            "macro_score": signal.macro_score,
            "entry_reason": signal.entry_reason,
            "smc_patterns": signal.smc_patterns,
            "atr": getattr(signal, "atr", 0),
            "signal_timestamp": time.time(),
        }

        # Initialize exit tracking state
        self._exit_states[pos_id] = self._exit_mgr.init_exit_state(
            self._paper_positions[pos_id]
        )

        log.info(
            "[PAPER] %s %s @ $%.2f | qty=%.6f notional=$%.2f "
            "SL=$%.2f TP=$%.2f (R:R %.1f)",
            signal.direction, signal.symbol, entry_price,
            size.qty, size.notional_usd,
            signal.stop_loss, signal.take_profit_1, signal.risk_reward,
        )
        return pos_id

    def check_paper_positions(
        self, current_prices: dict[str, float], regime: str = "neutral",
    ) -> list[TradeResult]:
        """Check paper positions using ExitManager for trailing SL, partial TP, time exits."""
        closed = []

        for pos_id, pos in list(self._paper_positions.items()):
            symbol = pos["symbol"]
            price = current_prices.get(symbol, 0)
            if price <= 0:
                continue

            # Get or create exit state
            state = self._exit_states.get(pos_id)
            if not state:
                state = self._exit_mgr.init_exit_state(pos)
                self._exit_states[pos_id] = state

            # Evaluate all exit conditions
            decisions = self._exit_mgr.update(pos, state, price, regime)

            for decision in decisions:
                if decision.action == ExitAction.STOP_LOSS:
                    exit_price = decision.close_price or price
                    result = self._close_paper_position(
                        pos, exit_price, "SL", decision.reason,
                        state.partial_closes,
                    )
                    closed.append(result)
                    del self._paper_positions[pos_id]
                    self._exit_states.pop(pos_id, None)
                    break

                elif decision.action == ExitAction.TIME_EXIT:
                    result = self._close_paper_position(
                        pos, price, "TIME", decision.reason,
                        state.partial_closes,
                    )
                    closed.append(result)
                    del self._paper_positions[pos_id]
                    self._exit_states.pop(pos_id, None)
                    break

                elif decision.action in (
                    ExitAction.PARTIAL_EARLY, ExitAction.PARTIAL_TP1, ExitAction.PARTIAL_TP2, ExitAction.PARTIAL_TP3,
                ):
                    if decision.close_pct >= 1.0:
                        # Full close (TP3 runner or all remaining)
                        result = self._close_paper_position(
                            pos, price, decision.action.value, decision.reason,
                            state.partial_closes,
                        )
                        closed.append(result)
                        del self._paper_positions[pos_id]
                        self._exit_states.pop(pos_id, None)
                        break
                    else:
                        self._partial_close_paper(pos_id, pos, state, price, decision)

                elif decision.action == ExitAction.TRAIL_SL:
                    pos["stop_loss"] = decision.new_sl
                    log.info("[EXIT-MGR] %s %s: %s",
                             pos["symbol"], pos_id, decision.reason)

        return closed

    def _partial_close_paper(
        self,
        pos_id: str,
        pos: dict,
        state: PositionExitState,
        price: float,
        decision: ExitDecision,
    ) -> None:
        """Execute a partial close on a paper position."""
        close_qty = state.remaining_qty * decision.close_pct
        if close_qty <= 0:
            return

        entry = pos["entry_price"]
        if pos["direction"] == "LONG":
            partial_pnl = (price - entry) * close_qty
        else:
            partial_pnl = (entry - price) * close_qty

        fees = (price * close_qty) * self._paper_fee_rate
        partial_pnl -= fees

        # Update remaining qty
        state.remaining_qty -= close_qty
        pos["qty"] = state.remaining_qty
        pos["notional"] = state.remaining_qty * entry

        # Record partial close
        partial_record = {
            "action": decision.action.value,
            "qty": round(close_qty, 8),
            "price": round(price, 2),
            "pnl_usd": round(partial_pnl, 2),
            "time": time.time(),
            "reason": decision.reason,
        }
        state.partial_closes.append(partial_record)

        log.info(
            "[EXIT-MGR] PARTIAL %s %s: close %.6f @ $%.2f | PnL $%.2f | %s",
            pos["symbol"], pos_id, close_qty, price, partial_pnl, decision.reason,
        )

    def _close_paper_position(
        self,
        pos: dict,
        exit_price: float,
        reason: str,
        detail: str = "",
        partial_closes: list | None = None,
    ) -> TradeResult:
        """Close a paper position and generate trade result."""
        entry = pos["entry_price"]
        qty = pos["qty"]

        if pos["direction"] == "LONG":
            pnl = (exit_price - entry) * qty
            pnl_pct = (exit_price / entry - 1) * 100
        else:
            pnl = (entry - exit_price) * qty
            pnl_pct = (entry / exit_price - 1) * 100

        # Realistic fees: taker + slippage (configurable via paper_fee_rate)
        fees = pos["notional"] * self._paper_fee_rate

        hold_hours = (time.time() - pos["entry_time"]) / 3600

        # Calculate actual risk and R:R from position data
        sl = pos.get("stop_loss", 0)
        risk_dist = abs(entry - sl) if sl > 0 else 0
        risk_pct_val = (risk_dist / entry * 100) if entry > 0 and risk_dist > 0 else 0
        rr_val = abs(exit_price - entry) / risk_dist if risk_dist > 0 else 0

        # Expected R:R from TP1
        tp1 = pos.get("take_profit_1", 0)
        expected_rr = abs(tp1 - entry) / risk_dist if risk_dist > 0 and tp1 > 0 else 0

        result = TradeResult(
            trade_id=pos["id"],
            symbol=pos["symbol"],
            side=pos["direction"],
            entry_price=entry,
            exit_price=exit_price,
            qty=qty,
            pnl_usd=round(pnl - fees, 2),
            pnl_pct=round(pnl_pct, 3),
            fees=round(fees, 4),
            leverage=pos["leverage"],
            entry_time=pos["entry_time"],
            exit_time=time.time(),
            hold_duration_hours=round(hold_hours, 2),
            exit_reason=reason,
            exit_reason_detail=detail,
            entry_signal=pos.get("entry_reason", ""),
            confluence_score=pos.get("confidence", 0),
            macro_regime=pos.get("macro_regime", ""),
            macro_score=pos.get("macro_score", 0),
            risk_pct=round(risk_pct_val, 3),
            rr_ratio=round(rr_val, 2),
            conviction_score=pos.get("conviction_score", 0),
            conviction_tier=pos.get("conviction_tier", ""),
            signal_timestamp=pos.get("signal_timestamp", pos["entry_time"]),
            fill_timestamp=pos["entry_time"],
            expected_rr=round(expected_rr, 2),
            actual_rr=round(rr_val, 2),
            stop_loss_price=sl,
            partial_closes=partial_closes or [],
            is_partial=bool(partial_closes),
        )

        self._log_trade(result)

        log.info(
            "[PAPER] CLOSED %s %s @ $%.2f → $%.2f | PnL: $%.2f (%.2f%%) "
            "| Reason: %s | Hold: %.1fh%s",
            result.side, result.symbol, entry, exit_price,
            result.pnl_usd, result.pnl_pct, reason, hold_hours,
            f" | Partials: {len(partial_closes)}" if partial_closes else "",
        )
        return result

    # ── Live Trading ──

    def _live_entry(self, signal: TradeSignal, size: PositionSize) -> Optional[str]:
        """Place a live market order on Hyperliquid."""
        if not self._client:
            log.error("[EXEC] No exchange client — cannot place live order")
            return None

        is_buy = signal.direction == "LONG"

        try:
            # Set leverage first
            self._client.set_leverage(
                signal.symbol, size.leverage, is_cross=True,
            )

            # Market order
            result = self._client.place_market_order(
                signal.symbol, is_buy, size.qty, slippage=0.01,
            )

            if result.get("status") == "ok":
                fill_price = signal.entry_price
                fill_qty = size.qty
                statuses = result.get("response", {}).get("data", {}).get("statuses", [])
                for s in statuses:
                    if "filled" in s:
                        filled = s["filled"]
                        fill_qty = float(filled["totalSz"])
                        fill_price = float(filled["avgPx"])
                        log.info("[LIVE] Filled: %s %.6f @ $%.2f",
                                 signal.symbol, fill_qty, fill_price)

                # Place TP/SL as trigger orders
                self._client.place_tpsl(
                    symbol=signal.symbol,
                    qty=fill_qty,
                    direction=signal.direction,
                    tp_price=signal.take_profit_1,
                    sl_price=signal.stop_loss,
                )

                pos_id = f"hl_{signal.symbol}_{int(time.time())}"

                # Track locally for restart recovery + close detection
                self._live_positions[pos_id] = {
                    "id": pos_id,
                    "symbol": signal.symbol,
                    "direction": signal.direction,
                    "entry_price": fill_price,
                    "qty": fill_qty,
                    "notional": fill_qty * fill_price,
                    "leverage": size.leverage,
                    "stop_loss": signal.stop_loss,
                    "take_profit_1": signal.take_profit_1,
                    "take_profit_2": signal.take_profit_2,
                    "entry_time": time.time(),
                    "entry_reason": signal.entry_reason,
                    "conviction_score": getattr(signal, "conviction_score", 0),
                    "macro_regime": signal.macro_regime,
                }
                self._save_live_positions()

                log.info("[LIVE] Position opened: %s", pos_id)
                return pos_id
            else:
                log.error("[LIVE] Order rejected: %s", result)
                return None

        except Exception as e:
            log.error("[LIVE] Order failed: %s", str(e)[:200])
            return None

    # ── Live Position Persistence ──

    def _save_live_positions(self) -> None:
        """Atomically persist live positions to disk for restart recovery."""
        try:
            tmp = self._live_positions_file.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(self._live_positions, f, indent=2, default=str)
            tmp.replace(self._live_positions_file)
        except Exception as e:
            log.error("[LIVE] Save error: %s", str(e)[:100])

    def _load_live_positions(self) -> None:
        """Load live positions from disk on startup."""
        if self._live_positions_file.exists():
            try:
                with open(self._live_positions_file) as f:
                    self._live_positions = json.load(f)
                log.info("[LIVE] Loaded %d tracked positions from disk",
                         len(self._live_positions))
            except Exception as e:
                log.error("[LIVE] Load error: %s", str(e)[:100])
                self._live_positions = {}

    def check_live_positions(self) -> list[TradeResult]:
        """Cross-reference local tracking with exchange positions.

        Detects positions that closed (TP/SL triggered on exchange).
        Returns list of TradeResults for closed positions.
        """
        if not self._client or self._dry_run:
            return []

        closed_results: list[TradeResult] = []

        try:
            exchange_positions = self._client.get_positions()
            exchange_symbols = {p.symbol for p in exchange_positions}
        except Exception as e:
            log.warning("[LIVE-CHECK] Failed to fetch exchange positions: %s",
                        str(e)[:150])
            return []

        for pos_id, pos in list(self._live_positions.items()):
            symbol = pos["symbol"]

            if symbol in exchange_symbols:
                # Position still open — check for stale (>24h)
                age_hours = (time.time() - pos.get("entry_time", 0)) / 3600
                if age_hours > 24:
                    log.warning("[LIVE-CHECK] Stale position %s: open %.1fh (>24h). "
                                "TP/SL may not have triggered.", symbol, age_hours)
                continue

            # Position was in our tracking but NOT on exchange → closed
            log.info("[LIVE-CHECK] Position %s (%s) no longer on exchange — "
                     "inferring close", pos_id, symbol)

            # Get current price for P&L estimation
            try:
                exit_price = self._client.get_price(symbol)
            except Exception:
                exit_price = pos.get("entry_price", 0)

            entry_price = pos.get("entry_price", 0)
            qty = pos.get("qty", 0)
            direction = pos.get("direction", "LONG")

            # Infer P&L (actual exit price unknown — TP/SL trigger price is best guess)
            # Check if it hit TP or SL based on current price vs levels
            sl = pos.get("stop_loss", 0)
            tp = pos.get("take_profit_1", 0)

            if direction == "LONG":
                pnl_raw = (exit_price - entry_price) * qty
                # Guess exit reason
                if tp > 0 and exit_price >= tp * 0.995:
                    exit_reason = "TP"
                    exit_price = tp
                elif sl > 0 and exit_price <= sl * 1.005:
                    exit_reason = "SL"
                    exit_price = sl
                else:
                    exit_reason = "EXCHANGE_CLOSE"
            else:
                pnl_raw = (entry_price - exit_price) * qty
                if tp > 0 and exit_price <= tp * 1.005:
                    exit_reason = "TP"
                    exit_price = tp
                elif sl > 0 and exit_price >= sl * 0.995:
                    exit_reason = "SL"
                    exit_price = sl
                else:
                    exit_reason = "EXCHANGE_CLOSE"

            # Recalculate P&L with inferred exit price
            if direction == "LONG":
                pnl_raw = (exit_price - entry_price) * qty
                pnl_pct = (exit_price / entry_price - 1) * 100
            else:
                pnl_raw = (entry_price - exit_price) * qty
                pnl_pct = (entry_price / exit_price - 1) * 100

            # Estimate fees (taker close)
            fees = (exit_price * qty) * 0.00035
            hold_hours = (time.time() - pos.get("entry_time", time.time())) / 3600

            result = TradeResult(
                trade_id=pos_id,
                symbol=symbol,
                side=direction,
                entry_price=entry_price,
                exit_price=exit_price,
                qty=qty,
                pnl_usd=round(pnl_raw - fees, 2),
                pnl_pct=round(pnl_pct, 3),
                fees=round(fees, 4),
                leverage=pos.get("leverage", 1),
                entry_time=pos.get("entry_time", 0),
                exit_time=time.time(),
                hold_duration_hours=round(hold_hours, 2),
                exit_reason=exit_reason,
                exit_reason_detail=f"Position closed on exchange ({exit_reason})",
                entry_signal=pos.get("entry_reason", ""),
                confluence_score=pos.get("conviction_score", 0),
                macro_regime=pos.get("macro_regime", ""),
            )

            self._log_trade(result)
            closed_results.append(result)

            # Remove from tracking
            del self._live_positions[pos_id]
            log.info("[LIVE-CLOSED] %s %s | entry=$%.2f exit=$%.2f | PnL=$%.2f (%s) | Hold: %.1fh",
                     direction, symbol, entry_price, exit_price,
                     result.pnl_usd, exit_reason, hold_hours)

        if closed_results:
            self._save_live_positions()

        return closed_results

    def get_live_positions(self) -> list[dict]:
        """Get tracked live positions for dashboard."""
        return list(self._live_positions.values())

    def place_tp_sl(
        self, symbol: str, position_id: str, signal: TradeSignal
    ) -> None:
        """Place TP/SL triggers on an existing position."""
        if self._dry_run or not self._client:
            return

        try:
            # Get current position size
            positions = self._client.get_positions(symbol)
            if not positions:
                log.warning("[EXEC] No position found for %s — skipping TP/SL", symbol)
                return
            qty = positions[0].qty

            self._client.place_tpsl(
                symbol=symbol,
                qty=qty,
                direction=signal.direction,
                tp_price=signal.take_profit_1,
                sl_price=signal.stop_loss,
            )
        except Exception as e:
            log.error("[EXEC] TP/SL placement failed: %s", str(e)[:200])

    # ── Status ──

    def get_open_positions_count(self) -> int:
        if self._dry_run:
            return len(self._paper_positions)
        # Use local tracking (fast) — exchange sync happens in check_live_positions
        return len(self._live_positions)

    def has_position_for_symbol(self, symbol: str) -> bool:
        """Check if there's already an open position for this symbol."""
        if self._dry_run:
            return any(p["symbol"] == symbol for p in self._paper_positions.values())
        return any(p["symbol"] == symbol for p in self._live_positions.values())

    def get_total_exposure(self) -> float:
        if self._dry_run:
            return sum(p["notional"] for p in self._paper_positions.values())
        return sum(p.get("notional", 0) for p in self._live_positions.values())

    def get_paper_positions(self, current_prices: dict[str, float] | None = None) -> list[dict]:
        """Return paper positions enriched with current price + P&L for dashboard."""
        positions = []
        for p in self._paper_positions.values():
            pos = dict(p)  # shallow copy
            if current_prices:
                symbol = pos.get("symbol", "")
                price = current_prices.get(symbol, 0)
                if price > 0:
                    pos["current_price"] = price
                    pos["mark_price"] = price
                    direction = pos.get("direction", "LONG")
                    entry = pos.get("entry_price", 0)
                    qty = pos.get("qty", 0)
                    if direction == "LONG":
                        pos["pnl_usd"] = round((price - entry) * qty, 2)
                    else:
                        pos["pnl_usd"] = round((entry - price) * qty, 2)
            positions.append(pos)
        return positions

    def set_position_meta(self, position_id: str, key: str, value) -> None:
        """Store metadata on a position (paper or live)."""
        if position_id in self._paper_positions:
            self._paper_positions[position_id].setdefault("_meta", {})[key] = value
        elif position_id in self._live_positions:
            self._live_positions[position_id].setdefault("_meta", {})[key] = value
            self._save_live_positions()

    def get_position_meta(self, position_id: str, key: str, default=None):
        """Retrieve metadata from a position (paper or live)."""
        pos = self._paper_positions.get(position_id) or self._live_positions.get(position_id, {})
        return pos.get("_meta", {}).get(key, default)

    # ── Limit / Scaled Entries (Phase 3) ──

    def execute_limit_entry(
        self,
        signal: TradeSignal,
        size: PositionSize,
        limit_price: float,
        ttl_seconds: int = 7200,
    ) -> Optional[str]:
        """Place a single limit order at a specific price.

        Returns order ID or None. In paper mode, creates a pending order
        that converts to a position when price touches the limit price.
        """
        if size.notional_usd < 5:
            return None

        self._log_signal(signal, size)

        if self._dry_run:
            return self._paper_limit_entry(signal, size, limit_price, ttl_seconds)
        return self._live_limit_entry(signal, size, limit_price, ttl_seconds)

    def execute_scaled_entry(
        self,
        signal: TradeSignal,
        size: PositionSize,
        zone_top: float,
        zone_bottom: float,
        tranches: int = 3,
        ttl_seconds: int = 7200,
    ) -> list[str]:
        """Split entry across multiple price levels within an OB zone.

        Distributes size across N tranches with heavier weighting near zone edge.
        Returns list of order IDs.
        """
        if size.notional_usd < 5 or tranches < 1:
            return []

        self._log_signal(signal, size)

        # Weight distribution: heavier at the zone edge (better fill price)
        # For LONG: buy closer to zone_bottom (lower = better)
        # For SHORT: sell closer to zone_top (higher = better)
        is_long = signal.direction == "LONG"

        # Calculate tranche prices evenly across zone
        if tranches == 1:
            prices = [(zone_top + zone_bottom) / 2]
            weights = [1.0]
        else:
            prices = []
            step = (zone_top - zone_bottom) / (tranches - 1) if tranches > 1 else 0
            for i in range(tranches):
                prices.append(round(zone_bottom + step * i, 2))
            # Weights: heavier toward the favorable end
            # LONG → heavier at lower prices; SHORT → heavier at higher prices
            raw_weights = list(range(1, tranches + 1))
            if is_long:
                raw_weights.reverse()  # More weight at bottom (lower prices)
            total_w = sum(raw_weights)
            weights = [w / total_w for w in raw_weights]

        order_ids = []
        for i, (price, weight) in enumerate(zip(prices, weights)):
            tranche_qty = round(size.qty * weight, 8)
            tranche_notional = tranche_qty * price
            if tranche_notional < 3:
                continue

            tranche_size = PositionSize(
                qty=tranche_qty,
                notional_usd=round(tranche_notional, 2),
                margin_usd=round(tranche_notional / size.leverage, 2),
                leverage=size.leverage,
                risk_usd=round(size.risk_usd * weight, 2),
                sl_price=size.sl_price,
                sl_source=size.sl_source,
                sl_distance_pct=size.sl_distance_pct,
            )

            oid = self.execute_limit_entry(
                signal, tranche_size, price, ttl_seconds,
            )
            if oid:
                order_ids.append(oid)
                log.info("[SCALED] Tranche %d/%d: %s @ $%.2f qty=%.6f (%.0f%%)",
                         i + 1, tranches, signal.direction, price,
                         tranche_qty, weight * 100)

        return order_ids

    def _paper_limit_entry(
        self,
        signal: TradeSignal,
        size: PositionSize,
        limit_price: float,
        ttl_seconds: int,
    ) -> str:
        """Create a pending paper limit order."""
        order_id = f"plimit_{uuid.uuid4().hex[:8]}"
        now = time.time()

        self._pending_orders[order_id] = {
            "id": order_id,
            "symbol": signal.symbol,
            "direction": signal.direction,
            "limit_price": limit_price,
            "qty": size.qty,
            "notional": size.notional_usd,
            "margin": size.margin_usd,
            "leverage": size.leverage,
            "stop_loss": signal.stop_loss,
            "take_profit_1": signal.take_profit_1,
            "take_profit_2": signal.take_profit_2,
            "placed_time": now,
            "expires_at": now + ttl_seconds,
            "confidence": signal.confidence,
            "macro_regime": signal.macro_regime,
            "macro_score": signal.macro_score,
            "entry_reason": signal.entry_reason,
            "smc_patterns": signal.smc_patterns,
            "atr": getattr(signal, "atr", 0),
            "status": "pending",
            "mode": "paper",
        }

        log.info(
            "[PAPER-LIMIT] %s %s @ $%.2f | qty=%.6f notional=$%.2f | TTL=%ds",
            signal.direction, signal.symbol, limit_price,
            size.qty, size.notional_usd, ttl_seconds,
        )
        return order_id

    def _live_limit_entry(
        self,
        signal: TradeSignal,
        size: PositionSize,
        limit_price: float,
        ttl_seconds: int,
    ) -> Optional[str]:
        """Place a live limit order on Hyperliquid."""
        if not self._client:
            log.error("[EXEC] No exchange client — cannot place limit order")
            return None

        is_buy = signal.direction == "LONG"

        try:
            self._client.set_leverage(signal.symbol, size.leverage, is_cross=True)

            result = self._client.place_limit_order(
                signal.symbol, is_buy, size.qty, limit_price, tif="Gtc",
            )

            if result.get("status") == "ok":
                statuses = result.get("response", {}).get("data", {}).get("statuses", [])
                oid = None
                for s in statuses:
                    if "resting" in s:
                        oid = s["resting"].get("oid")
                    elif "filled" in s:
                        # Immediate fill — treat as market fill
                        filled = s["filled"]
                        log.info("[LIVE-LIMIT] Immediately filled: %s @ $%s",
                                 signal.symbol, filled.get("avgPx", "?"))
                        pos_id = f"hl_{signal.symbol}_{int(time.time())}"
                        self._client.place_tpsl(
                            symbol=signal.symbol, qty=size.qty,
                            direction=signal.direction,
                            tp_price=signal.take_profit_1,
                            sl_price=signal.stop_loss,
                        )
                        return pos_id

                if oid:
                    order_id = f"hlimit_{oid}"
                    now = time.time()
                    self._pending_orders[order_id] = {
                        "id": order_id,
                        "hl_oid": oid,
                        "symbol": signal.symbol,
                        "direction": signal.direction,
                        "limit_price": limit_price,
                        "qty": size.qty,
                        "notional": size.notional_usd,
                        "leverage": size.leverage,
                        "stop_loss": signal.stop_loss,
                        "take_profit_1": signal.take_profit_1,
                        "take_profit_2": signal.take_profit_2,
                        "placed_time": now,
                        "expires_at": now + ttl_seconds,
                        "status": "pending",
                        "mode": "live",
                    }
                    log.info("[LIVE-LIMIT] Resting: %s %s @ $%.2f oid=%s",
                             signal.direction, signal.symbol, limit_price, oid)
                    return order_id

            log.error("[LIVE-LIMIT] Order rejected: %s", result)
            return None

        except Exception as e:
            log.error("[LIVE-LIMIT] Order failed: %s", str(e)[:200])
            return None

    def check_pending_orders(self, current_prices: dict[str, float]) -> list[str]:
        """Check paper pending orders — fill if price touches limit. Returns filled pos IDs."""
        filled_ids = []
        now = time.time()

        for oid, order in list(self._pending_orders.items()):
            if order.get("mode") != "paper":
                continue

            # Expire stale orders
            if now > order.get("expires_at", float("inf")):
                log.info("[PENDING] Expired: %s %s @ $%.2f",
                         order["direction"], order["symbol"], order["limit_price"])
                del self._pending_orders[oid]
                continue

            price = current_prices.get(order["symbol"], 0)
            if price <= 0:
                continue

            # Check if price has reached the limit
            limit_px = order["limit_price"]
            filled = False
            if order["direction"] == "LONG" and price <= limit_px:
                filled = True
            elif order["direction"] == "SHORT" and price >= limit_px:
                filled = True

            if filled:
                pos_id = self._convert_pending_to_position(oid, order)
                if pos_id:
                    filled_ids.append(pos_id)

        return filled_ids

    def _convert_pending_to_position(self, order_id: str, order: dict) -> Optional[str]:
        """Convert a filled pending order into an active paper position."""
        pos_id = f"paper_{uuid.uuid4().hex[:8]}"

        self._paper_positions[pos_id] = {
            "id": pos_id,
            "symbol": order["symbol"],
            "direction": order["direction"],
            "entry_price": order["limit_price"],
            "qty": order["qty"],
            "notional": order["notional"],
            "margin": order.get("margin", 0),
            "risk_usd": order.get("risk_usd", 0),
            "leverage": order["leverage"],
            "stop_loss": order["stop_loss"],
            "take_profit_1": order["take_profit_1"],
            "take_profit_2": order["take_profit_2"],
            "entry_time": time.time(),
            "confidence": order.get("confidence", 0),
            "macro_regime": order.get("macro_regime", ""),
            "macro_score": order.get("macro_score", 0),
            "entry_reason": order.get("entry_reason", "limit_fill"),
            "smc_patterns": order.get("smc_patterns", []),
            "atr": order.get("atr", 0),
            "from_limit_order": order_id,
        }

        self._exit_states[pos_id] = self._exit_mgr.init_exit_state(
            self._paper_positions[pos_id]
        )

        del self._pending_orders[order_id]

        log.info(
            "[LIMIT-FILL] %s %s @ $%.2f → position %s",
            order["direction"], order["symbol"], order["limit_price"], pos_id,
        )
        return pos_id

    def on_fill(self, fill_data: dict) -> None:
        """Handle a live fill from WS userFills. Converts pending → position."""
        fills = fill_data if isinstance(fill_data, list) else fill_data.get("fills", [])
        for fill in fills:
            coin = fill.get("coin", "")
            oid = fill.get("oid")
            if not coin or not oid:
                continue

            # Find matching pending order
            order_key = f"hlimit_{oid}"
            order = self._pending_orders.get(order_key)
            if not order:
                continue

            log.info("[WS-FILL] %s oid=%s filled @ $%s sz=%s",
                     coin, oid, fill.get("px", "?"), fill.get("sz", "?"))

            # Place TP/SL on the filled position
            if self._client and not self._dry_run:
                try:
                    self._client.place_tpsl(
                        symbol=order["symbol"],
                        qty=order["qty"],
                        direction=order["direction"],
                        tp_price=order["take_profit_1"],
                        sl_price=order["stop_loss"],
                    )
                except Exception as e:
                    log.error("[WS-FILL] TP/SL failed: %s", str(e)[:100])

            del self._pending_orders[order_key]

    def on_order_update(self, update_data: dict) -> None:
        """Handle order status change from WS orderUpdates."""
        updates = update_data if isinstance(update_data, list) else [update_data]
        for upd in updates:
            oid = upd.get("oid")
            status = upd.get("status", "")
            if not oid:
                continue

            order_key = f"hlimit_{oid}"
            if order_key not in self._pending_orders:
                continue

            if status in ("canceled", "rejected"):
                order = self._pending_orders.pop(order_key)
                log.info("[WS-ORDER] %s %s: %s",
                         order.get("symbol", "?"), status, order_key)

    def sweep_stale_orders(self, ttl_override: int = 0) -> int:
        """Cancel expired pending orders. Returns count cancelled."""
        now = time.time()
        cancelled = 0
        live_cancels = []

        for oid, order in list(self._pending_orders.items()):
            expires = order.get("expires_at", float("inf"))
            if now <= expires:
                continue

            if order.get("mode") == "live" and order.get("hl_oid"):
                live_cancels.append((order["symbol"], order["hl_oid"]))

            log.info("[SWEEP] Expired order: %s %s @ $%.2f (age=%.0fm)",
                     order.get("direction", "?"), order.get("symbol", "?"),
                     order.get("limit_price", 0),
                     (now - order.get("placed_time", now)) / 60)
            del self._pending_orders[oid]
            cancelled += 1

        # Bulk cancel live orders on exchange
        if live_cancels and self._client and not self._dry_run:
            try:
                self._client.bulk_cancel_orders(live_cancels)
            except Exception as e:
                log.error("[SWEEP] Bulk cancel error: %s", str(e)[:100])

        return cancelled

    def get_pending_orders(self) -> list[dict]:
        """Get all pending orders for dashboard."""
        return list(self._pending_orders.values())

    def get_pending_count_for_symbol(self, symbol: str) -> int:
        """Count pending orders for a symbol."""
        return sum(
            1 for o in self._pending_orders.values()
            if o.get("symbol") == symbol
        )

    def has_pending_for_symbol(self, symbol: str) -> bool:
        """Check if there are any pending orders for a symbol."""
        return self.get_pending_count_for_symbol(symbol) > 0

    # ── Logging ──

    def _log_signal(self, signal: TradeSignal, size: PositionSize) -> None:
        try:
            rec = {
                **signal.to_dict(),
                "size_margin": size.margin_usd,
                "size_notional": size.notional_usd,
                "size_qty": size.qty,
                "size_leverage": size.leverage,
                "size_risk_usd": size.risk_usd,
            }
            with open(self._signals_file, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except Exception:
            pass

    def _log_trade(self, result: TradeResult) -> None:
        try:
            rec = {
                "trade_id": result.trade_id,
                "symbol": result.symbol,
                "side": result.side,
                "entry_price": result.entry_price,
                "exit_price": result.exit_price,
                "qty": result.qty,
                "pnl_usd": result.pnl_usd,
                "pnl_pct": result.pnl_pct,
                "fees": result.fees,
                "leverage": result.leverage,
                "entry_time": result.entry_time,
                "exit_time": result.exit_time,
                "hold_hours": result.hold_duration_hours,
                "exit_reason": result.exit_reason,
                "exit_reason_detail": result.exit_reason_detail,
                "entry_signal": result.entry_signal,
                "confluence": result.confluence_score,
                "conviction_score": result.conviction_score,
                "conviction_tier": result.conviction_tier,
                "macro_regime": result.macro_regime,
                "macro_score": result.macro_score,
                "risk_pct": result.risk_pct,
                "rr_ratio": result.rr_ratio,
                "stop_loss": result.stop_loss_price,
                "expected_rr": result.expected_rr,
                "actual_rr": result.actual_rr,
                "is_win": result.is_win,
                "is_partial": result.is_partial,
                "partial_closes": result.partial_closes,
                "mode": "paper" if self._dry_run else "live",
            }
            with open(self._trades_file, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except Exception:
            pass
