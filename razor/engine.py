"""Razor Engine — the brain. Detect arbs, execute, manage exits.

Pure math, no AI. When A + B < 1, the proof writes itself.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from razor.config import RazorConfig
from razor.feed import RazorFeed
from razor.executor import RazorExecutor
from razor.tracker import ArbPosition, RazorTracker
from razor.scanner import RazorMarket

log = logging.getLogger(__name__)

# Polymarket fees
WINNER_FEE = 0.02  # 2% on winning side payout


def _taker_fee(p: float) -> float:
    """Polymarket taker fee formula: 0.25 * (p * (1-p))^2.

    Fee is a function of the price — cheaper near 0/1, most expensive at 0.50.
    """
    q = p * (1.0 - p)
    return 0.25 * (q * q)


@dataclass
class ArbOpportunity:
    """A detected completeness arbitrage opportunity."""
    market: RazorMarket
    price_a: float
    price_b: float
    ask_a: float  # Real ask from CLOB orderbook
    ask_b: float
    spread: float  # 1.0 - (ask_a + ask_b)
    net_profit_per_dollar: float  # After all fees
    depth_a: float  # Shares available at best ask
    depth_b: float
    max_shares: float
    position_usd: float


class RazorEngine:
    """Core brain: scans prices, detects arbs, manages positions."""

    def __init__(self, cfg: RazorConfig, executor: RazorExecutor, tracker: RazorTracker):
        self.cfg = cfg
        self.executor = executor
        self.tracker = tracker
        self._last_opportunities: list[ArbOpportunity] = []
        # CLOB batch scanner state — rotates through all markets
        self._clob_scan_idx = 0
        self._clob_batch_size = 30  # Markets per batch (60 REST calls)

    def scan_opportunities(
        self, markets: list[RazorMarket], feed: RazorFeed,
    ) -> list[ArbOpportunity]:
        """Scan all markets for completeness arbitrage using WS ask prices.

        Uses WS best_asks (from book events) as primary signal.
        Falls back to WS mid-prices only if no ask data.
        Called every scan_interval_s (1s). Pure math — microsecond execution.
        """
        opportunities: list[ArbOpportunity] = []

        for market in markets:
            if self.tracker.has_position(market.condition_id):
                continue

            # Read WS data (microsecond read)
            price_a, price_b, bid_a, bid_b, ask_a, ask_b = feed.get_pair_prices(
                market.token_a_id, market.token_b_id,
            )

            # Use asks as primary signal (that's where arbs live)
            # Fall back to mid-price, then Gamma price
            eff_a = ask_a if ask_a > 0 else (price_a if price_a > 0 else market.price_a)
            eff_b = ask_b if ask_b > 0 else (price_b if price_b > 0 else market.price_b)

            if eff_a <= 0 or eff_b <= 0:
                continue

            # THE MATH: spread = 1.00 - (ask_a + ask_b)
            combined = eff_a + eff_b
            spread = 1.0 - combined

            if spread < self.cfg.min_spread:
                continue

            opportunities.append(ArbOpportunity(
                market=market,
                price_a=price_a if price_a > 0 else market.price_a,
                price_b=price_b if price_b > 0 else market.price_b,
                ask_a=eff_a,
                ask_b=eff_b,
                spread=spread,
                net_profit_per_dollar=0.0,
                depth_a=0.0,
                depth_b=0.0,
                max_shares=0.0,
                position_usd=0.0,
            ))

        opportunities.sort(key=lambda o: o.spread, reverse=True)
        self._last_opportunities = opportunities

        if opportunities:
            log.info("Found %d potential arbs (top spread: %.3f = %.1f%%)",
                     len(opportunities), opportunities[0].spread,
                     opportunities[0].spread * 100)

        return opportunities

    def clob_batch_scan(self, markets: list[RazorMarket]) -> list[ArbOpportunity]:
        """Scan a batch of markets via CLOB REST orderbook.

        Rotates through ALL markets — catches arbs even without WS ask data.
        Called every ~5s from a dedicated loop.
        """
        n = len(markets)
        if n == 0:
            return []

        start = self._clob_scan_idx % n
        batch = []
        for i in range(self._clob_batch_size):
            idx = (start + i) % n
            m = markets[idx]
            if not self.tracker.has_position(m.condition_id):
                batch.append(m)
        self._clob_scan_idx = (start + self._clob_batch_size) % n

        opportunities: list[ArbOpportunity] = []
        best_spread = -1.0
        for m in batch:
            ask_a, depth_a = self.executor.fetch_best_ask(m.token_a_id)
            ask_b, depth_b = self.executor.fetch_best_ask(m.token_b_id)
            if ask_a <= 0 or ask_b <= 0:
                continue

            combined = ask_a + ask_b
            spread = 1.0 - combined
            if spread > best_spread:
                best_spread = spread
            if spread < self.cfg.min_spread:
                continue

            opportunities.append(ArbOpportunity(
                market=m,
                price_a=m.price_a,
                price_b=m.price_b,
                ask_a=ask_a,
                ask_b=ask_b,
                spread=spread,
                net_profit_per_dollar=0.0,
                depth_a=depth_a,
                depth_b=depth_b,
                max_shares=0.0,
                position_usd=0.0,
            ))

        # Always log progress so we can track scanning
        log.info("[CLOB SCAN] batch %d-%d/%d | checked=%d | best_spread=%.4f | arbs=%d",
                 start, (start + self._clob_batch_size) % n, n,
                 len(batch), best_spread, len(opportunities))

        if opportunities:
            opportunities.sort(key=lambda o: o.spread, reverse=True)

        return opportunities

    def execute_arb(self, opp: ArbOpportunity) -> ArbPosition | None:
        """Verify opportunity with CLOB orderbook and execute if still profitable.

        Returns ArbPosition if executed, None if skipped.
        """
        cfg = self.cfg

        # Check capital limits
        if self.tracker.open_count >= cfg.max_concurrent:
            log.debug("Max concurrent arbs reached (%d)", cfg.max_concurrent)
            return None
        if self.tracker.exposure >= cfg.max_exposure:
            log.debug("Max exposure reached ($%.2f)", self.tracker.exposure)
            return None

        # CLOB deep check — verify real ask prices
        ask_a, depth_a = self.executor.fetch_best_ask(opp.market.token_a_id)
        ask_b, depth_b = self.executor.fetch_best_ask(opp.market.token_b_id)

        if ask_a <= 0 or ask_b <= 0:
            log.debug("No asks for %s", opp.market.condition_id[:12])
            return None

        # Recalculate with real ask prices
        combined_cost = ask_a + ask_b
        spread = 1.0 - combined_cost

        # Calculate ALL fees
        fee_a = _taker_fee(ask_a)
        fee_b = _taker_fee(ask_b)
        total_taker_fee = fee_a + fee_b
        winner_fee = WINNER_FEE  # 2% on the $1.00 payout

        # Net profit per $1 pair: $1.00 - combined_cost - taker_fees - winner_fee
        net_profit = 1.0 - combined_cost - total_taker_fee - winner_fee

        if net_profit <= 0:
            log.debug("Not profitable after fees: spread=%.4f, fees=%.4f, net=%.4f | %s",
                      spread, total_taker_fee + winner_fee, net_profit,
                      opp.market.question[:60])
            return None

        if spread < cfg.min_spread:
            log.debug("Spread too thin after CLOB check: %.4f < %.4f",
                      spread, cfg.min_spread)
            return None

        # Depth check
        min_depth = min(depth_a, depth_b)
        if min_depth * combined_cost < cfg.min_depth_usd:
            log.debug("Insufficient depth: $%.2f < $%.2f",
                      min_depth * combined_cost, cfg.min_depth_usd)
            return None

        # Size the trade
        available = cfg.bankroll_usd - self.tracker.exposure
        max_usd = min(cfg.max_per_trade, available)
        if max_usd < cfg.min_per_trade:
            log.debug("Insufficient capital: $%.2f available", available)
            return None

        shares = min(min_depth, max_usd / combined_cost)
        position_usd = round(shares * combined_cost, 2)
        expected_profit = round(shares * net_profit, 2)

        if position_usd < cfg.min_per_trade:
            return None

        # Update opportunity with verified numbers
        opp.ask_a = ask_a
        opp.ask_b = ask_b
        opp.spread = spread
        opp.net_profit_per_dollar = net_profit
        opp.depth_a = depth_a
        opp.depth_b = depth_b
        opp.max_shares = shares
        opp.position_usd = position_usd

        log.info("EXECUTING ARB: %s | cost=$%.4f | spread=%.3f | net=%.4f/$ | $%.2f profit | %s",
                 opp.market.condition_id[:12], combined_cost, spread, net_profit,
                 expected_profit, opp.market.question[:60])

        # Execute
        result = self.executor.buy_both_sides(
            opp.market.token_a_id, opp.market.token_b_id,
            ask_a, ask_b, shares,
        )
        if not result:
            return None

        order_a, order_b = result
        arb_id = f"razor-{opp.market.condition_id[:8]}-{int(time.time())}"

        pos = ArbPosition(
            arb_id=arb_id,
            condition_id=opp.market.condition_id,
            question=opp.market.question,
            token_a_id=opp.market.token_a_id,
            token_b_id=opp.market.token_b_id,
            outcome_a=opp.market.outcome_a,
            outcome_b=opp.market.outcome_b,
            ask_a=ask_a,
            ask_b=ask_b,
            combined_cost=combined_cost,
            shares=shares,
            position_usd=position_usd,
            expected_profit=expected_profit,
            order_a=order_a,
            order_b=order_b,
            status="open",
            entry_time=time.time(),
            dry_run=cfg.dry_run,
        )
        self.tracker.add_position(pos)
        self._bus_arb_placed(pos)
        return pos

    def manage_exits(self, feed: RazorFeed) -> None:
        """THE KILLER FEATURE — manage all open positions for early exit.

        Called every scan_interval_s. For each open position:
        1. Early exit: sell losing side when winner > EXIT_THRESHOLD (0.70)
        2. Profit lock: sell winning side too when > PROFIT_LOCK (0.95)
        3. Max hold: force exit after MAX_HOLD_S (2 hours)
        4. Settlement: detect resolved markets
        """
        cfg = self.cfg

        for pos in self.tracker.open_positions:
            price_a, price_b, bid_a, bid_b, _ask_a, _ask_b = feed.get_pair_prices(
                pos.token_a_id, pos.token_b_id,
            )

            # Use last known ask as fallback
            if price_a <= 0:
                price_a = pos.ask_a
            if price_b <= 0:
                price_b = pos.ask_b

            # Determine winner and loser
            if price_a >= price_b:
                winner_price, loser_price = price_a, price_b
                winner_token, loser_token = pos.token_a_id, pos.token_b_id
                winner_bid = bid_a if bid_a > 0 else price_a
                loser_bid = bid_b if bid_b > 0 else price_b
            else:
                winner_price, loser_price = price_b, price_a
                winner_token, loser_token = pos.token_b_id, pos.token_a_id
                winner_bid = bid_b if bid_b > 0 else price_b
                loser_bid = bid_a if bid_a > 0 else price_a

            age = pos.age_s()

            # 1. MAX HOLD — force exit after 2 hours
            if age > cfg.max_hold_s:
                log.info("MAX HOLD reached (%.0fs): force-selling both sides | %s",
                         age, pos.question[:60])
                # Estimate recovery
                recovery = (winner_bid + loser_bid) * pos.shares
                cost = pos.position_usd
                pnl = recovery - cost
                self.executor.sell_both_sides(pos.token_a_id, pos.token_b_id, pos.shares)
                self.tracker.close_position(pos, round(pnl, 2), "max_hold")
                continue

            # 2. PROFIT LOCK — sell both when winner > 95%
            if winner_price >= cfg.profit_lock and pos.status == "open":
                log.info("PROFIT LOCK: winner @ $%.2f (>$%.2f) | %s",
                         winner_price, cfg.profit_lock, pos.question[:60])
                # Sell winner at ~95c → get ~93c after fee. Sell loser at ~3-5c bid
                winner_recovery = winner_bid * pos.shares
                loser_recovery = loser_bid * pos.shares
                total_recovery = winner_recovery + loser_recovery
                pnl = total_recovery - pos.position_usd
                self.executor.sell_both_sides(pos.token_a_id, pos.token_b_id, pos.shares)
                self.tracker.close_position(pos, round(pnl, 2), "profit_lock")
                continue

            # 3. EARLY EXIT — sell losing side when winner > 70%
            if winner_price >= cfg.exit_threshold and pos.status == "open":
                log.info("EARLY EXIT: selling loser (winner @ $%.2f) | %s",
                         winner_price, pos.question[:60])
                sell_result = self.executor.sell_side(loser_token, pos.shares)
                if sell_result:
                    recovery = loser_bid * pos.shares
                    pos.exit_recovery = round(recovery, 2)
                    pos.status = "exiting"
                    self.tracker.update_position(pos)
                    log.info("Recovered $%.2f from loser side", recovery)

            # 4. After early exit, check if winner hit profit lock threshold
            if pos.status == "exiting" and winner_price >= cfg.profit_lock:
                log.info("PROFIT LOCK on exiting pos: selling winner @ $%.2f | %s",
                         winner_price, pos.question[:60])
                self.executor.sell_side(winner_token, pos.shares)
                winner_recovery = winner_bid * pos.shares
                total_recovery = pos.exit_recovery + winner_recovery
                pnl = total_recovery - pos.position_usd
                self.tracker.close_position(pos, round(pnl, 2), "early_exit_profit_lock")

    def check_settlements(self) -> None:
        """Check if any open positions have settled (market resolved)."""
        from bot.http_session import get_session
        session = get_session()

        for pos in self.tracker.open_positions:
            try:
                resp = session.get(
                    f"{self.cfg.gamma_host}/markets?condition_id={pos.condition_id}",
                    timeout=5,
                )
                if resp.status_code != 200:
                    continue
                markets = resp.json()
                if not markets:
                    continue
                market = markets[0] if isinstance(markets, list) else markets
                if not market.get("resolved", False):
                    continue

                # Market resolved — collect payout
                shares = pos.shares
                payout = shares * (1.0 - WINNER_FEE)  # $1.00 - 2% fee per winning share
                cost = pos.position_usd
                # Subtract any recovery already counted
                pnl = payout + pos.exit_recovery - cost
                self.tracker.close_position(pos, round(pnl, 2), "settled")
                log.info("SETTLED: %s | payout=$%.2f | PnL=$%.2f | %s",
                         pos.arb_id, payout, pnl, pos.question[:60])
            except Exception:
                log.debug("Failed to check settlement for %s", pos.condition_id[:12])

    @property
    def last_opportunities(self) -> list[ArbOpportunity]:
        return self._last_opportunities

    def _bus_arb_placed(self, pos: ArbPosition) -> None:
        """Publish arb event to the shared event bus."""
        try:
            from shared.events import publish as bus_publish
            bus_publish(
                agent="razor",
                event_type="arb_placed",
                data={
                    "arb_id": pos.arb_id,
                    "condition_id": pos.condition_id,
                    "question": pos.question[:200],
                    "combined_cost": pos.combined_cost,
                    "spread": round(1.0 - pos.combined_cost, 4),
                    "position_usd": pos.position_usd,
                    "expected_profit": pos.expected_profit,
                    "shares": pos.shares,
                    "dry_run": pos.dry_run,
                },
                summary=f"Razor arb: ${pos.expected_profit:.2f} profit on {pos.question[:80]}",
            )
        except Exception:
            log.debug("Event bus publish failed (non-fatal)")
