"""Discord Intelligence → Odin Execution Pipeline.

Routes Discord trader signals through tier-based processing:
  TRUSTED (kiku, sn06)  → security checks → auto-execute → TG alert
  GOOD (abns92, miku)   → 4-agent voting → decision engine → TG alert
  LEARNING (charts-ideas) → Atlas KB only, zero execution

Consumes 'discord_signal' events from the shared event bus.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

from shared.llm_client import llm_call

log = logging.getLogger("odin.discord_pipeline")


# ── Trader Tier System ──

class TraderTier(Enum):
    TRUSTED = "trusted"    # kiku, sn06 — auto-execute
    GOOD = "good"          # abns92, miku — full voting
    EVALUATE = "evaluate"  # charts-ideas — score only, no execution
    LEARNING = "learning"  # ut-education — KB only


TRADER_REGISTRY: dict[str, TraderTier] = {
    "kiku": TraderTier.TRUSTED,
    "sn06": TraderTier.TRUSTED,
    "abns92": TraderTier.GOOD,
    "miku": TraderTier.GOOD,
}

# Channel → tier overrides (regardless of author)
EVALUATE_CHANNELS = {"charts-ideas"}
LEARNING_CHANNELS = {"ut-education"}

TIER_RISK = {
    TraderTier.TRUSTED: {"max_risk_usd": 180, "hard_cap_usd": 220, "max_positions": 3},
    TraderTier.GOOD: {"min_risk_usd": 50, "max_risk_usd": 75, "max_positions": 3},
}

# 4-agent voting weights (sum = 1.0)
VOTER_WEIGHTS = {
    "oracle": 0.30,
    "odin": 0.30,
    "garves": 0.25,
    "atlas": 0.15,
}

# Decision thresholds
SCORE_AUTO_TAKE = 82
SCORE_MANUAL_REVIEW = 65


# ── Data Model ──

@dataclass
class DiscordSignal:
    signal_id: str
    trader: str
    tier: TraderTier
    channel: str
    ticker: str
    direction: str
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    strategy: Optional[str] = None
    confidence: float = 0.5
    msg_type: str = "signal"
    # Filled after processing
    vote_result: Optional[dict] = None
    decision: str = "pending"
    decision_score: float = 0.0
    execution_id: Optional[str] = None
    blocked_reason: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tier"] = self.tier.value
        return d


# ── Voter Prompts ──

_VOTER_PROMPTS = {
    "oracle": (
        "You are Oracle, a crypto market analyst with a broad view of 60+ markets. "
        "Given a Discord trader signal, evaluate if the trade aligns with current "
        "market conditions. Consider market cycle, correlation, and macro trends."
    ),
    "odin": (
        "You are Odin, a regime-adaptive crypto futures trader using SMC analysis. "
        "Evaluate this Discord signal against current market structure, order blocks, "
        "and fair value gaps. Is the entry well-timed?"
    ),
    "garves": (
        "You are Garves, a crypto directional trader tracking on-chain flows and "
        "order book imbalances. Does this Discord signal align with your view on "
        "the asset's near-term direction?"
    ),
    "atlas": (
        "You are Atlas, a research engine tracking news sentiment, social trends, "
        "and macro events. Is there news or sentiment that supports or contradicts "
        "this trade signal?"
    ),
}


# ── Core Pipeline ──

class DiscordPipeline:
    """Routes Discord signals through tier-based processing to Odin execution."""

    def __init__(self, brotherhood, order_mgr, sizer, breaker, cfg):
        self._brotherhood = brotherhood
        self._order_mgr = order_mgr
        self._sizer = sizer
        self._breaker = breaker
        self._cfg = cfg

        self._processed_ids: set[str] = set()
        self._discord_positions: dict[str, dict] = {}
        self._stats = {
            "processed": 0,
            "auto_taken": 0,
            "approved": 0,
            "rejected": 0,
            "blocked": 0,
            "manual_review": 0,
            "learning_fed": 0,
        }
        self._recent_signals: list[dict] = []

        # Persist file for discord-sourced positions
        self._positions_file = cfg.data_dir / "discord_positions.json"
        self._approvals_file = cfg.data_dir / "discord_approvals.json"
        self._load_state()

        # TG bot (lazy init)
        self._tg = None

        log.info("[DISCORD] Pipeline initialized — %d registered traders",
                 len(TRADER_REGISTRY))

    # ── State Persistence ──

    def _load_state(self) -> None:
        if self._positions_file.exists():
            try:
                self._discord_positions = json.loads(
                    self._positions_file.read_text()
                )
                log.info("[DISCORD] Loaded %d discord positions from disk",
                         len(self._discord_positions))
            except Exception:
                pass

    def _save_positions(self) -> None:
        try:
            self._positions_file.parent.mkdir(parents=True, exist_ok=True)
            self._positions_file.write_text(
                json.dumps(self._discord_positions, indent=2, default=str)
            )
        except Exception as e:
            log.debug("[DISCORD] Save positions error: %s", str(e)[:100])

    def _get_tg(self):
        if self._tg is None:
            try:
                from shelby.core.telegram import TelegramBot
                self._tg = TelegramBot()
            except Exception as e:
                log.debug("[DISCORD] TG init error: %s", str(e)[:80])
        return self._tg

    # ── Main Entry Point ──

    def process_signal(self, event_data: dict) -> Optional[DiscordSignal]:
        """Process a discord_signal event. Returns DiscordSignal or None."""
        data = event_data.get("data", event_data)

        # Dedup by db_message_id
        msg_id = str(data.get("db_message_id", data.get("signal_id", "")))
        if not msg_id:
            return None
        if msg_id in self._processed_ids:
            log.debug("[DISCORD] Skipping duplicate: %s", msg_id)
            return None
        self._processed_ids.add(msg_id)

        # Skip result messages — only process trade signals
        if data.get("msg_type") == "result":
            log.debug("[DISCORD] Skipping result message: %s", msg_id)
            return None

        if not data.get("is_trade_signal") and not data.get("ticker"):
            log.debug("[DISCORD] Not a trade signal: %s", msg_id)
            return None

        # Classify tier
        author = (data.get("author") or "").lower()
        channel = (data.get("channel") or "").lower()
        tier = self._classify_tier(author, channel)

        if tier is None:
            log.debug("[DISCORD] Unknown trader '%s' in #%s — ignoring", author, channel)
            return None

        ticker = (data.get("ticker") or "").upper()
        direction = (data.get("direction") or "").upper()

        if not ticker or direction not in ("LONG", "SHORT"):
            log.debug("[DISCORD] Missing ticker/direction: %s %s", ticker, direction)
            return None

        signal = DiscordSignal(
            signal_id=msg_id,
            trader=author,
            tier=tier,
            channel=channel,
            ticker=ticker,
            direction=direction,
            entry_price=_safe_float(data.get("entry_price")),
            stop_loss=_safe_float(data.get("stop_loss")),
            take_profit=_safe_float(data.get("take_profit")),
            strategy=data.get("strategy"),
            confidence=_safe_float(data.get("confidence")) or 0.5,
            msg_type=data.get("msg_type", "signal"),
        )

        log.info("[DISCORD] Signal: %s %s %s from %s (#%s) tier=%s",
                 signal.ticker, signal.direction, signal.strategy or "",
                 signal.trader, signal.channel, signal.tier.value)

        # Route by tier
        if tier == TraderTier.TRUSTED:
            self._process_trusted(signal)
        elif tier == TraderTier.GOOD:
            self._process_good(signal)
        elif tier == TraderTier.EVALUATE:
            self._process_evaluate(signal)
        elif tier == TraderTier.LEARNING:
            self._feed_to_atlas_kb(signal)

        self._stats["processed"] += 1
        self._record_recent(signal)
        return signal

    def _classify_tier(self, author: str, channel: str) -> Optional[TraderTier]:
        """Classify trader tier from author name and channel."""
        # Channel overrides (regardless of author)
        if channel in EVALUATE_CHANNELS:
            return TraderTier.EVALUATE
        if channel in LEARNING_CHANNELS:
            return TraderTier.LEARNING

        # Check author registry
        if author in TRADER_REGISTRY:
            return TRADER_REGISTRY[author]

        # Unknown trader — ignore
        return None

    # ── Trusted Path (kiku, sn06) ──

    def _process_trusted(self, signal: DiscordSignal) -> None:
        """Auto-execute after security checks. No voting."""
        risk = TIER_RISK[TraderTier.TRUSTED]

        # Check 1: Max discord positions
        open_discord = len([
            p for p in self._discord_positions.values()
            if p.get("status") == "open"
        ])
        if open_discord >= risk["max_positions"]:
            signal.decision = "blocked"
            signal.blocked_reason = f"Max discord positions ({risk['max_positions']}) reached"
            self._stats["blocked"] += 1
            log.info("[DISCORD] BLOCKED %s: %s", signal.trader, signal.blocked_reason)
            self._tg_blocked(signal)
            return

        # Check 2: Circuit breaker
        cb = self._breaker.check()
        if not cb.trading_allowed:
            signal.decision = "blocked"
            signal.blocked_reason = f"Circuit breaker: {cb.reason}"
            self._stats["blocked"] += 1
            log.info("[DISCORD] BLOCKED %s: %s", signal.trader, signal.blocked_reason)
            self._tg_blocked(signal)
            return

        # Check 3: Block only if there's already a DISCORD position in same ticker
        open_same_ticker = [
            p for p in self._discord_positions.values()
            if p.get("status") == "open" and p.get("ticker") == signal.ticker
        ]
        if open_same_ticker:
            signal.decision = "blocked"
            signal.blocked_reason = f"Already have Discord position in {signal.ticker}"
            self._stats["blocked"] += 1
            log.info("[DISCORD] BLOCKED %s: %s", signal.trader, signal.blocked_reason)
            self._tg_blocked(signal)
            return

        # All checks passed — auto-execute
        signal.decision = "auto_take"
        signal.decision_score = 100.0
        self._stats["auto_taken"] += 1

        pos_id = self._execute_signal(signal, risk["max_risk_usd"])
        if pos_id:
            signal.execution_id = pos_id
            log.info("[DISCORD-EXEC] Auto-take %s %s from %s → %s",
                     signal.direction, signal.ticker, signal.trader, pos_id)
            self._tg_auto_take(signal)
        else:
            signal.decision = "blocked"
            signal.blocked_reason = "Execution failed"
            self._stats["blocked"] += 1
            self._tg_blocked(signal)

    # ── Good Path (abns92, miku) ──

    def _process_good(self, signal: DiscordSignal) -> None:
        """Full 4-agent voting → decision engine → TG alert."""
        risk = TIER_RISK[TraderTier.GOOD]

        # Pre-checks
        open_discord = len([
            p for p in self._discord_positions.values()
            if p.get("status") == "open"
        ])
        if open_discord >= risk["max_positions"]:
            signal.decision = "blocked"
            signal.blocked_reason = f"Max discord positions ({risk['max_positions']}) reached"
            self._stats["blocked"] += 1
            log.info("[DISCORD] BLOCKED %s: %s", signal.trader, signal.blocked_reason)
            self._tg_blocked(signal)
            return

        symbol = f"{signal.ticker}USDT"
        if self._order_mgr.has_position_for_symbol(symbol):
            signal.decision = "blocked"
            signal.blocked_reason = f"Already in {symbol} position"
            self._stats["blocked"] += 1
            return

        # 4-agent voting
        votes = self._run_voting(signal)
        signal.vote_result = votes

        # Weighted score
        weighted_score = self._calculate_weighted_score(votes)
        signal.decision_score = weighted_score

        log.info("[DISCORD] Voting for %s %s from %s: score=%.1f votes=%s",
                 signal.ticker, signal.direction, signal.trader,
                 weighted_score, {k: v.get("vote") for k, v in votes.items()})

        # Decision thresholds
        if weighted_score >= SCORE_AUTO_TAKE:
            signal.decision = "approved"
            self._stats["approved"] += 1
            pos_id = self._execute_signal(signal, risk["max_risk_usd"])
            if pos_id:
                signal.execution_id = pos_id
                log.info("[DISCORD-EXEC] Approved %s %s from %s (%.1f) → %s",
                         signal.direction, signal.ticker, signal.trader,
                         weighted_score, pos_id)
                self._tg_auto_take(signal)
            else:
                signal.decision = "blocked"
                signal.blocked_reason = "Execution failed after approval"
                self._stats["blocked"] += 1
        elif weighted_score >= SCORE_MANUAL_REVIEW:
            signal.decision = "manual_review"
            self._stats["manual_review"] += 1
            log.info("[DISCORD] Manual review: %s %s from %s (%.1f)",
                     signal.ticker, signal.direction, signal.trader, weighted_score)
            self._tg_manual_review(signal)
        else:
            signal.decision = "rejected"
            self._stats["rejected"] += 1
            log.info("[DISCORD] Rejected: %s %s from %s (%.1f < %d)",
                     signal.ticker, signal.direction, signal.trader,
                     weighted_score, SCORE_MANUAL_REVIEW)

    # ── Evaluate Path (charts-ideas) ──

    def _process_evaluate(self, signal: DiscordSignal) -> None:
        """Score via 4-agent voting but NO execution. Jordan approves via dashboard/TG."""
        # Run voting to get a score
        votes = self._run_voting(signal)
        signal.vote_result = votes
        weighted_score = self._calculate_weighted_score(votes)
        signal.decision_score = weighted_score

        # Classify recommendation
        if weighted_score >= 75:
            signal.decision = "recommended"
        elif weighted_score >= 50:
            signal.decision = "interesting"
        else:
            signal.decision = "pass"

        log.info("[DISCORD] EVALUATE %s %s from %s: score=%.1f → %s",
                 signal.ticker, signal.direction, signal.trader,
                 weighted_score, signal.decision)

        # Always feed to Atlas KB
        self._feed_to_atlas_kb(signal)

        # Store in chart_ideas list for API
        if not hasattr(self, "_chart_ideas"):
            self._chart_ideas = []
        self._chart_ideas.append(signal.to_dict())
        if len(self._chart_ideas) > 50:
            self._chart_ideas = self._chart_ideas[-50:]

        # TG alert for recommended ideas
        if signal.decision == "recommended":
            self._tg_chart_idea(signal)

    def _tg_chart_idea(self, signal: DiscordSignal) -> None:
        """TG alert for high-scoring chart ideas."""
        tg = self._get_tg()
        if not tg:
            return
        try:
            _dir_icon = "\U0001f7e2" if signal.direction == "LONG" else "\U0001f534"
            msg = (
                f"\U0001f4ca *ODIN — CHART IDEA (Recommended)*\n"
                f"\n"
                f"{_dir_icon} *{signal.direction} {signal.ticker}*\n"
                f"\U0001f464 {signal.trader} | #{signal.channel}\n"
                f"\U0001f9e0 Score: *{signal.decision_score:.0f}*/100\n"
            )
            if signal.strategy:
                msg += f"\U0001f4dd _{signal.strategy}_\n"
            msg += f"\n\U0001f449 Approve via dashboard to execute"
            tg.send(msg, parse_mode="Markdown")
        except Exception as e:
            log.debug("[DISCORD] TG chart idea error: %s", str(e)[:80])

    def _run_voting(self, signal: DiscordSignal) -> dict[str, dict]:
        """Run 4-agent LLM voting. Returns {agent: {vote, confidence, reason}}."""
        signal_ctx = (
            f"Discord trader '{signal.trader}' (tier: {signal.tier.value}) "
            f"calls {signal.direction} {signal.ticker}"
        )
        if signal.entry_price:
            signal_ctx += f" @ ${signal.entry_price:,.2f}"
        if signal.stop_loss:
            signal_ctx += f" SL ${signal.stop_loss:,.2f}"
        if signal.take_profit:
            signal_ctx += f" TP ${signal.take_profit:,.2f}"
        if signal.strategy:
            signal_ctx += f" | Strategy: {signal.strategy}"

        user_prompt = (
            f"{signal_ctx}\n\n"
            "Should we take this trade? Reply in EXACTLY this JSON format:\n"
            '{"vote": "YES" or "NO", "confidence": 0-100, "reason": "brief reason"}'
        )

        votes: dict[str, dict] = {}
        for agent_name, system_prompt in _VOTER_PROMPTS.items():
            try:
                raw = llm_call(
                    system=system_prompt,
                    user=user_prompt,
                    agent=agent_name,
                    task_type="fast",
                    max_tokens=150,
                    temperature=0.3,
                )
                vote = self._parse_vote(raw, agent_name)
                votes[agent_name] = vote
            except Exception as e:
                log.debug("[DISCORD] Vote error from %s: %s", agent_name, str(e)[:80])
                votes[agent_name] = {"vote": "ABSTAIN", "confidence": 0, "reason": "error"}

        return votes

    def _parse_vote(self, raw: str, agent: str) -> dict:
        """Parse LLM vote response into structured dict."""
        try:
            # Try JSON parse first
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                parsed = json.loads(raw[start:end])
                vote = parsed.get("vote", "").upper()
                if vote not in ("YES", "NO"):
                    vote = "ABSTAIN"
                conf = max(0, min(100, int(parsed.get("confidence", 50))))
                return {
                    "vote": vote,
                    "confidence": conf,
                    "reason": str(parsed.get("reason", ""))[:200],
                }
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: keyword detection
        upper = raw.upper()
        if "YES" in upper:
            return {"vote": "YES", "confidence": 50, "reason": "parsed from text"}
        elif "NO" in upper:
            return {"vote": "NO", "confidence": 50, "reason": "parsed from text"}
        return {"vote": "ABSTAIN", "confidence": 0, "reason": "unparseable"}

    def _calculate_weighted_score(self, votes: dict[str, dict]) -> float:
        """Calculate weighted score from votes. Returns 0-100."""
        total_weight = 0.0
        weighted_sum = 0.0

        for agent, vote_data in votes.items():
            weight = VOTER_WEIGHTS.get(agent, 0.0)
            vote = vote_data.get("vote", "ABSTAIN")
            conf = vote_data.get("confidence", 0)

            if vote == "YES":
                weighted_sum += conf * weight
                total_weight += weight
            elif vote == "NO":
                # NO votes subtract from score
                weighted_sum -= conf * weight * 0.5
                total_weight += weight
            # ABSTAIN: skip

        if total_weight <= 0:
            return 50.0  # No data — neutral

        score = weighted_sum / total_weight
        return max(0.0, min(100.0, score))

    # ── Execution Bridge ──

    def _execute_signal(self, signal: DiscordSignal, max_risk_usd: float) -> Optional[str]:
        """Convert DiscordSignal → TradeSignal → execute via OrderManager."""
        from odin.strategy.signals import TradeSignal

        symbol = f"{signal.ticker}USDT"
        current_price = signal.entry_price

        # If no entry price, try to fetch current price
        if not current_price or current_price <= 0:
            try:
                from odin.exchange.hyperliquid_client import HyperliquidClient
                client = HyperliquidClient(self._cfg)
                current_price = client.get_price(symbol)
            except Exception:
                log.warning("[DISCORD] Cannot get price for %s — skipping", symbol)
                return None

        if not current_price or current_price <= 0:
            return None

        # Calculate SL — TRUSTED uses trader's SL as-is, others fall back to 2%
        sl = signal.stop_loss
        if not sl or sl <= 0:
            sl_pct = 0.02
            if signal.direction == "LONG":
                sl = round(current_price * (1 - sl_pct), 2)
            else:
                sl = round(current_price * (1 + sl_pct), 2)

        # Calculate TP — TRUSTED uses trader's TP as-is, others fall back to 2R
        tp = signal.take_profit
        sl_dist = abs(current_price - sl)
        if not tp or tp <= 0:
            if signal.direction == "LONG":
                tp = round(current_price + sl_dist * 2.0, 2)
            else:
                tp = round(current_price - sl_dist * 2.0, 2)

        rr = sl_dist / max(abs(current_price - tp), 0.01) if sl_dist > 0 else 2.0
        rr = abs(current_price - tp) / sl_dist if sl_dist > 0 else 2.0

        # Build TradeSignal
        trade_signal = TradeSignal(
            symbol=symbol,
            direction=signal.direction,
            confidence=signal.confidence,
            entry_price=current_price,
            entry_zone_top=current_price,
            entry_zone_bottom=current_price,
            stop_loss=sl,
            take_profit_1=tp,
            take_profit_2=tp,
            risk_reward=rr,
            macro_multiplier=1.0,
            macro_regime=self._cfg.dry_run and "paper" or "live",
            macro_score=70,
            atr=sl_dist,
            entry_reason=f"discord_{signal.trader}_{signal.channel}",
            reasons=[
                f"Discord {signal.tier.value}: {signal.trader}",
                f"Score: {signal.decision_score:.0f}",
            ],
        )

        # Force tradeable by setting conviction
        trade_signal.conviction_score = max(signal.decision_score, 80)
        trade_signal.risk_multiplier = 1.0

        # Position sizing — cap risk to tier limits
        balance = self._breaker.state.current_balance
        risk_usd = min(max_risk_usd, balance * 0.15)

        size = self._sizer.calculate(
            balance=balance,
            entry_price=current_price,
            stop_loss=sl,
            confidence=1.0,
            macro_multiplier=1.0,
            current_exposure=self._order_mgr.get_total_exposure(),
            conviction_score=trade_signal.conviction_score,
            direction=signal.direction,
        )

        # Cap risk to tier limit
        if size.risk_usd > max_risk_usd:
            ratio = max_risk_usd / size.risk_usd
            size = self._sizer.calculate(
                balance=balance,
                entry_price=current_price,
                stop_loss=sl,
                confidence=ratio,
                macro_multiplier=1.0,
                current_exposure=self._order_mgr.get_total_exposure(),
                conviction_score=trade_signal.conviction_score,
                direction=signal.direction,
            )

        if size.notional_usd < 5:
            log.info("[DISCORD] Position too small ($%.2f) for %s", size.notional_usd, symbol)
            return None

        # Execute
        pos_id = self._order_mgr.execute_signal(trade_signal, size)
        if pos_id:
            # Track as discord-sourced position
            self._discord_positions[pos_id] = {
                "status": "open",
                "signal_id": signal.signal_id,
                "trader": signal.trader,
                "tier": signal.tier.value,
                "ticker": signal.ticker,
                "direction": signal.direction,
                "entry_price": current_price,
                "stop_loss": sl,
                "take_profit": tp,
                "risk_usd": round(size.risk_usd, 2),
                "notional_usd": round(size.notional_usd, 2),
                "decision": signal.decision,
                "decision_score": signal.decision_score,
                "opened_at": time.time(),
            }
            self._save_positions()

            # Publish to brotherhood
            self._brotherhood.publish_trade_open({
                "symbol": symbol,
                "direction": signal.direction,
                "entry_price": current_price,
                "conviction_score": trade_signal.conviction_score,
                "source": f"discord_{signal.trader}",
            })

        return pos_id

    # ── Learning Path ──

    def _feed_to_atlas_kb(self, signal: DiscordSignal) -> None:
        """Feed charts-ideas/education to Atlas KB. Zero execution."""
        try:
            from shared.agent_memory import AgentMemory
            mem = AgentMemory("atlas")
            mem.set_knowledge(
                category="discord_charts",
                key=f"discord_{signal.trader}_{signal.ticker}_{int(time.time())}",
                value=(
                    f"{signal.trader} shared {signal.ticker} {signal.direction} "
                    f"idea in #{signal.channel}: {signal.strategy or 'chart analysis'}"
                ),
                source=f"discord_#{signal.channel}",
                ttl_hours=168,  # 7 days
            )
            self._stats["learning_fed"] += 1
            log.info("[DISCORD] Fed %s chart from %s to Atlas KB",
                     signal.ticker, signal.trader)
        except Exception as e:
            log.debug("[DISCORD] Atlas KB feed error: %s", str(e)[:100])

    # ── Closed-Loop Learning ──

    def record_discord_outcome(self, trade_id: str, pnl_usd: float, is_win: bool) -> None:
        """Called when a discord-sourced position closes."""
        if trade_id not in self._discord_positions:
            return

        pos = self._discord_positions[trade_id]
        pos["status"] = "closed"
        pos["pnl_usd"] = round(pnl_usd, 2)
        pos["is_win"] = is_win
        pos["closed_at"] = time.time()
        self._save_positions()

        trader = pos.get("trader", "")
        ticker = pos.get("ticker", "")
        tier = pos.get("tier", "")

        log.info("[DISCORD] Outcome: %s %s from %s → %s ($%.2f)",
                 pos.get("direction", ""), ticker, trader,
                 "WIN" if is_win else "LOSS", pnl_usd)

        # Update trader_scores in discord_intel.db
        try:
            import sqlite3
            db_path = Path.home() / "polymarket-bot" / "data" / "discord_intel.db"
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                conn.execute("""
                    UPDATE trader_scores
                    SET outcome = ?, pnl_pct = ?, resolved_at = datetime('now')
                    WHERE author = ? AND ticker = ? AND outcome = 'pending'
                    ORDER BY created_at DESC LIMIT 1
                """, (
                    "win" if is_win else "loss",
                    round(pnl_usd / max(pos.get("notional_usd", 1), 1) * 100, 2),
                    trader, ticker,
                ))
                conn.commit()
                conn.close()
        except Exception as e:
            log.debug("[DISCORD] DB update error: %s", str(e)[:100])

        # Feed outcome to Odin's memory for future calibration
        try:
            from shared.agent_memory import AgentMemory
            mem = AgentMemory("odin")
            result_label = "won" if is_win else "lost"
            mem.set_knowledge(
                category="discord_outcomes",
                key=f"discord_{trader}_{ticker}_{result_label}",
                value=(
                    f"Discord {tier} trader {trader} {result_label} "
                    f"{pos.get('direction', '')} {ticker} (${pnl_usd:+.2f})"
                ),
                source="discord_pipeline",
                ttl_hours=72,
            )
        except Exception:
            pass

    # ── Exit Signal Processing ──

    def process_exit_signal(self, event_data: dict) -> bool:
        """Process a discord_exit_signal event. Returns True if a position was closed."""
        data = event_data.get("data", event_data)
        channel = (data.get("channel") or "").lower()
        author = (data.get("author") or "").lower()
        ticker = (data.get("ticker") or "").upper()

        if not ticker:
            log.debug("[DISCORD-EXIT] No ticker in exit signal from %s", author)
            return False

        # Find matching open discord position by ticker + source channel/trader
        closed = False
        for pos_id, pos in list(self._discord_positions.items()):
            if pos.get("status") != "open":
                continue
            if pos.get("ticker", "").upper() != ticker:
                continue
            # Match: same ticker from same channel or trader
            pos_channel = (pos.get("channel") or "").lower()
            pos_trader = (pos.get("trader") or "").lower()
            if author == pos_trader or channel == pos_channel:
                log.info("[DISCORD-EXIT] Closing %s %s (from %s) — exit signal from %s",
                         pos.get("direction"), ticker, pos_trader, author)
                # Close via order manager
                symbol = f"{ticker}USDT"
                try:
                    self._order_mgr.close_position(symbol, reason="discord_exit_signal")
                    pos["status"] = "closed_discord_exit"
                    pos["closed_at"] = time.time()
                    pos["exit_reason"] = f"Exit signal from {author} in #{channel}"
                    self._save_positions()
                    closed = True

                    # TG alert
                    tg = self._get_tg()
                    if tg:
                        tg.send(
                            f"\U0001f6a8 *DISCORD EXIT*\n\n"
                            f"Closed *{pos.get('direction', '')} {ticker}*\n"
                            f"\U0001f464 Exit from: {author} in #{channel}\n"
                            f"\U0001f4dd _{data.get('raw_content', '')[:100]}_",
                            parse_mode="Markdown",
                        )
                except Exception as e:
                    log.warning("[DISCORD-EXIT] Close error for %s: %s", symbol, str(e)[:100])

        if not closed:
            log.debug("[DISCORD-EXIT] No matching position for %s exit from %s", ticker, author)

        return closed

    # ── Manual Approval Polling ──

    def check_approvals(self) -> None:
        """Check for manual approvals written by dashboard API."""
        if not self._approvals_file.exists():
            return
        try:
            approvals = json.loads(self._approvals_file.read_text())
            for signal_id, action in list(approvals.items()):
                if action.get("processed"):
                    continue
                if action.get("decision") == "approve":
                    self._handle_approval(signal_id)
                approvals[signal_id]["processed"] = True
            self._approvals_file.write_text(json.dumps(approvals, indent=2))
        except Exception as e:
            log.debug("[DISCORD] Approvals check error: %s", str(e)[:100])

    def _handle_approval(self, signal_id: str) -> None:
        """Execute a manually approved signal."""
        # Find signal in recent_signals
        for sig_data in self._recent_signals:
            if sig_data.get("signal_id") == signal_id:
                log.info("[DISCORD] Manual approval for signal %s", signal_id)
                # Re-create signal and execute
                signal = DiscordSignal(
                    signal_id=signal_id,
                    trader=sig_data.get("trader", ""),
                    tier=TraderTier(sig_data.get("tier", "good")),
                    channel=sig_data.get("channel", ""),
                    ticker=sig_data.get("ticker", ""),
                    direction=sig_data.get("direction", ""),
                    entry_price=sig_data.get("entry_price"),
                    stop_loss=sig_data.get("stop_loss"),
                    take_profit=sig_data.get("take_profit"),
                    strategy=sig_data.get("strategy"),
                    confidence=sig_data.get("confidence", 0.5),
                    decision="approved",
                    decision_score=sig_data.get("decision_score", 75),
                )
                risk = TIER_RISK.get(signal.tier, TIER_RISK[TraderTier.GOOD])
                pos_id = self._execute_signal(signal, risk["max_risk_usd"])
                if pos_id:
                    self._stats["approved"] += 1
                    self._tg_auto_take(signal)
                return
        log.warning("[DISCORD] Approval signal %s not found in recent", signal_id)

    # ── Telegram Alerts ──

    def _tg_auto_take(self, signal: DiscordSignal) -> None:
        tg = self._get_tg()
        if not tg:
            return
        try:
            tier_tag = "\U0001f31f TRUSTED" if signal.tier == TraderTier.TRUSTED else "\U0001f4ca VOTED"
            pos = self._discord_positions.get(signal.execution_id, {})
            _dir_icon = "\U0001f7e2" if signal.direction == "LONG" else "\U0001f534"
            _score_bar = "\u2588" * min(int(signal.decision_score / 10), 10) + "\u2591" * max(0, 10 - int(signal.decision_score / 10))
            msg = (
                f"\u26a1 *ODIN DISCORD — AUTO-TAKE*\n"
                f"\n"
                f"{_dir_icon} *{signal.direction} {signal.ticker}*\n"
                f"\U0001f464 {signal.trader} ({tier_tag}) | #{signal.channel}\n"
                f"\n"
            )
            if signal.entry_price:
                msg += f"\U0001f4b0 Entry: `${signal.entry_price:,.2f}`"
                if signal.stop_loss:
                    _risk_pct = abs(signal.entry_price - signal.stop_loss) / signal.entry_price * 100
                    msg += f" | SL: `${signal.stop_loss:,.2f}` (-{_risk_pct:.1f}%)"
                msg += "\n"
            if signal.take_profit:
                msg += f"\U0001f3af TP: `${signal.take_profit:,.2f}`\n"
            msg += f"\U0001f9e0 Score: *{signal.decision_score:.0f}*/100\n`{_score_bar}`\n"
            if pos:
                msg += f"\U0001f4b5 Risk: ${pos.get('risk_usd', 0):.0f} | Size: ${pos.get('notional_usd', 0):,.0f}\n"
            tg.send(msg, parse_mode="Markdown")
        except Exception as e:
            log.debug("[DISCORD] TG auto-take error: %s", str(e)[:80])

    def _tg_manual_review(self, signal: DiscordSignal) -> None:
        tg = self._get_tg()
        if not tg:
            return
        try:
            _dir_icon = "\U0001f7e2" if signal.direction == "LONG" else "\U0001f534"
            votes_str = ""
            if signal.vote_result:
                for agent, v in signal.vote_result.items():
                    emoji = "\u2705" if v["vote"] == "YES" else "\u274c" if v["vote"] == "NO" else "\u2753"
                    votes_str += f"  {emoji} {agent}: {v['confidence']}% — {v.get('reason', '')[:40]}\n"

            msg = (
                f"\U0001f514 *ODIN DISCORD — REVIEW NEEDED*\n"
                f"\n"
                f"{_dir_icon} *{signal.direction} {signal.ticker}*\n"
                f"\U0001f464 {signal.trader} | #{signal.channel}\n"
                f"\n"
            )
            if signal.entry_price:
                msg += f"\U0001f4b0 Entry: `${signal.entry_price:,.2f}`\n"
            if signal.stop_loss:
                msg += f"\U0001f6e1 SL: `${signal.stop_loss:,.2f}`\n"
            msg += f"\U0001f9e0 Score: *{signal.decision_score:.0f}*/100 (need {SCORE_AUTO_TAKE}+ for auto)\n"
            if votes_str:
                msg += f"\n*Agent Votes:*\n```\n{votes_str}```\n"
            msg += f"\n\U0001f449 Approve: `localhost:8877` > Discord tab"
            tg.send(msg, parse_mode="Markdown")
        except Exception as e:
            log.debug("[DISCORD] TG review error: %s", str(e)[:80])

    def _tg_blocked(self, signal: DiscordSignal) -> None:
        tg = self._get_tg()
        if not tg:
            return
        try:
            msg = (
                f"\U0001f6ab *ODIN DISCORD — BLOCKED*\n"
                f"\n"
                f"{signal.direction} {signal.ticker}\n"
                f"\U0001f464 {signal.trader} ({signal.tier.value})\n"
                f"\U0001f4dd _{signal.blocked_reason}_"
            )
            tg.send(msg, parse_mode="Markdown")
        except Exception as e:
            log.debug("[DISCORD] TG blocked error: %s", str(e)[:80])

    # ── Status ──

    def _record_recent(self, signal: DiscordSignal) -> None:
        self._recent_signals.append(signal.to_dict())
        if len(self._recent_signals) > 20:
            self._recent_signals = self._recent_signals[-20:]

    def get_chart_ideas(self) -> list[dict]:
        """Return chart ideas with evaluation scores."""
        return getattr(self, "_chart_ideas", [])

    def get_status(self) -> dict:
        open_positions = [
            p for p in self._discord_positions.values()
            if p.get("status") == "open"
        ]
        return {
            "attached": True,
            "stats": self._stats.copy(),
            "open_positions": len(open_positions),
            "open_position_details": open_positions,
            "recent_signals": self._recent_signals[-5:],
            "registered_traders": {
                k: v.value for k, v in TRADER_REGISTRY.items()
            },
            "chart_ideas": self.get_chart_ideas()[-10:],
        }


# ── Helpers ──

def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(val)
        return f if f > 0 else None
    except (ValueError, TypeError):
        return None
