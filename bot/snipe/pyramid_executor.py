"""Snipe Executor — Liquidity Seeker taker for 5-minute binary books.

Strategy:
  - Liquidity confirmed (ignition): FOK only, no GTC fallback
  - Score >= 75 (high conviction): FOK at best_ask + 3c (cross the spread)
  - Score 65-74 (moderate): GTC at best_ask + 1c (mild taker, can rest)
  - Dynamic sizing: min(desired, 60% of ask-side liquidity)
  - Falls back to GTC at price cap if no book data available
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from bot.config import Config

log = logging.getLogger("garves.snipe")

SNIPE_TRADES_FILE = Path(__file__).parent.parent.parent / "data" / "snipe_trades.jsonl"

# Wave config: (wave_num, budget_fraction, price_cap, fire_below_seconds)
WAVES = [
    (1, 1.00, 0.72, 120),   # 100% budget, $0.72 cap (raised for taker fills), T-120s
]

# Taker premium by conviction tier
TAKER_PREMIUM_HIGH = 0.03   # Score >= 75: cross spread by 3 cents
TAKER_PREMIUM_MOD = 0.01    # Score 65-74: cross spread by 1 cent
HIGH_CONVICTION_SCORE = 75  # Threshold for aggressive IOC/FOK execution
LIQUIDITY_CAP_PCT = 0.60    # Max 60% of available ask-side liquidity


@dataclass
class WaveResult:
    """Result of a single wave execution."""
    wave_num: int
    direction: str
    size_usd: float
    price: float
    shares: float
    token_id: str
    order_id: str
    filled: bool
    timestamp: float = field(default_factory=time.time)


@dataclass
class SnipePosition:
    """Tracks all waves for a single window."""
    market_id: str
    direction: str
    open_price: float
    asset: str = "bitcoin"
    score: float = 0.0              # v7 conviction score (0-100)
    score_breakdown: dict = field(default_factory=dict)  # per-component scores
    waves: list[WaveResult] = field(default_factory=list)
    total_size_usd: float = 0.0
    total_shares: float = 0.0
    avg_entry: float = 0.0
    started_at: float = field(default_factory=time.time)


class PyramidExecutor:
    """Executes GTC LIMIT orders on CLOB."""

    def __init__(
        self,
        cfg: Config,
        clob_client,
        dry_run: bool = True,
        budget_per_window: float = 50.0,
    ):
        self._cfg = cfg
        self._client = clob_client
        self._dry_run = dry_run
        self._budget = budget_per_window
        self._active_position: SnipePosition | None = None
        self._completed: list[SnipePosition] = []
        self._pending_order_id: str | None = None
        self._pending_token_id: str = ""
        self._latencies: list[float] = []  # Recent execution latencies in ms

    def start_position(
        self, market_id: str, direction: str, open_price: float,
        asset: str = "bitcoin", score: float = 0.0, score_breakdown: dict | None = None,
    ) -> None:
        """Initialize a new snipe position for this window."""
        self._active_position = SnipePosition(
            market_id=market_id,
            direction=direction,
            open_price=open_price,
            asset=asset,
            score=score,
            score_breakdown=score_breakdown or {},
        )
        self._pending_order_id = None
        log.info(
            "[SNIPE] Position started: %s %s %s (open=$%.2f, score=%.0f)",
            asset.upper(), direction.upper(), market_id[:12], open_price, score,
        )

    def should_fire_wave(self, wave_num: int, remaining_s: float, implied_price: float) -> bool:
        """Check if conditions are met to fire a specific wave."""
        if not self._active_position:
            return False

        fired_waves = {w.wave_num for w in self._active_position.waves}
        if wave_num in fired_waves:
            return False
        for prev in range(1, wave_num):
            if prev not in fired_waves:
                return False

        _, _, price_cap, fire_below = WAVES[wave_num - 1]
        if remaining_s > fire_below:
            return False
        if implied_price > price_cap:
            log.info("[SNIPE] Wave %d blocked: price $%.3f > cap $%.3f", wave_num, implied_price, price_cap)
            return False
        return True

    def execute_wave(
        self,
        wave_num: int,
        token_id: str,
        implied_price: float,
        score: float = 0.0,
        book_data: dict | None = None,
        liquidity_confirmed: bool = False,
    ) -> WaveResult | None:
        """Execute a single wave with Liquidity Seeker taker logic.

        Args:
            score: v7 conviction score (0-100) — determines aggressiveness
            book_data: CLOB orderbook dict with best_ask, sell_pressure, etc.
            liquidity_confirmed: if True, use FOK only (no GTC fallback)
        """
        if not self._active_position:
            return None

        _, budget_frac, price_cap, _ = WAVES[wave_num - 1]
        size_usd = self._budget * budget_frac

        # ── Determine price from book + conviction ──
        best_ask = self._get_best_ask(book_data)
        ask_depth_shares = self._get_ask_depth(book_data, price_cap)
        is_high_conviction = score >= HIGH_CONVICTION_SCORE

        if best_ask and best_ask < price_cap:
            premium = TAKER_PREMIUM_HIGH if (liquidity_confirmed or is_high_conviction) else TAKER_PREMIUM_MOD
            price = min(price_cap, round(best_ask + premium, 2))
        else:
            price = price_cap

        price = max(0.01, min(0.99, round(price, 2)))
        shares = int(size_usd / price)

        # ── Dynamic sizing: cap at 60% of ask-side liquidity ──
        if ask_depth_shares > 0 and shares > ask_depth_shares * LIQUIDITY_CAP_PCT:
            capped_shares = max(1, int(ask_depth_shares * LIQUIDITY_CAP_PCT))
            log.info(
                "[SNIPE EXEC] Liquidity cap: %d -> %d shares (book has %d on ask side)",
                shares, capped_shares, ask_depth_shares,
            )
            shares = capped_shares
            size_usd = round(shares * price, 2)

        # Liquidity confirmed → always FOK (no point resting on confirmed-liquid book)
        use_fok = liquidity_confirmed or is_high_conviction
        order_type = "FOK" if use_fok else "GTC"
        log.info(
            "[SNIPE EXEC] Score: %.0f | Book depth: %d shares | Best ask: $%.3f | "
            "Order: %s @ $%.3f | Size: %d shares ($%.2f)",
            score, ask_depth_shares, best_ask or 0,
            order_type, price, shares, size_usd,
        )

        t0 = time.time()
        if self._dry_run:
            order_id = f"snipe-dry-w{wave_num}-{int(time.time())}"
            log.info(
                "[SNIPE][DRY] %s Wave %d: %s %.1f shares @ $%.3f ($%.2f) | %s",
                order_type, wave_num, self._active_position.direction.upper(),
                shares, price, size_usd, order_id,
            )
            result = WaveResult(
                wave_num=wave_num,
                direction=self._active_position.direction,
                size_usd=size_usd,
                price=price,
                shares=shares,
                token_id=token_id,
                order_id=order_id,
                filled=True,
            )
            self._record_fill(result)
            self._record_latency(t0)
            return result
        else:
            result = self._place_order(
                wave_num, token_id, price, shares, size_usd,
                use_fok=use_fok,
            )
            self._record_latency(t0)
            if result:
                self._record_fill(result)
                log.info("[SNIPE EXEC] Filled: YES | %s | %.0f shares @ $%.3f", order_type, result.shares, result.price)
            else:
                if liquidity_confirmed:
                    # Liquidity Seeker: FOK-only, no GTC fallback
                    log.info("[IGNITION] FOK failed despite confirmed liquidity — phantom depth, no fallback")
                elif is_high_conviction and not self._pending_order_id:
                    # FOK failed (no liquidity) — retry as GTC to rest on book
                    log.info("[SNIPE EXEC] FOK failed, falling back to GTC resting")
                    result = self._place_order(
                        wave_num, token_id, price, shares, size_usd,
                        use_fok=False,
                    )
                    if result:
                        self._record_fill(result)
                        log.info("[SNIPE EXEC] Filled: YES (GTC fallback) | %.0f shares @ $%.3f", result.shares, result.price)
                    elif not self._pending_order_id:
                        log.info("[SNIPE EXEC] Filled: NO | book too thin for %s", order_type)
                else:
                    log.info("[SNIPE EXEC] Filled: NO (resting as GTC)")
            return result

    @staticmethod
    def _get_best_ask(book_data: dict | None) -> float | None:
        """Extract best ask price from CLOB book metrics."""
        if not book_data:
            return None
        best_ask = book_data.get("best_ask", 0)
        return best_ask if best_ask > 0 else None

    @staticmethod
    def _get_ask_depth(book_data: dict | None, max_price: float) -> int:
        """Estimate ask-side depth in shares from sell_pressure metric.

        sell_pressure = sum(price * size) for top 5 levels.
        We estimate shares = sell_pressure / avg_price.
        """
        if not book_data:
            return 0
        sell_pressure = book_data.get("sell_pressure", 0)
        best_ask = book_data.get("best_ask", 0)
        if sell_pressure <= 0 or best_ask <= 0:
            return 0
        return int(sell_pressure / best_ask)

    def _record_fill(self, result: WaveResult) -> None:
        """Record a fill in the active position."""
        if not self._active_position:
            return
        self._active_position.waves.append(result)
        self._active_position.total_size_usd += result.size_usd
        self._active_position.total_shares += result.shares
        total_cost = sum(w.size_usd for w in self._active_position.waves)
        total_shares = sum(w.shares for w in self._active_position.waves)
        self._active_position.avg_entry = total_cost / total_shares if total_shares > 0 else 0
        log.info(
            "[SNIPE] Fill recorded | $%.2f invested, %.1f shares, avg=$%.3f",
            self._active_position.total_size_usd,
            self._active_position.total_shares, self._active_position.avg_entry,
        )

    def _place_order(
        self,
        wave_num: int,
        token_id: str,
        price: float,
        shares: float,
        size_usd: float,
        use_fok: bool = False,
    ) -> WaveResult | None:
        """Place order on CLOB — FOK for high conviction, GTC otherwise.

        FOK (Fill or Kill): entire order fills immediately or cancels.
        GTC: sweeps existing asks, rests remainder as a bid.
        """
        if not self._client:
            log.error("[SNIPE] No CLOB client for live order")
            return None

        order_type_label = "FOK" if use_fok else "GTC"
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
            clob_type = OrderType.FOK if use_fok else OrderType.GTC
            resp = self._client.post_order(signed_order, clob_type)
            order_id = resp.get("orderID") or resp.get("id", "unknown")
            status = resp.get("status", "")

            log.info("[SNIPE] CLOB %s response: %s", order_type_label, json.dumps(resp)[:500])

            if status.lower() in ("matched", "filled"):
                actual_shares, actual_price, actual_size = self._parse_fill_data(
                    resp, shares, price, size_usd
                )
                if actual_shares < 1:
                    log.warning("[SNIPE] %s near-zero fill: %.2f shares | %s", order_type_label, actual_shares, order_id)
                    return None
                log.info(
                    "[SNIPE][LIVE] %s FILLED: %s %.1f shares @ $%.3f ($%.2f) | %s",
                    order_type_label, self._active_position.direction.upper(),
                    actual_shares, actual_price, actual_size, order_id,
                )
                self._pending_order_id = None
                return WaveResult(
                    wave_num=wave_num,
                    direction=self._active_position.direction,
                    size_usd=actual_size,
                    price=actual_price,
                    shares=actual_shares,
                    token_id=token_id,
                    order_id=order_id,
                    filled=True,
                )

            elif status.lower() == "live":
                self._pending_order_id = order_id
                self._pending_token_id = token_id

                taking = resp.get("takingAmount") or ""
                making = resp.get("makingAmount") or ""
                if taking and making:
                    partial_shares = float(taking)
                    partial_size = float(making)
                    partial_price = partial_size / partial_shares if partial_shares > 0 else price
                    if partial_shares >= 1:
                        log.info(
                            "[SNIPE][LIVE] %s PARTIAL: %.1f/%d shares @ $%.3f + resting | %s",
                            order_type_label, partial_shares, shares, partial_price, order_id,
                        )
                        return WaveResult(
                            wave_num=wave_num,
                            direction=self._active_position.direction,
                            size_usd=round(partial_size, 2),
                            price=partial_price,
                            shares=partial_shares,
                            token_id=token_id,
                            order_id=order_id,
                            filled=True,
                        )

                log.info(
                    "[SNIPE] %s RESTING at $%.3f (%d shares) | %s",
                    order_type_label, price, shares, order_id,
                )
                return None

            else:
                log.warning("[SNIPE] %s unexpected status: %s | %s", order_type_label, status, order_id)
                return None

        except Exception as e:
            log.error("[SNIPE] %s order failed (wave %d): %s", order_type_label, wave_num, str(e)[:200])
            return None

    def _parse_fill_data(
        self, resp: dict, default_shares: float, default_price: float, default_size: float
    ) -> tuple[float, float, float]:
        """Parse actual fill data from CLOB response."""
        actual_shares = 0.0
        actual_price = default_price
        actual_size = 0.0

        taking = resp.get("takingAmount") or None  # "" → None
        making = resp.get("makingAmount") or None  # "" → None
        if taking is not None and making is not None:
            actual_shares = float(taking)
            actual_size = float(making)
            actual_price = actual_size / actual_shares if actual_shares > 0 else default_price
        else:
            avg_price = resp.get("averagePrice") or resp.get("average_price")
            if avg_price is not None:
                actual_price = float(avg_price)
            matched = resp.get("matchedAmount") or resp.get("matched_amount")
            if matched is not None:
                actual_shares = float(matched)
            else:
                actual_shares = default_shares
            actual_size = round(actual_shares * actual_price, 2)

        return actual_shares, actual_price, actual_size

    def cancel_pending_order(self) -> None:
        """Cancel any resting GTC order."""
        if not self._pending_order_id or not self._client:
            self._pending_order_id = None
            return
        try:
            self._client.cancel(self._pending_order_id)
            log.info("[SNIPE] Cancelled GTC order %s", self._pending_order_id)
        except Exception as e:
            log.warning("[SNIPE] Cancel failed: %s", str(e)[:150])
        self._pending_order_id = None

    def poll_pending_order(self) -> WaveResult | None:
        """Check if resting GTC order got filled. Returns WaveResult if filled."""
        if not self._pending_order_id or not self._client or not self._active_position:
            return None
        try:
            order = self._client.get_order(self._pending_order_id)
            if not order:
                return None

            status = (order.get("status") or "").lower()
            size_matched = float(
                order.get("size_matched") or order.get("sizeMatched") or 0
            )
            original_size = float(
                order.get("original_size") or order.get("originalSize")
                or order.get("size") or 0
            )
            avg_price = float(
                order.get("associate_trades_avg_price")
                or order.get("average_price")
                or order.get("price") or 0
            )

            if size_matched < 1:
                return None

            fill_pct = size_matched / original_size if original_size > 0 else 0

            if status in ("matched", "filled") or fill_pct >= 0.90:
                actual_size = round(size_matched * avg_price, 2)
                log.info(
                    "[SNIPE] GTC FILLED (poll): %.0f/%.0f shares @ $%.3f ($%.2f)",
                    size_matched, original_size, avg_price, actual_size,
                )
                oid = self._pending_order_id
                self._pending_order_id = None
                result = WaveResult(
                    wave_num=1,
                    direction=self._active_position.direction,
                    size_usd=actual_size,
                    price=avg_price,
                    shares=size_matched,
                    token_id=self._pending_token_id,
                    order_id=oid or "unknown",
                    filled=True,
                )
                self._record_fill(result)
                return result

            if size_matched > 0:
                log.info(
                    "[SNIPE] GTC partial: %.0f/%.0f shares (%.0f%%)",
                    size_matched, original_size, fill_pct * 100,
                )
            return None

        except Exception as e:
            log.warning("[SNIPE] Poll order error: %s", str(e)[:150])
            return None

    def finalize_partial_fill(self) -> WaveResult | None:
        """After cancelling, check if any partial fills happened and record them."""
        if not self._pending_order_id or not self._client or not self._active_position:
            return None
        try:
            order = self._client.get_order(self._pending_order_id)
            if not order:
                return None
            size_matched = float(
                order.get("size_matched") or order.get("sizeMatched") or 0
            )
            avg_price = float(
                order.get("associate_trades_avg_price")
                or order.get("average_price")
                or order.get("price") or 0
            )
            if size_matched >= 1:
                actual_size = round(size_matched * avg_price, 2)
                result = WaveResult(
                    wave_num=1,
                    direction=self._active_position.direction,
                    size_usd=actual_size,
                    price=avg_price,
                    shares=size_matched,
                    token_id=self._pending_token_id,
                    order_id=self._pending_order_id or "unknown",
                    filled=True,
                )
                self._record_fill(result)
                return result
        except Exception:
            pass
        return None

    def close_position(self, resolved_direction: str = "") -> dict | None:
        """Close active position after window resolves. Returns result summary."""
        pos = self._active_position
        if not pos or not pos.waves:
            self._active_position = None
            self._pending_order_id = None
            return None

        won = None
        if resolved_direction:
            won = resolved_direction.lower() == pos.direction.lower()

        pnl = 0.0
        if won is True:
            pnl = pos.total_shares - pos.total_size_usd
        elif won is False:
            pnl = -pos.total_size_usd

        result = {
            "market_id": pos.market_id,
            "asset": pos.asset,
            "direction": pos.direction,
            "waves": len(pos.waves),
            "total_size_usd": round(pos.total_size_usd, 2),
            "total_shares": round(pos.total_shares, 2),
            "avg_entry": round(pos.avg_entry, 4),
            "pnl_usd": round(pnl, 2),
            "won": won,
            "open_price": pos.open_price,
            "hold_s": round(time.time() - pos.started_at),
            "score": pos.score,
            "score_breakdown": pos.score_breakdown,
        }

        self._log_trade(result)
        self._completed.append(pos)
        self._active_position = None
        self._pending_order_id = None

        status = "WIN" if won else "LOSS" if won is False else "PENDING"
        log.info(
            "[SNIPE] Position closed: %s %s | %d waves | $%.2f invested | "
            "avg=$%.3f | PnL $%+.2f | %s",
            pos.direction.upper(), pos.market_id[:12], len(pos.waves),
            pos.total_size_usd, pos.avg_entry, pnl, status,
        )
        return result

    def _log_trade(self, result: dict) -> None:
        """Append trade to snipe trades JSONL file."""
        try:
            SNIPE_TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(SNIPE_TRADES_FILE, "a") as f:
                f.write(json.dumps({**result, "timestamp": time.time()}) + "\n")
        except Exception:
            pass

    @property
    def has_active_position(self) -> bool:
        return self._active_position is not None

    @property
    def active_direction(self) -> str:
        if self._active_position:
            return self._active_position.direction
        return ""

    @property
    def waves_fired(self) -> int:
        if not self._active_position:
            return 0
        return len(self._active_position.waves)

    @property
    def has_pending_order(self) -> bool:
        return self._pending_order_id is not None

    def _record_latency(self, t0: float) -> None:
        """Record order execution latency."""
        elapsed_ms = round((time.time() - t0) * 1000, 1)
        self._latencies.append(elapsed_ms)
        if len(self._latencies) > 50:
            self._latencies = self._latencies[-50:]
        log.info("[SNIPE] Execution latency: %.1fms", elapsed_ms)

    def get_avg_latency_ms(self) -> float | None:
        """Average execution latency from recent trades."""
        if not self._latencies:
            return None
        return round(sum(self._latencies) / len(self._latencies), 1)

    def get_status(self) -> dict:
        """Dashboard-friendly status."""
        pos = self._active_position
        if pos:
            return {
                "active": True,
                "market_id": pos.market_id[:12],
                "asset": pos.asset,
                "direction": pos.direction,
                "waves_fired": len(pos.waves),
                "total_invested": round(pos.total_size_usd, 2),
                "total_shares": round(pos.total_shares, 2),
                "avg_entry": round(pos.avg_entry, 4),
                "pending_order": self._pending_order_id is not None,
            }
        return {"active": False}

    def get_history(self, limit: int = 20) -> list[dict]:
        """Recent completed snipe trades."""
        trades = []
        try:
            if SNIPE_TRADES_FILE.exists():
                lines = SNIPE_TRADES_FILE.read_text().strip().split("\n")
                for line in lines[-limit:]:
                    if line.strip():
                        trades.append(json.loads(line))
        except Exception:
            pass
        return trades

    def get_performance_stats(self) -> dict:
        """Score-bucketed performance stats from all trade history."""
        trades = self.get_history(limit=200)
        resolved = [t for t in trades if t.get("won") is not None]
        if not resolved:
            return {"total_trades": 0}

        # Overall stats
        wins = [t for t in resolved if t["won"] is True]
        losses = [t for t in resolved if t["won"] is False]
        total_pnl = sum(t.get("pnl_usd", 0) for t in resolved)
        avg_score_win = round(sum(t.get("score", 0) for t in wins) / len(wins), 1) if wins else 0
        avg_score_loss = round(sum(t.get("score", 0) for t in losses) / len(losses), 1) if losses else 0

        # Score buckets: 60-69, 70-79, 80-89, 90-100
        buckets = {}
        for lo, hi, label in [(60, 70, "60-69"), (70, 80, "70-79"), (80, 90, "80-89"), (90, 101, "90-100")]:
            bucket_trades = [t for t in resolved if lo <= t.get("score", 0) < hi]
            bucket_wins = sum(1 for t in bucket_trades if t["won"] is True)
            bucket_pnl = sum(t.get("pnl_usd", 0) for t in bucket_trades)
            buckets[label] = {
                "trades": len(bucket_trades),
                "wins": bucket_wins,
                "wr": round(bucket_wins / len(bucket_trades) * 100, 1) if bucket_trades else 0,
                "pnl": round(bucket_pnl, 2),
            }

        # Direction stats
        up_trades = [t for t in resolved if t.get("direction") == "up"]
        down_trades = [t for t in resolved if t.get("direction") == "down"]
        up_wins = sum(1 for t in up_trades if t["won"] is True)
        down_wins = sum(1 for t in down_trades if t["won"] is True)

        # Streak tracking
        streak = 0
        streak_type = ""
        for t in reversed(resolved):
            if not streak_type:
                streak_type = "W" if t["won"] else "L"
                streak = 1
            elif (t["won"] and streak_type == "W") or (not t["won"] and streak_type == "L"):
                streak += 1
            else:
                break

        return {
            "total_trades": len(resolved),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(resolved) * 100, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_score_winners": avg_score_win,
            "avg_score_losers": avg_score_loss,
            "score_buckets": buckets,
            "direction": {
                "up": {"trades": len(up_trades), "wins": up_wins,
                       "wr": round(up_wins / len(up_trades) * 100, 1) if up_trades else 0},
                "down": {"trades": len(down_trades), "wins": down_wins,
                         "wr": round(down_wins / len(down_trades) * 100, 1) if down_trades else 0},
            },
            "streak": f"{streak}{streak_type}",
        }
