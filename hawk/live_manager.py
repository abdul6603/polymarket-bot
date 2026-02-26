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
        self._sold: set[str] = set()                   # condition_ids already sold (prevent repeats)
        self._sell_failures: dict[str, int] = {}       # condition_id -> consecutive sell failure count
        self._live_prices: dict[str, float] = {}       # condition_id -> last CLOB midpoint
        self._price_history: dict[str, list[float]] = {}  # condition_id -> last 10 midpoints
        self._peak_price: dict[str, float] = {}           # condition_id -> highest price seen
        self._match_confidence: dict[str, int] = {}        # condition_id -> last match confidence (0-100)

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

            action = self._check_price_exits(pos)
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

            game, confidence = self._match_to_game(pos, live_games)
            if not game:
                continue

            self._match_confidence[cid] = confidence
            if confidence < 90:
                log.warning("[LIVE] Low confidence match (%d%%) for %s — price-only mode | %s",
                            confidence, cid[:8], pos.get("question", "")[:50])

            action = self._evaluate(pos, game, confidence)
            if action:
                actions.append(action)
                self._log_action(action)

        return actions

    def _check_price_exits(self, pos: dict) -> LiveAction | None:
        """Price-only exit check. No ESPN match needed.

        Handles both take-profit AND stop-loss based purely on CLOB price.
        Works even after game ends (ESPN drops it but CLOB still tradeable).
        """
        cid = pos.get("condition_id", "")
        if cid in self._sold:
            return None  # Already sold, skip

        # Fix 8: Respect min hold time before any exit (matches _evaluate() logic)
        opened_at = pos.get("opened_at", pos.get("order_placed_at", 0))
        if opened_at and (time.time() - opened_at) < self.cfg.live_min_hold_minutes * 60:
            return None

        direction = pos.get("direction", "yes")
        entry_price = pos.get("entry_price", 0.5)

        # Resolve token_id FIRST so _get_live_price can fetch real CLOB midpoint
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

        current_price = self._get_live_price(pos)
        # On-chain positions may have cur_price already
        if pos.get("_from_onchain") and current_price == pos.get("entry_price", 0.5):
            current_price = pos.get("cur_price", current_price)

        # TAKE PROFIT: our token hit high price
        if current_price >= self.cfg.live_take_profit_threshold:
            pass  # fall through to exit logic below
        # STOP LOSS: our token dropped significantly
        elif entry_price > 0:
            drop_pct = (entry_price - current_price) / entry_price
            if drop_pct >= self.cfg.live_stop_loss_pct:
                pass  # fall through to exit logic below
            else:
                return None  # No action needed
        else:
            return None

        # Check cooldown
        now = time.time()
        last_action = self._last_action_time.get(cid, 0)
        if last_action and (now - last_action) < 120:
            return None

        shares = pos.get("size_usd", 0) / entry_price if entry_price > 0 else 0
        pnl_estimate = (current_price - entry_price) * shares

        # Determine if this is take-profit or stop-loss
        is_take_profit = current_price >= self.cfg.live_take_profit_threshold
        if is_take_profit:
            profit_pct = (pnl_estimate / pos.get("size_usd", 1)) * 100 if pos.get("size_usd") else 0
            reason = (f"Take profit: {direction.upper()} @ {current_price:.2f} "
                      f"(entry {entry_price:.2f}, +{profit_pct:.0f}%) — "
                      f"locking ${pnl_estimate:.2f} profit, freeing capital")
        else:
            drop_pct = (entry_price - current_price) / entry_price if entry_price > 0 else 0
            reason = (f"Stop-loss: {direction.upper()} @ {current_price:.2f} "
                      f"(entry {entry_price:.2f}, -{drop_pct:.0%}) — "
                      f"cutting ${abs(pnl_estimate):.2f} loss")

        self._execute_exit(pos, reason)
        self._actions_count[cid] = self._actions_count.get(cid, 0) + 1
        self._last_action_time[cid] = now

        action_name = "TAKE_PROFIT" if is_take_profit else "STOP_LOSS"
        log.info("[LIVE] %s: %s | $%.2f | %s",
                 action_name, cid[:12], pnl_estimate, pos.get("question", "")[:50])

        return LiveAction(
            condition_id=cid, action=action_name, reason=reason,
            score_home=0, score_away=0, period=0, clock="",
            entry_price=entry_price, current_price=current_price,
            pnl_estimate=pnl_estimate,
        )

    def _match_to_game(self, pos: dict, live_games: list[dict]) -> tuple[dict | None, int]:
        """Match a position to a live ESPN game with confidence scoring.

        Returns (game_dict_copy, confidence) where confidence is 0-100.
        For 'X vs Y' questions, requires both teams for high confidence.
        Single-team questions (spreads) can match on one team at lower confidence.
        """
        question = pos.get("question", "").lower()
        has_versus = " vs " in question or " vs. " in question

        best_match = None
        best_confidence = 0

        for game in live_games:
            home = game.get("home_team", "").lower()
            away = game.get("away_team", "").lower()
            if not home or not away:
                continue

            # Extract keywords (words > 3 chars) from ESPN team names
            home_keywords = [w for w in home.split() if len(w) > 3]
            away_keywords = [w for w in away.split() if len(w) > 3]

            # Check which ESPN teams appear in the question
            home_hit = any(kw in question for kw in home_keywords)
            away_hit = any(kw in question for kw in away_keywords)

            if not home_hit and not away_hit:
                continue

            # ── Score the match ──
            confidence = 0

            if home_hit and away_hit:
                # Both teams found — strong match
                confidence = 95
            elif has_versus:
                # Question has "X vs Y" but only ONE team matched → likely wrong game
                confidence = 35
            else:
                # Single-team question (e.g. "Spread: Cavaliers (-4.5)")
                confidence = 85

            # League consistency bonus
            sport_key = game.get("sport_key", "")
            if self._league_consistent(question, sport_key):
                confidence = min(confidence + 5, 100)

            if confidence > best_confidence:
                best_confidence = confidence
                best_match = game

        if best_match and best_confidence >= 35:
            # Return a copy so we don't mutate the ESPN cache
            match_copy = dict(best_match)
            match_copy["_match_confidence"] = best_confidence
            return match_copy, best_confidence

        return None, 0

    def _league_consistent(self, question: str, sport_key: str) -> bool:
        """Check if ESPN sport_key is plausible for this question."""
        q = question.lower()
        if "nba" in sport_key or "ncaab" in sport_key or "ncaam" in sport_key:
            # Basketball team names are distinctive — always consistent
            return True
        if "nfl" in sport_key:
            return True
        if "nhl" in sport_key:
            return True
        if "soccer" in sport_key:
            return "fc" in q or "united" in q or "city" in q
        return True  # Default: don't penalize unknown leagues

    def _evaluate(self, pos: dict, game: dict, confidence: int = 100) -> LiveAction | None:
        """Decision engine: EXIT, ADD, or HOLD.

        Logic:
        1. Check min hold time
        2. Check max actions per game
        3. Get current market price from CLOB
        4. Analyze score + time remaining (only if confidence >= 90)
        5. Decide: EXIT if stop-loss hit, ADD if winning big, HOLD otherwise

        confidence < 90 → price-only mode (no game-state decisions).
        """
        cid = pos.get("condition_id", "")
        if cid in self._sold:
            return None  # Already sold
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

        # Use verified game data only for game-state decisions
        verified_game = game if confidence >= 90 else None

        # 0. SMART EXIT: Adaptive take-profit based on momentum + game state
        smart = self._smart_exit_decision(pos, verified_game, current_price, pnl_estimate)
        if smart == "SELL":
            profit_pct = (pnl_estimate / pos.get("size_usd", 1)) * 100 if pos.get("size_usd") else 0
            reason = self._smart_exit_reason(pos, game, current_price, entry_price, pnl_estimate, profit_pct)
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

        # 2. SCORE-BASED EXIT: Losing badly late in game (verified matches only)
        if confidence >= 90 and position_assessment == "losing_badly" and self._is_late_game(sport, period, clock):
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

        # 3. SCALE-UP: Winning comfortably, edge increased (verified matches only)
        if (confidence >= 90 and position_assessment == "winning_big"
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
            conf_tag = f"conf={confidence}%" if confidence < 90 else ""
            log.info("[LIVE] Score update %s: %d-%d P%d %s | price $%.2f (entry $%.2f) | %s%s | %s",
                     cid[:8], home_score, away_score, period, clock,
                     current_price, entry_price, position_assessment,
                     f" | {conf_tag}" if conf_tag else "",
                     pos.get("question", "")[:50])

        # Update tracking
        self._last_scores[cid] = {
            "home": home_score, "away": away_score, "period": period,
            "confidence": confidence,
        }

        return None

    def _smart_exit_decision(self, pos: dict, game: dict, current_price: float, pnl: float) -> str:
        """Adaptive exit decision. Returns 'SELL', 'HOLD', or 'WAIT'.

        Decision factors:
        1. Profit level — are we up enough to care?
        2. Momentum — is price still climbing or reversing?
        3. Game state — blowout with 2 min left? Hold for resolution.
        4. Peak drawdown — did we peak and now dropping?
        """
        cid = pos.get("condition_id", "")
        entry_price = pos.get("entry_price", 0.5)
        size = pos.get("size_usd", 1)
        profit_pct = (pnl / size) * 100 if size > 0 else 0

        # Not in profit — no take-profit decision needed
        if profit_pct < 15:
            return "WAIT"

        # ── HOLD FOR RESOLUTION: blowout + late game ──
        if game and self._is_late_game(game.get("sport_key", ""), game.get("period", 0), game.get("clock", "")):
            score_gap = abs(game.get("home_score", 0) - game.get("away_score", 0))
            sport = game.get("sport_key", "")
            # Big lead in late game = let it resolve
            blowout = False
            if ("nba" in sport or "ncaab" in sport or "basketball" in sport) and score_gap >= 15:
                blowout = True
            elif ("nfl" in sport or "football" in sport) and score_gap >= 14:
                blowout = True
            elif ("nhl" in sport or "hockey" in sport) and score_gap >= 3:
                blowout = True
            elif "soccer" in sport and score_gap >= 2:
                blowout = True

            if blowout and current_price >= 0.90:
                log.info("[LIVE] HOLD for resolution: %s blowout %d-%d late game, price %.2f | %s",
                         sport, game.get("home_score", 0), game.get("away_score", 0),
                         current_price, pos.get("question", "")[:40])
                return "HOLD"

        # ── MOMENTUM REVERSAL: price peaked and dropping ──
        hist = self._price_history.get(cid, [])
        peak = self._peak_price.get(cid, current_price)

        if len(hist) >= 4 and peak > entry_price:
            # Check if price dropped from peak
            peak_drawdown = (peak - current_price) / peak if peak > 0 else 0
            # Recent trend: compare last 3 prices
            recent = hist[-3:]
            dropping = all(recent[i] <= recent[i-1] for i in range(1, len(recent)))

            # Peaked and now dropping — sell before it gets worse
            if peak_drawdown >= 0.08 and dropping and profit_pct >= 20:
                log.info("[LIVE] Momentum reversal: peaked at %.2f, now %.2f (-%0.f%%), selling | %s",
                         peak, current_price, peak_drawdown * 100, pos.get("question", "")[:40])
                return "SELL"

        # ── STRONG PROFIT: up 40%+ and game is not a guaranteed win ──
        if profit_pct >= 40:
            # If game is live and close, sell to lock gains
            if game:
                score_gap = abs(game.get("home_score", 0) - game.get("away_score", 0))
                sport = game.get("sport_key", "")
                is_close = False
                if ("nba" in sport or "ncaab" in sport or "basketball" in sport) and score_gap <= 10:
                    is_close = True
                elif ("nfl" in sport or "football" in sport) and score_gap <= 7:
                    is_close = True
                elif "soccer" in sport and score_gap <= 1:
                    is_close = True

                if is_close:
                    return "SELL"

            # No game data but sitting on big profit — sell
            if not game:
                return "SELL"

        # ── NEAR CERTAIN: price >= 93% — sell unless holding for resolution
        if current_price >= 0.93:
            return "SELL"

        # ── GOOD PROFIT + LATE GAME RISK: up 25%+ in second half ──
        if profit_pct >= 25 and game:
            period = game.get("period", 0)
            sport = game.get("sport_key", "")
            if ("nba" in sport or "ncaab" in sport) and period >= 3:
                score_gap = abs(game.get("home_score", 0) - game.get("away_score", 0))
                if score_gap <= 8:  # Close game in 2nd half, lock profit
                    return "SELL"

        return "WAIT"

    def _smart_exit_reason(self, pos, game, current_price, entry_price, pnl, profit_pct) -> str:
        """Generate human-readable reason for the smart exit."""
        direction = pos.get("direction", "yes")
        game_info = ""
        if game:
            hs = game.get("home_score", 0)
            as_ = game.get("away_score", 0)
            p = game.get("period", 0)
            game_info = f" | game {as_}-{hs} P{p}"

        peak = self._peak_price.get(pos.get("condition_id", ""), current_price)
        if peak > current_price * 1.05:
            return (f"Smart exit (momentum reversal): {direction.upper()} peaked {peak:.2f}→{current_price:.2f} "
                    f"(entry {entry_price:.2f}, +{profit_pct:.0f}%) — locking ${pnl:.2f}{game_info}")
        else:
            return (f"Smart exit: {direction.upper()} @ {current_price:.2f} "
                    f"(entry {entry_price:.2f}, +{profit_pct:.0f}%) — locking ${pnl:.2f}{game_info}")

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
                # Track history for momentum detection
                hist = self._price_history.setdefault(cid, [])
                hist.append(price)
                if len(hist) > 10:
                    hist.pop(0)
                # Track peak
                if price > self._peak_price.get(cid, 0):
                    self._peak_price[cid] = price
                return price
        except Exception:
            log.debug("[LIVE] CLOB midpoint failed for %s", cid[:8])

        # Fallback to last known
        return self._live_prices.get(cid, pos.get("entry_price", 0.5))

    def _can_scale_up(self, pos: dict) -> bool:
        """Check if we can add to this position (max scale not exceeded).

        Fix 4: Uses original_size_usd (recorded at trade creation) instead of
        current size_usd, which grows with each add and allows infinite scaling.
        """
        current_size = pos.get("size_usd", 0)
        original_size = pos.get("original_size_usd", pos.get("size_usd", 0))
        max_total = original_size * self.cfg.live_max_scale
        return current_size < max_total

    def _execute_exit(self, pos: dict, reason: str) -> None:
        """Execute a position exit."""
        cid = pos.get("condition_id", "")

        # Skip if too many consecutive failures (likely phantom or resolved)
        if self._sell_failures.get(cid, 0) >= 3:
            if cid not in self._sold:
                log.warning("[LIVE] Giving up on %s after 3 failed sells — likely phantom/resolved", cid[:12])
                self._sold.add(cid)
            return

        sell_id = self.executor.sell_position(pos, reason)

        # Market closed / no orderbook — force-remove to stop retry spam
        if sell_id == "MARKET_CLOSED":
            log.warning("[LIVE] Market closed for %s — force-removing position", cid[:12])
            self._sold.add(cid)
            for p in list(self.tracker._positions):
                if p.get("condition_id") == cid:
                    self.tracker._positions.remove(p)
                    break
            self.tracker.add_cooldown(cid)
            return

        if not sell_id:
            self._sell_failures[cid] = self._sell_failures.get(cid, 0) + 1
            log.warning("[LIVE] Sell failed for %s (attempt %d/3)", cid[:12], self._sell_failures[cid])
            return

        if sell_id:
            self._sold.add(cid)  # Prevent repeated sells
            self._actions_count[cid] = self._actions_count.get(cid, 0) + 1
            self._last_action_time[cid] = time.time()
            log.info("[LIVE] EXIT executed: %s | %s", sell_id, reason)
            # Remove from tracker by condition_id (order_id may be missing for on-chain)
            removed = False
            for p in list(self.tracker._positions):
                if p.get("condition_id") == cid:
                    self.tracker._positions.remove(p)
                    removed = True
                    break
            if not removed:
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
                "match_confidence": self._match_confidence.get(cid, None),
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
