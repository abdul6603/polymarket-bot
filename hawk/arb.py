"""Hawk Arbitrage Engine — guaranteed profit from mispriced binary markets.

Strategy: When the sum of best ASK prices for both outcomes of a binary market
is less than $0.98 (after Polymarket's 2% winner fee), buy both sides.
One side always resolves to $1.00, locking in risk-free profit.

Flow:
  1. Gamma pre-filter: scan binary markets where price sum < 0.97
  2. CLOB deep check: verify actual orderbook best asks sum < $0.98
  3. Execute: FOK buy both legs; unwind if second leg fails
  4. Resolve: when market settles, collect $1.00 per share minus 2% fee
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from hawk.config import HawkConfig
from bot.http_session import get_session

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
ARB_TRADES_FILE = DATA_DIR / "hawk_arb_trades.jsonl"
ARB_STATUS_FILE = DATA_DIR / "hawk_arb_status.json"
ET = ZoneInfo("America/New_York")

POLYMARKET_FEE = 0.02  # 2% winner fee


@dataclass
class ArbOpportunity:
    """A binary market where buying both sides guarantees profit."""
    condition_id: str
    question: str
    token_a_id: str           # Outcome A token
    token_b_id: str           # Outcome B token
    outcome_a: str            # e.g. "Yes"
    outcome_b: str            # e.g. "No"
    ask_a: float              # Best ask from CLOB
    ask_b: float              # Best ask from CLOB
    combined_cost: float      # ask_a + ask_b
    net_profit_per_share: float  # 1.00 - combined - 0.02
    max_shares: float         # min(depth_a, depth_b)
    position_usd: float       # Capped by config
    expected_profit_usd: float
    volume: float = 0.0
    liquidity: float = 0.0
    category: str = "other"
    depth_a: float = 0.0
    depth_b: float = 0.0


class ArbEngine:
    """Scans binary markets for arbitrage and executes both-side buys."""

    def __init__(self, cfg: HawkConfig, client=None):
        self.cfg = cfg
        self.client = client  # py_clob_client.ClobClient or None
        self._open_arbs: list[dict] = []
        self._load_open_arbs()

    def _load_open_arbs(self) -> None:
        """Load unresolved arb trades from disk."""
        if not ARB_TRADES_FILE.exists():
            return
        try:
            with open(ARB_TRADES_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if not rec.get("resolved"):
                        self._open_arbs.append(rec)
            if self._open_arbs:
                log.info("Loaded %d open arb positions", len(self._open_arbs))
        except Exception:
            log.exception("Failed to load arb trades")

    def scan(self, markets) -> list[ArbOpportunity]:
        """Full arb scan pipeline: gamma prefilter -> CLOB deep check."""
        if not self.cfg.arb_enabled:
            return []

        candidates = self._gamma_prefilter(markets)
        if not candidates:
            log.info("[ARB] No binary arb candidates (all price sums >= 0.985)")
            return []

        log.info("[ARB] Scanning %d candidates with CLOB orderbook...", len(candidates))
        opportunities = []
        for market in candidates:
            opp = self._clob_deep_check(market)
            if opp:
                opportunities.append(opp)
            time.sleep(0.1)  # Rate limiting between CLOB fetches

        if opportunities:
            # Sort by profit per share descending
            opportunities.sort(key=lambda o: o.net_profit_per_share, reverse=True)
            log.info("[ARB] Found %d profitable arb opportunities!", len(opportunities))
            for opp in opportunities[:5]:
                log.info("[ARB]   %.3f profit/share | cost=%.4f | %s",
                         opp.net_profit_per_share, opp.combined_cost,
                         opp.question[:80])
        else:
            log.info("[ARB] No profitable arb opportunities after CLOB verification")

        return opportunities

    def _gamma_prefilter(self, markets) -> list:
        """Filter to binary markets where Gamma price sum < 0.985.

        Loosened from 0.97 to catch more candidates — Gamma prices are
        rough estimates; CLOB asks may be significantly lower.
        """
        candidates = []
        for m in markets:
            tokens = m.tokens
            if len(tokens) != 2:
                continue  # Only binary markets

            # Sum the Gamma-reported prices
            try:
                prices = [float(t.get("price", 0.5)) for t in tokens]
            except (ValueError, TypeError):
                continue

            price_sum = sum(prices)
            if price_sum < 0.985:
                candidates.append(m)

        log.info("[ARB] Gamma pre-filter: %d binary markets with price sum < 0.985", len(candidates))
        return candidates

    def _clob_deep_check(self, market) -> ArbOpportunity | None:
        """Verify arb opportunity using actual CLOB orderbook asks."""
        tokens = market.tokens
        if len(tokens) != 2:
            return None

        token_a = tokens[0]
        token_b = tokens[1]
        tid_a = token_a.get("token_id", "")
        tid_b = token_b.get("token_id", "")

        if not tid_a or not tid_b:
            return None

        # Fetch best asks from CLOB orderbook
        ask_a, depth_a = self._fetch_best_ask(tid_a)
        ask_b, depth_b = self._fetch_best_ask(tid_b)

        if ask_a <= 0 or ask_b <= 0:
            return None

        combined = ask_a + ask_b
        profit_per_share = 1.00 - combined - POLYMARKET_FEE

        if profit_per_share < self.cfg.arb_min_profit_pct:
            return None

        # Calculate position size
        max_shares_by_depth = min(depth_a, depth_b)
        if max_shares_by_depth * combined < self.cfg.arb_min_depth_usd:
            return None  # Not enough liquidity

        # Cap by bankroll and max_per_trade
        available_bankroll = self.cfg.arb_bankroll_usd - self._open_exposure()
        if available_bankroll <= 0:
            return None

        # V6: Auto-scale sizing by profit margin
        if profit_per_share >= 0.03:
            scale = 1.5    # 3%+ profit → go bigger
        elif profit_per_share >= 0.02:
            scale = 1.25   # 2%+ → moderate scale
        else:
            scale = 1.0    # standard

        max_usd = min(self.cfg.arb_max_per_trade * scale, available_bankroll)
        max_shares_by_budget = max_usd / combined if combined > 0 else 0
        shares = min(max_shares_by_depth, max_shares_by_budget)

        if shares <= 0:
            return None

        position_usd = round(shares * combined, 2)
        expected_profit = round(shares * profit_per_share, 2)

        return ArbOpportunity(
            condition_id=market.condition_id,
            question=market.question,
            token_a_id=tid_a,
            token_b_id=tid_b,
            outcome_a=token_a.get("outcome", "A"),
            outcome_b=token_b.get("outcome", "B"),
            ask_a=round(ask_a, 4),
            ask_b=round(ask_b, 4),
            combined_cost=round(combined, 4),
            net_profit_per_share=round(profit_per_share, 4),
            max_shares=round(shares, 2),
            position_usd=position_usd,
            expected_profit_usd=expected_profit,
            volume=market.volume,
            liquidity=market.liquidity,
            category=market.category,
            depth_a=round(depth_a, 2),
            depth_b=round(depth_b, 2),
        )

    def _fetch_best_ask(self, token_id: str) -> tuple[float, float]:
        """Fetch best ask price and total depth from CLOB orderbook.

        Returns (best_ask_price, total_depth_in_shares). Returns (0, 0) on failure.
        """
        try:
            resp = get_session().get(
                f"{self.cfg.clob_host}/book?token_id={token_id}",
                timeout=5,
            )
            if resp.status_code != 200:
                return (0.0, 0.0)

            book = resp.json()
            asks = book.get("asks", [])
            if not asks:
                return (0.0, 0.0)

            # Best ask = lowest price in the asks array
            best_ask = float("inf")
            total_depth = 0.0
            for ask in asks:
                price = float(ask.get("price", 0))
                size = float(ask.get("size", 0))
                if price < best_ask:
                    best_ask = price
                total_depth += size

            if best_ask == float("inf"):
                return (0.0, 0.0)

            return (best_ask, total_depth)
        except Exception:
            log.debug("Failed to fetch orderbook for token %s", token_id[:16])
            return (0.0, 0.0)

    def execute(self, opp: ArbOpportunity) -> dict | None:
        """Execute arb: buy both legs via FOK. Unwind on partial failure."""
        if len(self._open_arbs) >= self.cfg.arb_max_concurrent:
            log.info("[ARB] Max concurrent arbs reached (%d), skipping", self.cfg.arb_max_concurrent)
            return None

        now = time.time()
        arb_id = f"arb-{opp.condition_id[:8]}-{int(now)}"

        if self.cfg.dry_run:
            # Dry-run: simulate the fill
            log.info("[ARB][DRY RUN] Simulated arb: %s | cost=%.4f | profit=%.4f/share | $%.2f total profit",
                     opp.question[:60], opp.combined_cost, opp.net_profit_per_share, opp.expected_profit_usd)
            rec = self._build_trade_record(arb_id, opp, "dry_a", "dry_b")
            self._open_arbs.append(rec)
            self._append_trade(rec)
            self._bus_arb_placed(opp, arb_id)
            return rec

        if not self.client:
            log.error("[ARB] No CLOB client for live arb execution")
            return None

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY, SELL

            # Leg A: FOK buy
            args_a = OrderArgs(
                price=round(opp.ask_a, 2),
                size=opp.max_shares,
                side=BUY,
                token_id=opp.token_a_id,
            )
            signed_a = self.client.create_order(args_a)
            resp_a = self.client.post_order(signed_a, OrderType.FOK)
            order_a = resp_a.get("orderID") or resp_a.get("id", "")

            if not order_a:
                log.warning("[ARB] Leg A failed for %s — aborting", opp.condition_id[:12])
                return None

            log.info("[ARB] Leg A filled: %s | %s @ $%.4f", order_a, opp.outcome_a, opp.ask_a)

            # Leg B: FOK buy
            args_b = OrderArgs(
                price=round(opp.ask_b, 2),
                size=opp.max_shares,
                side=BUY,
                token_id=opp.token_b_id,
            )
            signed_b = self.client.create_order(args_b)
            resp_b = self.client.post_order(signed_b, OrderType.FOK)
            order_b = resp_b.get("orderID") or resp_b.get("id", "")

            if not order_b:
                # Leg B failed — unwind Leg A immediately
                log.warning("[ARB] Leg B failed — unwinding Leg A %s", order_a)
                self._unwind_leg(opp.token_a_id, opp.max_shares)
                return None

            log.info("[ARB] Leg B filled: %s | %s @ $%.4f", order_b, opp.outcome_b, opp.ask_b)
            log.info("[ARB] ARBITRAGE LOCKED: %s | cost=$%.4f | profit=$%.2f",
                     opp.question[:60], opp.combined_cost, opp.expected_profit_usd)

            rec = self._build_trade_record(arb_id, opp, order_a, order_b)
            self._open_arbs.append(rec)
            self._append_trade(rec)
            self._bus_arb_placed(opp, arb_id)
            return rec

        except Exception:
            log.exception("[ARB] Failed to execute arb for %s", opp.condition_id[:12])
            return None

    def _unwind_leg(self, token_id: str, shares: float) -> bool:
        """Emergency sell a leg via FOK at best bid for immediate execution."""
        if not self.client:
            return False
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import SELL

            # Fetch best bid to sell at
            resp = get_session().get(
                f"{self.cfg.clob_host}/book?token_id={token_id}",
                timeout=5,
            )
            if resp.status_code != 200:
                log.error("[ARB] Cannot fetch book for unwind of %s", token_id[:16])
                return False

            book = resp.json()
            bids = book.get("bids", [])
            if not bids:
                # No bids — sell at $0.01 as last resort
                sell_price = 0.01
            else:
                sell_price = max(float(b.get("price", 0.01)) for b in bids)

            args = OrderArgs(
                price=round(sell_price, 2),
                size=shares,
                side=SELL,
                token_id=token_id,
            )
            signed = self.client.create_order(args)
            # V6: Use FOK for immediate execution on emergency unwind
            resp = self.client.post_order(signed, OrderType.FOK)
            log.info("[ARB] Unwind FOK order placed: %s @ $%.2f for %.2f shares",
                     token_id[:16], sell_price, shares)
            return True
        except Exception:
            log.exception("[ARB] Failed to unwind leg %s", token_id[:16])
            return False

    def resolve(self) -> dict:
        """Check if any open arb positions have resolved (market settled)."""
        if not self._open_arbs:
            return {"resolved": 0, "profit": 0.0}

        resolved_count = 0
        total_profit = 0.0
        still_open = []

        for arb in self._open_arbs:
            cid = arb.get("condition_id", "")
            try:
                resp = get_session().get(
                    f"{self.cfg.gamma_host}/markets?condition_id={cid}",
                    timeout=5,
                )
                if resp.status_code != 200:
                    still_open.append(arb)
                    continue

                markets = resp.json()
                if not markets:
                    still_open.append(arb)
                    continue

                market = markets[0] if isinstance(markets, list) else markets
                if not market.get("resolved", False):
                    still_open.append(arb)
                    continue

                # Market resolved — calculate profit
                shares = arb.get("shares", 0)
                combined_cost = arb.get("combined_cost", 0)
                payout = shares * (1.0 - POLYMARKET_FEE)  # Winner gets $1 minus 2% fee
                cost = shares * combined_cost
                profit = round(payout - cost, 2)

                arb["resolved"] = True
                arb["resolve_time"] = time.time()
                arb["profit"] = profit
                self._update_trade_in_file(arb)

                resolved_count += 1
                total_profit += profit
                log.info("[ARB] Resolved: %s | profit=$%.2f | %s",
                         cid[:12], profit, arb.get("question", "")[:60])

            except Exception:
                log.debug("[ARB] Failed to check resolution for %s", cid[:12])
                still_open.append(arb)

        self._open_arbs = still_open

        if resolved_count > 0:
            log.info("[ARB] Resolved %d arbs | total profit: $%.2f", resolved_count, total_profit)

        return {"resolved": resolved_count, "profit": round(total_profit, 2)}

    def save_status(self) -> None:
        """Write arb status to JSON for dashboard."""
        DATA_DIR.mkdir(exist_ok=True)
        all_trades = self._load_all_trades()
        resolved = [t for t in all_trades if t.get("resolved")]
        total_profit = sum(t.get("profit", 0) for t in resolved)
        total_invested = sum(t.get("position_usd", 0) for t in all_trades)

        status = {
            "enabled": self.cfg.arb_enabled,
            "bankroll": self.cfg.arb_bankroll_usd,
            "open_arbs": len(self._open_arbs),
            "total_executed": len(all_trades),
            "total_resolved": len(resolved),
            "total_profit": round(total_profit, 2),
            "total_invested": round(total_invested, 2),
            "open_exposure": round(self._open_exposure(), 2),
            "positions": [
                {
                    "condition_id": a.get("condition_id", ""),
                    "question": a.get("question", "")[:120],
                    "outcome_a": a.get("outcome_a", ""),
                    "outcome_b": a.get("outcome_b", ""),
                    "ask_a": a.get("ask_a", 0),
                    "ask_b": a.get("ask_b", 0),
                    "combined_cost": a.get("combined_cost", 0),
                    "profit_per_share": a.get("profit_per_share", 0),
                    "shares": a.get("shares", 0),
                    "position_usd": a.get("position_usd", 0),
                    "expected_profit": a.get("expected_profit", 0),
                    "time_str": a.get("time_str", ""),
                } for a in self._open_arbs
            ],
            "last_update": datetime.now(ET).isoformat(),
        }
        try:
            ARB_STATUS_FILE.write_text(json.dumps(status, indent=2))
        except Exception:
            log.exception("Failed to save arb status")

    def summary(self) -> dict:
        """Summary stats for inclusion in main Hawk status."""
        all_trades = self._load_all_trades()
        resolved = [t for t in all_trades if t.get("resolved")]
        total_profit = sum(t.get("profit", 0) for t in resolved)
        return {
            "arb_enabled": self.cfg.arb_enabled,
            "arb_open": len(self._open_arbs),
            "arb_total": len(all_trades),
            "arb_resolved": len(resolved),
            "arb_profit": round(total_profit, 2),
            "arb_exposure": round(self._open_exposure(), 2),
            "arb_bankroll": self.cfg.arb_bankroll_usd,
        }

    def _open_exposure(self) -> float:
        """Total USD currently deployed in open arb positions."""
        return sum(a.get("position_usd", 0) for a in self._open_arbs)

    def _build_trade_record(self, arb_id: str, opp: ArbOpportunity,
                            order_a: str, order_b: str) -> dict:
        return {
            "arb_id": arb_id,
            "condition_id": opp.condition_id,
            "question": opp.question[:200],
            "category": opp.category,
            "token_a_id": opp.token_a_id,
            "token_b_id": opp.token_b_id,
            "outcome_a": opp.outcome_a,
            "outcome_b": opp.outcome_b,
            "order_a": order_a,
            "order_b": order_b,
            "ask_a": opp.ask_a,
            "ask_b": opp.ask_b,
            "combined_cost": opp.combined_cost,
            "profit_per_share": opp.net_profit_per_share,
            "shares": opp.max_shares,
            "position_usd": opp.position_usd,
            "expected_profit": opp.expected_profit_usd,
            "volume": opp.volume,
            "depth_a": opp.depth_a,
            "depth_b": opp.depth_b,
            "dry_run": self.cfg.dry_run,
            "resolved": False,
            "resolve_time": 0.0,
            "profit": 0.0,
            "timestamp": time.time(),
            "time_str": datetime.now(ET).strftime("%Y-%m-%d %I:%M%p"),
        }

    def _append_trade(self, rec: dict) -> None:
        """Append arb trade to JSONL file."""
        DATA_DIR.mkdir(exist_ok=True)
        try:
            with open(ARB_TRADES_FILE, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            log.exception("Failed to write arb trade")

    def _update_trade_in_file(self, updated_rec: dict) -> None:
        """Rewrite the JSONL file with updated record (for resolution)."""
        if not ARB_TRADES_FILE.exists():
            return
        try:
            lines = []
            with open(ARB_TRADES_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    if rec.get("arb_id") == updated_rec.get("arb_id"):
                        lines.append(json.dumps(updated_rec))
                    else:
                        lines.append(line)
            with open(ARB_TRADES_FILE, "w") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:
            log.exception("Failed to update arb trade file")

    def _load_all_trades(self) -> list[dict]:
        """Load all arb trades from JSONL."""
        if not ARB_TRADES_FILE.exists():
            return []
        trades = []
        try:
            with open(ARB_TRADES_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        trades.append(json.loads(line))
        except Exception:
            log.exception("Failed to load arb trades")
        return trades

    def _bus_arb_placed(self, opp: ArbOpportunity, arb_id: str) -> None:
        """Publish arb_placed event to shared event bus."""
        try:
            from shared.events import publish as bus_publish
            bus_publish(
                agent="hawk",
                event_type="arb_placed",
                data={
                    "arb_id": arb_id,
                    "condition_id": opp.condition_id,
                    "question": opp.question[:200],
                    "combined_cost": opp.combined_cost,
                    "profit_per_share": opp.net_profit_per_share,
                    "position_usd": opp.position_usd,
                    "expected_profit": opp.expected_profit_usd,
                    "category": opp.category,
                },
                summary=f"Hawk arb: ${opp.expected_profit_usd:.2f} profit on {opp.question[:80]}",
            )
        except Exception:
            log.debug("Event bus publish failed for arb_placed (non-fatal)")
