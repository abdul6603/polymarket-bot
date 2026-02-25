"""Hawk V9 Live In-Play Position Manager.

Monitors open positions during live games via ESPN (free, 30s polls).
Makes autonomous EXIT / ADD / HOLD decisions based on:
  - Live score differential vs position direction
  - Time remaining in game
  - Market price movement (CLOB midpoint vs entry)
  - Odds API shift (surgical, every 5 min on score change only)

Safety:
  - Max 3 actions per game (prevents over-trading)
  - Min 5 min hold before any action
  - Graceful degradation: ESPN down → hold, don't panic sell
  - All actions logged with reasoning
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
LIVE_LOG_FILE = DATA_DIR / "hawk_live_actions.jsonl"


@dataclass
class LiveAction:
    """Record of a live trading action."""
    condition_id: str
    action: str          # EXIT, ADD, HOLD
    reason: str
    score_home: int
    score_away: int
    period: int
    clock: str
    entry_price: float
    current_price: float
    pnl_estimate: float
    timestamp: float = field(default_factory=time.time)


class LivePositionManager:
    """Monitors and manages open positions during live games."""

    def __init__(self, cfg, executor, tracker):
        self.cfg = cfg
        self.executor = executor
        self.tracker = tracker
        self._last_scores: dict[str, dict] = {}      # condition_id -> {home, away, period}
        self._actions_count: dict[str, int] = {}       # condition_id -> actions taken
        self._last_action_time: dict[str, float] = {}  # condition_id -> timestamp
        self._last_odds_check: float = 0.0             # global odds check timestamp
        self._paused: set[str] = set()                 # condition_ids paused by user
        self._live_prices: dict[str, float] = {}       # condition_id -> last CLOB midpoint

    def monitor_positions(self) -> list[LiveAction]:
        """Main entry — check all open positions against live games.

        Returns list of actions taken this cycle.
        """
        if not self.cfg.live_enabled:
            return []

        open_pos = [p for p in self.tracker.open_positions
                     if not p.get("resolved") and p.get("category") == "sports"]
        if not open_pos:
            return []

        actions = []

        # Phase 0: Merge on-chain positions not in tracker (catches older positions)
        try:
            onchain_file = Path(__file__).parent.parent / "data" / "hawk_positions_onchain.json"
            if onchain_file.exists():
                import json as _json
                onchain_data = _json.loads(onchain_file.read_text())
                onchain_pos = onchain_data if isinstance(onchain_data, list) else onchain_data.get("positions", [])
                tracker_cids = {p.get("condition_id", "") for p in open_pos}
                for op in onchain_pos:
                    cid = op.get("condition_id", "")
                    if cid and cid not in tracker_cids and not op.get("resolved"):
                        # Convert on-chain format to tracker format
                        open_pos.append({
                            "condition_id": cid,
                            "question": op.get("question", op.get("title", "")),
                            "direction": op.get("direction", "yes"),
                            "entry_price": op.get("entry_price", 0.5),
                            "size_usd": op.get("size_usd", op.get("value", 0)),
                            "token_id": op.get("token_id", ""),
                            "category": "sports" if op.get("category") in ("", "unknown", None) else op.get("category"),
                            "shares": op.get("shares", 0),
                            "cur_price": op.get("cur_price", 0),
                            "_from_onchain": True,
                        })
        except Exception:
            log.debug("[LIVE] On-chain position merge failed (non-fatal)")

        # Phase 1: Price-only take-profit scan (ALL positions, no ESPN needed)
        # CLOB price alone tells us if a position is essentially won
        remaining_pos = []
        for pos in open_pos:
            cid = pos.get("condition_id", "")
            if cid in self._paused:
                remaining_pos.append(pos)
                continue

            action = self._check_take_profit(pos)
            if action:
                actions.append(action)
                self._log_action(action)
            else:
                remaining_pos.append(pos)

        # Phase 2: Live game monitoring (game-matched positions only)
        if not remaining_pos:
            return actions

        try:
            from hawk.espn import get_live_games
            live_games = get_live_games()
        except Exception:
            log.debug("[LIVE] ESPN fetch failed — holding all positions")
            return actions

        if not live_games:
            return actions

        for pos in remaining_pos:
            cid = pos.get("condition_id", "")
            if cid in self._paused:
                continue

            game = self._match_to_game(pos, live_games)
            if not game:
                continue

            action = self._evaluate(pos, game)
            if action:
                actions.append(action)
                self._log_action(action)

        return actions

    def _check_take_profit(self, pos: dict) -> LiveAction | None:
        """Price-only take-profit check. No ESPN match needed.

        Sells when CLOB price indicates position is essentially won.
        Works even after game ends (ESPN drops it but CLOB still tradeable).
        """
        cid = pos.get("condition_id", "")
        direction = pos.get("direction", "yes")
        entry_price = pos.get("entry_price", 0.5)

        current_price = self._get_live_price(pos)
        # On-chain positions may have cur_price already
        if pos.get("_from_onchain") and current_price == pos.get("entry_price", 0.5):
            current_price = pos.get("cur_price", current_price)
        threshold = self.cfg.live_take_profit_threshold

        # Our token hitting high price = we won (both YES and NO tokens)
        should_sell = current_price >= threshold

        if not should_sell:
            return None

        # Check cooldown
        now = time.time()
        last_action = self._last_action_time.get(cid, 0)
        if last_action and (now - last_action) < 120:
            return None

        shares = pos.get("size_usd", 0) / entry_price if entry_price > 0 else 0
        if direction == "yes":
            pnl_estimate = (current_price - entry_price) * shares
        else:
            pnl_estimate = (entry_price - current_price) * shares

        profit_pct = (pnl_estimate / pos.get("size_usd", 1)) * 100 if pos.get("size_usd") else 0
        reason = (f"Take profit: {direction.upper()} @ {current_price:.2f} "
                  f"(entry {entry_price:.2f}, +{profit_pct:.0f}%) — "
                  f"locking ${pnl_estimate:.2f} profit, freeing capital")

        # Resolve token_id if missing (on-chain positions don't have it)
        if not pos.get("token_id") and self.executor and self.executor.client:
            try:
                market = self.executor.client.get_market(cid)
                tokens = market.get("tokens", [])
                for t in tokens:
                    if t.get("outcome", "").lower() == direction:
                        pos["token_id"] = t["token_id"]
                        log.info("[LIVE] Resolved token_id for %s (%s)", cid[:12], direction)
                        break
            except Exception:
                log.debug("[LIVE] Could not resolve token_id for %s", cid[:12])

        self._execute_exit(pos, reason)
        self._actions_count[cid] = self._actions_count.get(cid, 0) + 1
        self._last_action_time[cid] = now

        log.info("[LIVE] TAKE_PROFIT: %s | $%.2f profit | %s",
                 cid[:12], pnl_estimate, pos.get("question", "")[:50])

        return LiveAction(
            condition_id=cid, action="TAKE_PROFIT", reason=reason,
            score_home=0, score_away=0, period=0, clock="",
            entry_price=entry_price, current_price=current_price,
            pnl_estimate=pnl_estimate,
        )

    def _match_to_game(self, pos: dict, live_games: list[dict]) -> dict | None:
        """Match a position to a live ESPN game by question text."""
        question = pos.get("question", "").lower()
        best_match = None
        best_score = 0.0

        for game in live_games:
            home = game.get("home_team", "").lower()
            away = game.get("away_team", "").lower()
            if not home or not away:
                continue

            # Check if both team names appear in question
            home_parts = home.split()
            away_parts = away.split()

            # Match on last word (team name, not city) for better accuracy
            home_name = home_parts[-1] if home_parts else ""
            away_name = away_parts[-1] if away_parts else ""

            home_match = home_name in question and len(home_name) > 3
            away_match = away_name in question and len(away_name) > 3

            if home_match and away_match:
                return game  # Exact match
            elif home_match or away_match:
                score = 0.5
                if score > best_score:
                    best_score = score
                    best_match = game

        return best_match

    def _evaluate(self, pos: dict, game: dict) -> LiveAction | None:
        """Decision engine: EXIT, ADD, or HOLD.

        Logic:
        1. Check min hold time
        2. Check max actions per game
        3. Get current market price from CLOB
        4. Analyze score + time remaining
        5. Decide: EXIT if stop-loss hit, ADD if winning big, HOLD otherwise
        """
        cid = pos.get("condition_id", "")
        now = time.time()

        # Min hold time check
        opened_at = pos.get("opened_at", pos.get("order_placed_at", 0))
        if opened_at and (now - opened_at) < self.cfg.live_min_hold_minutes * 60:
            return None

        # Max actions check
        if self._actions_count.get(cid, 0) >= self.cfg.live_max_actions_per_game:
            return None

        # Cooldown between actions (2 min minimum)
        last_action = self._last_action_time.get(cid, 0)
        if last_action and (now - last_action) < 120:
            return None

        # Get live game data
        home_score = game.get("home_score", 0)
        away_score = game.get("away_score", 0)
        period = game.get("period", 0)
        clock = game.get("clock", "")
        sport = game.get("sport_key", "")

        # Determine if our position is winning or losing
        direction = pos.get("direction", "yes")
        question = pos.get("question", "").lower()
        entry_price = pos.get("entry_price", 0.5)

        # Get current market price from CLOB (cheap, no API cost)
        current_price = self._get_live_price(pos)

        # Calculate unrealized P&L
        shares = pos.get("size_usd", 0) / entry_price if entry_price > 0 else 0
        # Our token price up = profit, down = loss (same for YES and NO tokens)
        pnl_estimate = (current_price - entry_price) * shares

        # Score differential analysis
        score_diff = home_score - away_score
        position_assessment = self._assess_position(pos, game, current_price)

        # ── DECISION LOGIC ──

        # 0. TAKE PROFIT: Position essentially won — sell now, free capital
        take_profit_thresh = self.cfg.live_take_profit_threshold
        should_take_profit = False
        # Our token hitting high price = we won (both YES and NO tokens)
        if current_price >= take_profit_thresh:
            should_take_profit = True

        if should_take_profit:
            profit_pct = (pnl_estimate / pos.get("size_usd", 1)) * 100 if pos.get("size_usd") else 0
            reason = (f"Take profit: {direction.upper()} @ {current_price:.2f} "
                      f"(entry {entry_price:.2f}, +{profit_pct:.0f}%) — "
                      f"locking ${pnl_estimate:.2f} profit, freeing capital")
            self._execute_exit(pos, reason)
            return LiveAction(
                condition_id=cid, action="TAKE_PROFIT", reason=reason,
                score_home=home_score, score_away=away_score,
                period=period, clock=clock,
                entry_price=entry_price, current_price=current_price,
                pnl_estimate=pnl_estimate,
            )

        # 1. STOP-LOSS: Market price dropped significantly
        # current_price is OUR token's price (YES or NO) — drop = losing for both
        price_drop_pct = (entry_price - current_price) / entry_price if entry_price > 0 else 0

        if price_drop_pct >= self.cfg.live_stop_loss_pct:
            reason = f"Stop-loss hit: price moved {price_drop_pct:.0%} against us ({entry_price:.2f}→{current_price:.2f})"
            self._execute_exit(pos, reason)
            return LiveAction(
                condition_id=cid, action="EXIT", reason=reason,
                score_home=home_score, score_away=away_score,
                period=period, clock=clock,
                entry_price=entry_price, current_price=current_price,
                pnl_estimate=pnl_estimate,
            )

        # 2. SCORE-BASED EXIT: Losing badly late in game
        if position_assessment == "losing_badly" and self._is_late_game(sport, period, clock):
            reason = (f"Losing badly in late game: {home_score}-{away_score} "
                      f"P{period} {clock} | price {entry_price:.2f}→{current_price:.2f}")
            self._execute_exit(pos, reason)
            return LiveAction(
                condition_id=cid, action="EXIT", reason=reason,
                score_home=home_score, score_away=away_score,
                period=period, clock=clock,
                entry_price=entry_price, current_price=current_price,
                pnl_estimate=pnl_estimate,
            )

        # 3. SCALE-UP: Winning comfortably, edge increased
        if (position_assessment == "winning_big"
                and self._can_scale_up(pos)
                and current_price > entry_price * 1.10):
            extra = min(
                pos.get("size_usd", 15) * 0.5,  # Add 50% of original
                self.cfg.max_bet_usd * self.cfg.live_max_scale - pos.get("size_usd", 0),
            )
            if extra >= 3:  # Min $3 to be worth the trade
                reason = (f"Winning big: {home_score}-{away_score} P{period} | "
                          f"price {entry_price:.2f}→{current_price:.2f} (+{(current_price/entry_price - 1)*100:.0f}%)")
                self._execute_add(pos, extra, reason)
                return LiveAction(
                    condition_id=cid, action="ADD", reason=reason,
                    score_home=home_score, score_away=away_score,
                    period=period, clock=clock,
                    entry_price=entry_price, current_price=current_price,
                    pnl_estimate=pnl_estimate,
                )

        # 4. HOLD — log significant score changes
        prev = self._last_scores.get(cid, {})
        if prev and (prev.get("home") != home_score or prev.get("away") != away_score):
            log.info("[LIVE] Score update %s: %d-%d P%d %s | price $%.2f (entry $%.2f) | %s | %s",
                     cid[:8], home_score, away_score, period, clock,
                     current_price, entry_price, position_assessment,
                     pos.get("question", "")[:50])

        # Update tracking
        self._last_scores[cid] = {
            "home": home_score, "away": away_score, "period": period,
        }

        return None

    def _assess_position(self, pos: dict, game: dict, current_price: float) -> str:
        """Assess if our position is winning, losing, or neutral.

        Returns: 'winning_big', 'winning', 'neutral', 'losing', 'losing_badly'
        """
        entry_price = pos.get("entry_price", 0.5)
        direction = pos.get("direction", "yes")

        # Price-based assessment (most reliable)
        # current_price is OUR token — up = winning, down = losing, for both YES and NO
        price_change = (current_price - entry_price) / entry_price if entry_price > 0 else 0

        if price_change > 0.20:
            return "winning_big"
        elif price_change > 0.05:
            return "winning"
        elif price_change > -0.10:
            return "neutral"
        elif price_change > -0.25:
            return "losing"
        else:
            return "losing_badly"

    def _is_late_game(self, sport_key: str, period: int, clock: str) -> bool:
        """Check if the game is in its late stages."""
        if "nba" in sport_key or "basketball" in sport_key:
            return period >= 4  # 4th quarter or OT
        elif "nfl" in sport_key or "football" in sport_key:
            return period >= 4
        elif "nhl" in sport_key or "hockey" in sport_key:
            return period >= 3
        elif "mlb" in sport_key or "baseball" in sport_key:
            return period >= 7  # 7th inning stretch
        elif "soccer" in sport_key:
            # Parse clock for soccer (minutes)
            try:
                minutes = int(clock.replace("'", "").split(":")[0])
                return minutes >= 70
            except (ValueError, IndexError):
                pass
        return False

    def _get_live_price(self, pos: dict) -> float:
        """Get current CLOB midpoint for position's token."""
        cid = pos.get("condition_id", "")
        token_id = pos.get("token_id", "")

        if not token_id or self.cfg.dry_run:
            return pos.get("entry_price", 0.5)

        try:
            if self.executor.client:
                mid = self.executor.client.get_midpoint(token_id)
                if isinstance(mid, dict):
                    mid = mid.get("mid", pos.get("entry_price", 0.5))
                price = float(mid) if mid else pos.get("entry_price", 0.5)
                self._live_prices[cid] = price
                return price
        except Exception:
            log.debug("[LIVE] CLOB midpoint failed for %s", cid[:8])

        # Fallback to last known
        return self._live_prices.get(cid, pos.get("entry_price", 0.5))

    def _can_scale_up(self, pos: dict) -> bool:
        """Check if we can add to this position (max scale not exceeded)."""
        current_size = pos.get("size_usd", 0)
        original_size = pos.get("size_usd", 0)  # TODO: track original vs current
        max_total = original_size * self.cfg.live_max_scale
        return current_size < max_total

    def _execute_exit(self, pos: dict, reason: str) -> None:
        """Execute a position exit."""
        cid = pos.get("condition_id", "")
        sell_id = self.executor.sell_position(pos, reason)
        if sell_id:
            self._actions_count[cid] = self._actions_count.get(cid, 0) + 1
            self._last_action_time[cid] = time.time()
            log.info("[LIVE] EXIT executed: %s | %s", sell_id, reason)
            # Remove from tracker after successful sell
            self.tracker.remove_position(pos.get("order_id", ""))
            # Add cooldown to prevent re-buying
            self.tracker.add_cooldown(cid)

    def _execute_add(self, pos: dict, extra_usd: float, reason: str) -> None:
        """Execute a scale-up."""
        cid = pos.get("condition_id", "")
        add_id = self.executor.add_to_position(pos, extra_usd, reason)
        if add_id:
            self._actions_count[cid] = self._actions_count.get(cid, 0) + 1
            self._last_action_time[cid] = time.time()
            # Update position size in tracker
            for p in self.tracker._positions:
                if p.get("condition_id") == cid:
                    p["size_usd"] = p.get("size_usd", 0) + extra_usd
                    break
            log.info("[LIVE] ADD executed: %s | +$%.2f | %s", add_id, extra_usd, reason)

    def pause_position(self, condition_id: str) -> None:
        """Pause live monitoring for a specific position."""
        self._paused.add(condition_id)
        log.info("[LIVE] Paused monitoring for %s", condition_id[:12])

    def resume_position(self, condition_id: str) -> None:
        """Resume live monitoring for a paused position."""
        self._paused.discard(condition_id)
        log.info("[LIVE] Resumed monitoring for %s", condition_id[:12])

    def get_live_status(self) -> list[dict]:
        """Get live status for all monitored positions (dashboard API)."""
        statuses = []
        for pos in self.tracker.open_positions:
            if pos.get("resolved") or pos.get("category") != "sports":
                continue
            cid = pos.get("condition_id", "")
            score_data = self._last_scores.get(cid, {})
            statuses.append({
                "condition_id": cid,
                "question": pos.get("question", "")[:100],
                "direction": pos.get("direction", ""),
                "entry_price": pos.get("entry_price", 0),
                "current_price": self._live_prices.get(cid, pos.get("entry_price", 0)),
                "size_usd": pos.get("size_usd", 0),
                "home_score": score_data.get("home", None),
                "away_score": score_data.get("away", None),
                "period": score_data.get("period", None),
                "actions_taken": self._actions_count.get(cid, 0),
                "paused": cid in self._paused,
                "assessment": self._assess_position(
                    pos, score_data,
                    self._live_prices.get(cid, pos.get("entry_price", 0)),
                ) if score_data else "pre_game",
            })
        return statuses

    def _log_action(self, action: LiveAction) -> None:
        """Persist action to JSONL for analysis."""
        try:
            DATA_DIR.mkdir(exist_ok=True)
            with open(LIVE_LOG_FILE, "a") as f:
                f.write(json.dumps({
                    "condition_id": action.condition_id,
                    "action": action.action,
                    "reason": action.reason,
                    "score": f"{action.score_home}-{action.score_away}",
                    "period": action.period,
                    "clock": action.clock,
                    "entry_price": action.entry_price,
                    "current_price": action.current_price,
                    "pnl_estimate": round(action.pnl_estimate, 2),
                    "timestamp": action.timestamp,
                }) + "\n")
        except Exception:
            log.debug("[LIVE] Failed to log action")
