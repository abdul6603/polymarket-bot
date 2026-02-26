"""Resolution Scalper — trades the last 15-90s of 5m windows.

Engine #2 for Garves. Runs parallel to the flow scanner.
When a 5m window is near expiry and the outcome is near-certain
(BTC $200 above strike with 60s left), buys the winning token
for $0.82 and collects $1.00 on resolution.

Philosophy: fast math engine for execution (50ms decisions, $0/day).
            Opus reflection for learning (every 50 trades, tunes thresholds).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests as _requests

from bot.config import Config
from bot.snipe.probability_model import (
    ProbabilityEstimate,
    calculate_edge,
    calculate_ev,
    calculate_probability,
    detect_large_candle,
    estimate_drift,
    estimate_volatility,
    kelly_size,
)
from bot.snipe.resolution_learner import ResolutionLearner
from bot.snipe import clob_book

log = logging.getLogger("garves.snipe")

TRADES_FILE = Path(__file__).parent.parent.parent / "data" / "resolution_trades.jsonl"

# ── Thresholds (overridable via Config) ──────────────────────────
MIN_PROBABILITY = 0.75      # Don't trade below 75%
MIN_EDGE = 0.08             # 8% minimum edge
MAX_MARKET_PRICE = 0.88     # Never buy above $0.88
MIN_MARKET_PRICE = 0.20     # Never buy below $0.20 (no liquidity, fake PnL)
MIN_TIME_REMAINING = 15     # Need time to fill
MAX_TIME_REMAINING = 90     # Too much uncertainty beyond 90s
MAX_SPREAD = 0.08           # Skip illiquid books
MAX_CONCURRENT = 5          # Max simultaneous positions
MAX_BET_PER_WINDOW = 35.0   # Hard cap per window
FRACTIONAL_KELLY = 0.25     # Quarter-Kelly
FEE_RATE = 0.02             # Polymarket taker fee
SCALP_ASSETS = ("bitcoin", "ethereum", "solana", "xrp")

# Liquidity cap: never take more than 60% of ask-side depth
LIQUIDITY_CAP_PCT = 0.60


@dataclass
class ScalpOpportunity:
    """A scored opportunity ready for execution."""
    window_id: str
    asset: str
    direction: str          # "up" or "down"
    token_id: str
    current_price: float    # Spot price
    strike_price: float     # Window open price
    probability: float      # Calculated P
    market_price: float     # CLOB best ask (what we pay)
    edge: float             # P - market_price
    ev_per_dollar: float    # Expected value per $1
    kelly_bet: float        # Quarter-Kelly dollar amount
    z_score: float
    remaining_s: float
    sigma: float
    spread: float


@dataclass
class ScalpPosition:
    """An active position from the resolution scalper."""
    window_id: str
    asset: str
    direction: str
    token_id: str
    entry_price: float      # What we paid per share
    shares: float
    size_usd: float
    probability_at_entry: float
    edge_at_entry: float
    z_score_at_entry: float
    order_id: str
    entry_time: float = field(default_factory=time.time)
    window_end_ts: float = 0.0
    resolved: bool = False
    won: bool = False
    pnl: float = 0.0
    learner_id: int = 0     # ID in resolution_learner DB


class ResolutionScalper:
    """Engine #2 — resolution scalping on near-certain 5m outcomes."""

    def __init__(
        self,
        cfg: Config,
        price_cache,
        window_tracker,
        orderbook_signal,
        clob_client,
        dry_run: bool = True,
        bankroll: float = 250.0,
    ):
        self._cfg = cfg
        self._cache = price_cache
        self._windows = window_tracker
        self._orderbook = orderbook_signal
        self._client = clob_client
        self._dry_run = dry_run
        self._bankroll = bankroll

        # Config overrides
        self._min_prob = getattr(cfg, "res_scalp_min_prob", MIN_PROBABILITY)
        self._min_edge = getattr(cfg, "res_scalp_min_edge", MIN_EDGE)
        self._max_price = getattr(cfg, "res_scalp_max_price", MAX_MARKET_PRICE)
        self._max_bet = getattr(cfg, "res_scalp_max_bet", MAX_BET_PER_WINDOW)
        self._kelly_frac = getattr(cfg, "res_scalp_kelly_frac", FRACTIONAL_KELLY)
        self._max_concurrent = getattr(cfg, "res_scalp_max_concurrent", MAX_CONCURRENT)
        self._enabled = getattr(cfg, "res_scalp_enabled", True)

        self._positions: list[ScalpPosition] = []
        self._learner = ResolutionLearner()

        # Stats
        self._stats = {
            "scans": 0, "opportunities": 0, "trades": 0,
            "wins": 0, "losses": 0, "pnl": 0.0,
        }
        self._last_opportunities: list[dict] = []

        # Track which windows flow scanner has claimed
        self._flow_claimed: set[str] = set()

    def mark_flow_claimed(self, market_id: str) -> None:
        """Called by flow scanner when it claims a window."""
        self._flow_claimed.add(market_id)

    def tick(self, live_prices: dict[str, float]) -> None:
        """Called every 2s from engine.tick(). Core resolution scalper loop."""
        if not self._enabled:
            return

        self._stats["scans"] += 1

        # Phase 1: Check resolutions on active positions
        self._check_resolutions(live_prices)

        # Phase 2: Look for new opportunities (if not at capacity)
        active_count = sum(1 for p in self._positions if not p.resolved)
        if active_count >= self._max_concurrent:
            return

        # Phase 3: Scan all windows with 15-90s remaining
        now = time.time()
        opportunities: list[ScalpOpportunity] = []
        scan_assets: list[str] = []

        for window in self._windows.all_active_windows():
            remaining = window.end_ts - now
            if remaining < MIN_TIME_REMAINING or remaining > MAX_TIME_REMAINING:
                continue

            # Skip if flow scanner already claimed this window
            if window.market_id in self._flow_claimed:
                continue

            # Skip if we already have a position in this window
            if any(p.window_id == window.market_id and not p.resolved
                   for p in self._positions):
                continue

            # Skip assets not in our list
            if window.asset not in SCALP_ASSETS:
                continue

            scan_assets.append(f"{window.asset}:{int(remaining)}s")
            opp = self._evaluate_window(window, live_prices, remaining)
            if opp:
                opportunities.append(opp)

        if scan_assets:
            log.info("[RES-SCALP] Scanning %d windows: %s", len(scan_assets), ", ".join(scan_assets))

        # Sort by edge descending, execute best
        opportunities.sort(key=lambda o: o.edge, reverse=True)
        self._last_opportunities = [self._opp_to_dict(o) for o in opportunities[:5]]

        if opportunities:
            self._stats["opportunities"] += len(opportunities)
            best = opportunities[0]
            slots_available = self._max_concurrent - active_count
            for opp in opportunities[:slots_available]:
                self._execute(opp)

    def _evaluate_window(self, window, live_prices: dict, remaining: float):
        """Evaluate a single window. Returns ScalpOpportunity or None."""
        asset = window.asset
        current_price = live_prices.get(asset)
        if not current_price or current_price <= 0:
            return None

        strike_price = window.open_price
        if not strike_price or strike_price <= 0:
            return None

        # Get recent closes for volatility/drift estimation
        closes = self._cache.get_closes(asset, 15)
        if len(closes) < 3:
            return None

        sigma = estimate_volatility(closes)
        drift = estimate_drift(closes[-5:] if len(closes) >= 5 else closes)

        # Orderbook imbalance
        ob_imbalance = 0.0
        ob_reading = self._orderbook.get_latest_reading(asset) if self._orderbook else None
        if ob_reading:
            ob_imbalance = ob_reading.imbalance

        # Detect large candle (volatile spike) — skip if true
        candles = self._cache.get_candles(asset, 5)
        candle_dicts = [
            {"high": c.high, "low": c.low, "timestamp": c.timestamp}
            for c in candles if hasattr(c, "high")
        ]
        if detect_large_candle(candle_dicts, sigma):
            return None

        # Calculate probability
        est = calculate_probability(
            current_price, strike_price, remaining, sigma, drift, ob_imbalance,
        )
        if est.probability < self._min_prob:
            log.debug(
                "[RES-SCALP] %s %s SKIP P=%.0f%%<%.0f%% | spot=$%.2f strike=$%.2f T-%ds",
                asset.upper(), est.direction.upper(),
                est.probability * 100, self._min_prob * 100,
                current_price, strike_price, int(remaining),
            )
            return None

        # Determine which token to buy
        if est.direction == "up":
            token_id = window.up_token_id
        else:
            token_id = window.down_token_id

        # Get CLOB book for that token
        book = clob_book.get_orderbook(token_id)
        if not book:
            return None

        best_ask = book.get("best_ask")
        spread = book.get("spread", 1.0)
        if not best_ask or best_ask <= 0:
            return None

        # Gate: spread too wide
        if spread > MAX_SPREAD:
            log.debug(
                "[RES-SCALP] %s %s SKIP spread=%.2f>%.2f | P=%.0f%% T-%ds",
                asset.upper(), est.direction.upper(), spread, MAX_SPREAD,
                est.probability * 100, int(remaining),
            )
            return None

        # Gate: price too low (no real liquidity, inflated PnL)
        if best_ask < MIN_MARKET_PRICE:
            log.info(
                "[RES-SCALP] %s %s SKIP ask=$%.3f<$%.2f (penny) | P=%.0f%% T-%ds",
                asset.upper(), est.direction.upper(), best_ask, MIN_MARKET_PRICE,
                est.probability * 100, int(remaining),
            )
            return None

        # Gate: dead zone $0.55-$0.70 (below breakeven WR for both directions)
        if 0.55 <= best_ask < 0.70:
            log.info(
                "[RES-SCALP] %s %s SKIP ask=$%.2f in dead zone $0.55-$0.70 | T-%ds",
                asset.upper(), est.direction.upper(), best_ask, int(remaining),
            )
            return None

        # Gate: UP direction above $0.80 (needs 82%+ WR, only getting 62%)
        if est.direction.lower() == "up" and best_ask >= 0.80:
            log.info(
                "[RES-SCALP] %s %s SKIP UP at $%.2f>=0.80 (unprofitable) | T-%ds",
                asset.upper(), est.direction.upper(), best_ask, int(remaining),
            )
            return None

        # Gate: price too high
        if best_ask > self._max_price:
            log.info(
                "[RES-SCALP] %s %s SKIP ask=$%.2f>$%.2f | P=%.0f%% mkt=$%.2f T-%ds",
                asset.upper(), est.direction.upper(), best_ask, self._max_price,
                est.probability * 100, best_ask, int(remaining),
            )
            return None

        # Calculate edge
        edge = calculate_edge(est.probability, best_ask)
        if edge < self._min_edge:
            return None

        # EV per dollar
        ev = calculate_ev(est.probability, best_ask, FEE_RATE)

        # Kelly sizing
        bet = kelly_size(est.probability, best_ask, self._bankroll, self._kelly_frac)
        bet = min(bet, self._max_bet)

        # Liquidity cap: don't take more than 60% of ask-side depth
        sell_pressure = book.get("sell_pressure", 0)
        if sell_pressure > 0:
            liq_cap = sell_pressure * LIQUIDITY_CAP_PCT
            bet = min(bet, liq_cap)

        if bet < 1.0:  # Minimum viable bet
            return None

        log.info(
            "[RES-SCALP] %s %s: P=%.1f%% mkt=$%.2f edge=%.1f%% z=%.2f T-%ds bet=$%.1f",
            asset.upper(), est.direction.upper(), est.probability * 100,
            best_ask, edge * 100, est.z_score, int(remaining), bet,
        )

        return ScalpOpportunity(
            window_id=window.market_id,
            asset=asset,
            direction=est.direction,
            token_id=token_id,
            current_price=current_price,
            strike_price=strike_price,
            probability=est.probability,
            market_price=best_ask,
            edge=edge,
            ev_per_dollar=ev,
            kelly_bet=bet,
            z_score=est.z_score,
            remaining_s=remaining,
            sigma=sigma,
            spread=spread,
        )

    def _execute(self, opp: ScalpOpportunity) -> None:
        """Execute a scalp trade via FOK order."""
        shares = opp.kelly_bet / opp.market_price
        shares = round(shares, 2)

        # Record in learner
        learner_id = self._learner.record(
            asset=opp.asset,
            direction=opp.direction,
            window_id=opp.window_id,
            probability=opp.probability,
            market_price=opp.market_price,
            edge=opp.edge,
            z_score=opp.z_score,
            sigma=opp.sigma,
            remaining_s=opp.remaining_s,
            bet_size=opp.kelly_bet,
        )

        if self._dry_run:
            order_id = f"dry-res-{int(time.time())}"
            log.info(
                "[RES-SCALP][DRY] BUY %s %.1f shares @ $%.3f ($%.2f) | %s %s T-%ds",
                opp.direction.upper(), shares, opp.market_price, opp.kelly_bet,
                opp.asset.upper(), opp.direction.upper(), int(opp.remaining_s),
            )
        else:
            order_id = self._place_fok_order(opp.token_id, opp.market_price, shares)
            if not order_id:
                log.warning("[RES-SCALP] FOK order failed for %s", opp.window_id)
                return

        # Find window end time
        window = self._windows.get_window(opp.window_id)
        window_end = window.end_ts if window else time.time() + opp.remaining_s

        pos = ScalpPosition(
            window_id=opp.window_id,
            asset=opp.asset,
            direction=opp.direction,
            token_id=opp.token_id,
            entry_price=opp.market_price,
            shares=shares,
            size_usd=opp.kelly_bet,
            probability_at_entry=opp.probability,
            edge_at_entry=opp.edge,
            z_score_at_entry=opp.z_score,
            order_id=order_id,
            window_end_ts=window_end,
            learner_id=learner_id,
        )
        self._positions.append(pos)
        self._stats["trades"] += 1

        # Log to JSONL
        self._log_trade(opp, pos)

        # Notify
        dry_tag = "[DRY] " if self._dry_run else ""
        _dir_icon = "\U0001f7e2" if opp.direction.upper() == "UP" else "\U0001f534"
        _countdown = int(opp.remaining_s)
        self._notify(
            f"\u23f1 *GARVES RES-SCALP* {dry_tag}ENTRY\n"
            f"\n"
            f"{_dir_icon} {opp.direction.upper()} {opp.asset.upper()} / 5m\n"
            f"\U0001f4ca Prob: {opp.probability:.0%} | Edge: *{opp.edge:.0%}* | z: {opp.z_score:.2f}\n"
            f"\U0001f4b0 ${opp.kelly_bet:.2f} \u2192 {shares:.1f} shares @ ${opp.market_price:.3f}\n"
            f"\u23f3 Resolves in *{_countdown}s*",
            event_data={
                "engine": "resolution_scalper", "type": "entry",
                "asset": opp.asset.upper(), "direction": opp.direction.upper(),
                "size_usd": round(opp.kelly_bet, 2), "shares": shares,
                "price": opp.market_price, "probability": round(opp.probability, 3),
                "edge": round(opp.edge, 3), "z_score": round(opp.z_score, 2),
                "remaining_s": int(opp.remaining_s), "dry_run": self._dry_run,
            },
        )

    def _place_fok_order(self, token_id: str, price: float, shares: float) -> str | None:
        """Place FOK order on CLOB. Returns order_id or None."""
        if not self._client:
            log.error("[RES-SCALP] No CLOB client for live order")
            return None

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY

            order_args = OrderArgs(
                price=price,
                size=shares,
                side=BUY,
                token_id=token_id,
            )
            signed_order = self._client.create_order(order_args)
            resp = self._client.post_order(signed_order, OrderType.FOK)
            order_id = resp.get("orderID") or resp.get("id", "unknown")
            status = resp.get("status", "")

            log.info("[RES-SCALP] CLOB FOK response: %s", json.dumps(resp)[:500])

            if status.lower() in ("matched", "filled"):
                return order_id
            else:
                log.warning("[RES-SCALP] FOK not filled: status=%s", status)
                return None

        except Exception as e:
            log.error("[RES-SCALP] Order error: %s", str(e)[:200])
            return None

    def _check_resolutions(self, live_prices: dict[str, float]) -> None:
        """Check if any active positions have resolved."""
        now = time.time()
        for pos in self._positions:
            if pos.resolved:
                continue

            # Wait 30s past window end for resolution
            if now < pos.window_end_ts + 30:
                continue

            # Resolve
            current_price = live_prices.get(pos.asset)
            if not current_price:
                continue

            # Find the window to get strike price
            window = self._windows.get_window(pos.window_id)
            if window:
                strike = window.open_price
            else:
                # Window may have been cleaned up — use position data
                # Can't determine winner without strike, mark as loss
                log.warning("[RES-SCALP] Window %s expired, can't resolve", pos.window_id)
                strike = None

            if strike is not None:
                if self._dry_run:
                    # Dry run: resolve based on final price vs strike
                    price_above = current_price > strike
                    won = (pos.direction == "up" and price_above) or \
                          (pos.direction == "down" and not price_above)
                else:
                    # Live: token resolves to $1 (won) or $0 (lost)
                    won = self._check_clob_resolution(pos)
            else:
                won = False

            pos.resolved = True
            pos.won = won
            if won:
                pos.pnl = pos.shares * (1.0 - pos.entry_price)
                self._stats["wins"] += 1
            else:
                pos.pnl = -pos.size_usd
                self._stats["losses"] += 1

            self._stats["pnl"] += pos.pnl
            self._learner.resolve(pos.learner_id, won, pos.pnl)

            # Write resolution back to JSONL so dashboard API can read it
            self._update_trade_log(pos)

            outcome = "WIN" if won else "LOSS"
            log.info(
                "[RES-SCALP] %s %s %s: $%.2f -> $%.2f (P=%.0f%% edge=%.0f%%)",
                outcome, pos.asset.upper(), pos.direction.upper(),
                pos.size_usd, pos.pnl,
                pos.probability_at_entry * 100, pos.edge_at_entry * 100,
            )

            _icon = "\U0001f7e2" if won else "\U0001f534"
            _result = "WIN" if won else "LOSS"
            dry_tag = "[DRY] " if self._dry_run else ""
            _total = self._stats['wins'] + self._stats['losses']
            _wr = (self._stats['wins'] / _total * 100) if _total > 0 else 0
            self._notify(
                f"\u23f1 *GARVES RES-SCALP* {dry_tag}\u2014 {_icon} *{_result}*\n"
                f"\n"
                f"{pos.direction.upper()} {pos.asset.upper()} / 5m\n"
                f"\U0001f4c9 Entry: ${pos.entry_price:.3f} | Size: ${pos.size_usd:.2f}\n"
                f"\U0001f4b0 P&L: *${pos.pnl:+.2f}*\n"
                f"\n"
                f"\U0001f4ca Season: {self._stats['wins']}W-{self._stats['losses']}L "
                f"({_wr:.0f}%) | Net: ${self._stats['pnl']:+.2f}",
                event_data={
                    "engine": "resolution_scalper", "type": "resolution",
                    "asset": pos.asset.upper(), "direction": pos.direction.upper(),
                    "won": won, "pnl": round(pos.pnl, 2),
                    "size_usd": round(pos.size_usd, 2), "entry_price": pos.entry_price,
                    "probability": round(pos.probability_at_entry, 3),
                    "dry_run": self._dry_run,
                },
            )

    def _check_clob_resolution(self, pos: ScalpPosition) -> bool:
        """Check CLOB API for token resolution status."""
        try:
            book = clob_book.get_orderbook(pos.token_id)
            if book:
                mid = (book.get("best_bid", 0) + book.get("best_ask", 0)) / 2
                return mid > 0.90  # Resolved to YES if trading near $1
        except Exception:
            pass
        return False

    def _log_trade(self, opp: ScalpOpportunity, pos: ScalpPosition) -> None:
        """Append trade to JSONL log."""
        entry = {
            "timestamp": time.time(),
            "engine": "resolution_scalper",
            "asset": opp.asset,
            "direction": opp.direction,
            "window_id": opp.window_id,
            "probability": round(opp.probability, 4),
            "market_price": opp.market_price,
            "edge": round(opp.edge, 4),
            "ev_per_dollar": round(opp.ev_per_dollar, 4),
            "z_score": round(opp.z_score, 3),
            "sigma": opp.sigma,
            "remaining_s": round(opp.remaining_s, 1),
            "bet_size": round(opp.kelly_bet, 2),
            "shares": pos.shares,
            "order_id": pos.order_id,
            "dry_run": self._dry_run,
        }
        try:
            TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(TRADES_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            log.warning("[RES-SCALP] Failed to write trade log: %s", str(e)[:100])

    def _update_trade_log(self, pos: ScalpPosition) -> None:
        """Update the JSONL entry for a resolved position with won/pnl."""
        try:
            if not TRADES_FILE.exists():
                return
            lines = TRADES_FILE.read_text().strip().split("\n")
            updated = False
            for i, line in enumerate(lines):
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("order_id") == pos.order_id:
                    entry["won"] = pos.won
                    entry["pnl"] = round(pos.pnl, 2)
                    entry["resolved"] = True
                    entry["resolved_at"] = time.time()
                    lines[i] = json.dumps(entry)
                    updated = True
                    break
            if updated:
                TRADES_FILE.write_text("\n".join(lines) + "\n")
        except Exception as e:
            log.warning("[RES-SCALP] Failed to update trade log: %s", str(e)[:100])

    def _notify(self, msg: str, event_data: dict | None = None) -> None:
        """Send Telegram notification + event bus publish."""
        # Event bus
        if event_data:
            try:
                from shared.events import publish, TRADE_EXECUTED
                publish(agent="garves", event_type=TRADE_EXECUTED,
                        data=event_data, summary=msg.split("\n")[0])
            except Exception:
                pass
        # Telegram
        try:
            tg_token = os.environ.get("TG_BOT_TOKEN", "")
            tg_chat = os.environ.get("TG_CHAT_ID", "")
            if tg_token and tg_chat:
                _requests.post(
                    f"https://api.telegram.org/bot{tg_token}/sendMessage",
                    json={"chat_id": tg_chat, "text": msg, "parse_mode": "Markdown"},
                    timeout=10,
                )
        except Exception:
            pass

    @staticmethod
    def _opp_to_dict(opp: ScalpOpportunity) -> dict:
        return {
            "asset": opp.asset,
            "direction": opp.direction,
            "probability": round(opp.probability * 100, 1),
            "market_price": opp.market_price,
            "edge": round(opp.edge * 100, 1),
            "ev_per_dollar": round(opp.ev_per_dollar, 3),
            "kelly_bet": round(opp.kelly_bet, 2),
            "z_score": round(opp.z_score, 2),
            "remaining_s": int(opp.remaining_s),
            "spread": opp.spread,
        }

    def get_status(self) -> dict:
        """Dashboard-friendly status snapshot."""
        active = [p for p in self._positions if not p.resolved]
        resolved = [p for p in self._positions if p.resolved]

        active_list = []
        for p in active:
            remaining = max(0, p.window_end_ts - time.time())
            active_list.append({
                "asset": p.asset,
                "direction": p.direction,
                "entry_price": p.entry_price,
                "size_usd": round(p.size_usd, 2),
                "shares": p.shares,
                "probability": round(p.probability_at_entry * 100, 1),
                "edge": round(p.edge_at_entry * 100, 1),
                "z_score": round(p.z_score_at_entry, 2),
                "remaining_s": int(remaining),
            })

        recent_resolved = []
        for p in resolved[-10:]:
            recent_resolved.append({
                "asset": p.asset,
                "direction": p.direction,
                "won": p.won,
                "pnl": round(p.pnl, 2),
                "entry_price": p.entry_price,
                "size_usd": round(p.size_usd, 2),
                "probability": round(p.probability_at_entry * 100, 1),
                "edge": round(p.edge_at_entry * 100, 1),
            })

        learner_stats = self._learner.get_stats()

        return {
            "enabled": self._enabled,
            "dry_run": self._dry_run,
            "engine": "resolution_scalper",
            "version": "v1",
            "thresholds": {
                "min_probability": self._min_prob,
                "min_edge": self._min_edge,
                "max_market_price": self._max_price,
                "max_bet": self._max_bet,
                "kelly_fraction": self._kelly_frac,
                "max_concurrent": self._max_concurrent,
            },
            "stats": self._stats.copy(),
            "active_positions": active_list,
            "active_count": len(active),
            "recent_trades": recent_resolved,
            "opportunities": self._last_opportunities,
            "learner": learner_stats,
            "timestamp": time.time(),
        }
