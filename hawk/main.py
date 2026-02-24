"""Hawk V8 Main Bot Loop — The Smart Degen."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from hawk.config import HawkConfig
from hawk.scanner import scan_all_markets
from hawk.analyst import batch_analyze
from hawk.edge import calculate_edge, calculate_confidence_tier, rank_opportunities, urgency_label
from hawk.executor import HawkExecutor
from hawk.tracker import HawkTracker
from hawk.risk import HawkRiskManager
from hawk.resolver import resolve_paper_trades
from hawk.learner import record_trade_outcome, get_dimension_adjustments

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
STATUS_FILE = DATA_DIR / "hawk_status.json"
OPPS_FILE = DATA_DIR / "hawk_opportunities.json"
SUGGESTIONS_FILE = DATA_DIR / "hawk_suggestions.json"
MODE_FILE = DATA_DIR / "hawk_mode.json"

from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York")

BRAIN_FILE = DATA_DIR / "brains" / "hawk.json"


def _load_brain_notes() -> list[dict]:
    """Load brain notes for Hawk."""
    if BRAIN_FILE.exists():
        try:
            data = json.loads(BRAIN_FILE.read_text())
            return data.get("notes", [])
        except Exception:
            pass
    return []


def _save_status(tracker: HawkTracker, risk: HawkRiskManager | None = None,
                 running: bool = True, cycle: int = 0,
                 scan_stats: dict | None = None,
                 regime: dict | None = None) -> None:
    """Save current status to data/hawk_status.json for dashboard."""
    DATA_DIR.mkdir(exist_ok=True)
    summary = tracker.summary()
    summary["running"] = running
    summary["cycle"] = cycle
    summary["last_update"] = datetime.now(ET).isoformat()
    if risk:
        summary["effective_bankroll"] = round(risk.effective_bankroll(), 2)
        summary["consecutive_losses"] = risk.consecutive_losses
    if scan_stats:
        summary["scan"] = scan_stats
    if regime:
        summary["regime"] = regime
    try:
        STATUS_FILE.write_text(json.dumps(summary, indent=2))
    except Exception:
        log.exception("Failed to save Hawk status")


def _save_opportunities(opps: list[dict]) -> None:
    """Save latest opportunities for dashboard."""
    try:
        OPPS_FILE.write_text(json.dumps({"opportunities": opps, "updated": time.time()}, indent=2))
    except Exception:
        log.exception("Failed to save Hawk opportunities")


NEXT_CYCLE_FILE = DATA_DIR / "hawk_next_cycle.json"


def _save_next_cycle(minutes: int) -> None:
    """V6: Write next cycle info for dashboard countdown."""
    try:
        DATA_DIR.mkdir(exist_ok=True)
        NEXT_CYCLE_FILE.write_text(json.dumps({
            "cycle_minutes": minutes,
            "next_at": time.time() + minutes * 60,
            "mode": "fast" if minutes <= 10 else "normal",
        }, indent=2))
    except Exception:
        pass


def _save_suggestions(suggestions: list[dict]) -> None:
    """Save trade suggestions for dashboard review."""
    try:
        DATA_DIR.mkdir(exist_ok=True)
        SUGGESTIONS_FILE.write_text(json.dumps({
            "suggestions": suggestions,
            "updated": time.time(),
        }, indent=2))
    except Exception:
        log.exception("Failed to save Hawk suggestions")


# ── Multi-Source Intel Loading ──

def _load_viper_context() -> dict:
    """Load Viper market context intel (pre-matched)."""
    ctx_file = DATA_DIR / "viper_market_context.json"
    if ctx_file.exists():
        try:
            ctx = json.loads(ctx_file.read_text())
            if ctx:
                return ctx
        except Exception:
            pass
    log.info("Viper market context empty — using raw intel fallback")
    return {}


def _match_raw_intel(markets) -> dict:
    """Fallback: load raw Viper intel and keyword-match to markets."""
    import re
    intel_file = DATA_DIR / "viper_intel.json"
    if not intel_file.exists():
        return {}
    try:
        data = json.loads(intel_file.read_text())
        raw_items = data.get("items", [])
    except Exception:
        return {}

    if not raw_items:
        return {}

    now = time.time()
    context = {}
    for market in markets:
        cid = market.condition_id
        q_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', market.question.lower()))
        stop = {"will", "what", "when", "this", "that", "have", "from", "with", "been", "more", "than"}
        q_words -= stop

        matched = []
        for item in raw_items:
            if now - item.get("timestamp", 0) > 86400:
                continue
            text = (item.get("headline", "") + " " + item.get("summary", "")).lower()
            overlap = sum(1 for w in q_words if w in text)
            if overlap >= 2:
                matched.append(item)

        if matched:
            context[cid] = matched[:5]

    if context:
        log.info("Raw intel fallback: matched %d markets", len(context))
    return context


def _load_atlas_intel() -> dict:
    """Load Atlas intel for Hawk markets."""
    atlas_file = DATA_DIR / "hawk_atlas_intel.json"
    if not atlas_file.exists():
        return {}
    try:
        data = json.loads(atlas_file.read_text())
        return data.get("market_intel", {})
    except Exception:
        return {}


def _load_all_intel(markets) -> dict:
    """Multi-source intel loader with fallback."""
    context = _load_viper_context()
    if not context:
        context = _match_raw_intel(markets)
    atlas_intel = _load_atlas_intel()
    # Merge atlas into context
    for cid, items in atlas_intel.items():
        if cid in context:
            context[cid].extend(items)
        else:
            context[cid] = items
    return context


# ── Urgency-Weighted Market Ranking ──

def _urgency_rank(markets) -> list:
    """Rank markets by sweet-spot timing + value factors."""
    scored = []
    for m in markets:
        score = 0

        # Sweet spot: 6-48h = enough time for edge, close enough for conviction
        if 6 <= m.time_left_hours <= 24:
            score += 40
        elif 24 < m.time_left_hours <= 48:
            score += 30
        elif 2 <= m.time_left_hours < 6:
            score += 15  # Acceptable but not ideal
        elif m.time_left_hours > 48:
            score += 5

        # Volume sweet spot (not too big, not too small)
        if 5000 <= m.volume <= 50000:
            score += 15
        elif m.volume > 50000:
            score += 5

        # Contestedness (closer to 50/50 = more edge potential)
        yes_price = _get_yes_price(m)
        if abs(yes_price - 0.5) < 0.15:
            score += 10
        elif abs(yes_price - 0.5) < 0.25:
            score += 5

        scored.append((score, m))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored]


def _get_domain_tag(category: str, question: str) -> str:
    """Map a market to a granular domain tag for pattern learning."""
    q = question.lower()
    if category == "weather":
        if "highest temperature" in q or "high temperature" in q:
            return "weather_exact_high"
        if "lowest temperature" in q or "low temperature" in q:
            return "weather_exact_low"
        if "rain" in q or "precipitation" in q:
            return "weather_precipitation"
        if "snow" in q or "frost" in q:
            return "weather_snow"
        if "wind" in q:
            return "weather_wind"
        return "weather_other"
    if category == "sports":
        if "o/u" in q or "over/under" in q or "total" in q:
            return "sports_over_under"
        if "win" in q or " vs " in q or " vs." in q:
            return "sports_team_win"
        if "goal" in q or "score" in q or "point" in q:
            return "sports_player_prop"
        if "spread" in q:
            return "sports_spread"
        return "sports_other"
    if category == "politics":
        if "approval" in q:
            return "politics_approval"
        if "election" in q or "vote" in q:
            return "politics_election"
        return "politics_other"
    if category == "crypto_event":
        if "between" in q or "price" in q:
            return "crypto_price_range"
        if "above" in q or "below" in q:
            return "crypto_price_threshold"
        return "crypto_other"
    return f"{category}_other"


class HawkBot:
    """The Poker Shark V2 — The Smart Degen."""

    def __init__(self):
        self.cfg = HawkConfig()
        self.tracker = HawkTracker()
        self.risk = HawkRiskManager(self.cfg, self.tracker)
        self.executor: HawkExecutor | None = None
        self.cycle = 0

        # Agent Brain — learning memory
        self._brain = None
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path.home() / "shared"))
            _sys.path.insert(0, str(Path.home()))
            from agent_brain import AgentBrain
            self._brain = AgentBrain("hawk", system_prompt="You are Hawk, a prediction market analyst.", task_type="analysis")
        except Exception:
            pass

    def _check_mode_toggle(self) -> None:
        """Check if mode was toggled via dashboard and update cfg accordingly."""
        if not MODE_FILE.exists():
            return
        try:
            mode_data = json.loads(MODE_FILE.read_text())
            new_dry_run = mode_data.get("dry_run", self.cfg.dry_run)
            if new_dry_run != self.cfg.dry_run:
                old_mode = "DRY RUN" if self.cfg.dry_run else "LIVE"
                new_mode = "DRY RUN" if new_dry_run else "LIVE"
                object.__setattr__(self.cfg, "dry_run", new_dry_run)
                log.info("Mode toggled: %s -> %s", old_mode, new_mode)
                if not new_dry_run:
                    self._init_executor()
        except Exception:
            log.exception("Failed to read mode toggle file")

    def _init_executor(self) -> None:
        """Initialize CLOB client and executor."""
        client = None
        if not self.cfg.dry_run:
            try:
                from bot.auth import build_client
                from bot.config import Config
                garves_cfg = Config()
                client = build_client(garves_cfg)
            except Exception:
                log.warning("Could not initialize CLOB client, running in dry-run mode")
        self.executor = HawkExecutor(self.cfg, client, self.tracker)

    def _check_atlas_alignment(self, opp) -> tuple[float, str]:
        """V6: Atlas pre-bet gate. Returns (size_multiplier, reason).

        1.0 = aligned/neutral (proceed), 0.5 = opposes (reduce), 0.0 = strong opposition (block).
        """
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path.home() / "shared"))
            from events import get_events
            events = get_events(agent="atlas", limit=20)
            if events:
                q_lower = opp.market.question.lower()
                q_words = set(q_lower.split())
                for evt in events:
                    summary = (evt.get("summary", "") or "").lower()
                    data = evt.get("data", {}) or {}
                    severity = data.get("severity", "info")
                    # Keyword overlap check
                    overlap = sum(1 for w in q_words if len(w) > 4 and w in summary)
                    if overlap < 2:
                        continue
                    # Atlas has relevant intelligence about this market
                    if severity == "critical":
                        log.info("[ATLAS-GATE] Critical Atlas intel opposes trade")
                        return 0.0, f"Atlas critical: {summary[:100]}"
                    elif severity in ("warning", "high"):
                        return 0.5, f"Atlas warning: {summary[:100]}"
        except Exception:
            log.debug("[ATLAS-GATE] Event bus check failed (non-fatal)")

        # Check Atlas news_sentiment.json for macro sentiment
        try:
            sentiment_file = DATA_DIR / "atlas_news_sentiment.json"
            if sentiment_file.exists():
                sent_data = json.loads(sentiment_file.read_text())
                macro_sent = sent_data.get("macro_sentiment", 0)
                # If macro strongly opposes trade direction → reduce
                direction = opp.direction.lower()
                if (direction == "yes" and macro_sent < -0.6) or (direction == "no" and macro_sent > 0.6):
                    return 0.5, f"Atlas macro sentiment opposes ({macro_sent:.2f})"
        except Exception:
            pass

        return 1.0, "aligned"

    def _calculate_next_cycle(self, markets) -> int:
        """V6: Dynamic cycle timing. Returns minutes until next cycle."""
        # Check for live games
        try:
            from hawk.espn import get_live_games
            live = get_live_games()
            if live:
                log.info("[CYCLE] %d live games detected — fast mode", len(live))
                return self.cfg.cycle_minutes_fast
        except Exception:
            pass

        # Check for same-day weather events
        try:
            from hawk.noaa import is_same_day_weather_event
            for m in markets:
                if m.category == "weather" and is_same_day_weather_event(m.question):
                    log.info("[CYCLE] Same-day weather event detected — fast mode")
                    return self.cfg.cycle_minutes_fast
        except Exception:
            pass

        return self.cfg.cycle_minutes_normal

    async def run(self) -> None:
        """Main loop — V6 Smart Degen."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s [HAWK] %(message)s",
            datefmt="%H:%M:%S",
        )
        log.info("Hawk V8 starting — Limit Orders + CLV Exit + Regime Filters + Correlation Guard")
        log.info("Config: bankroll=$%.0f, max_bet=$%.0f, kelly=%.0f%%, min_edge=%.0f%%",
                 self.cfg.bankroll_usd, self.cfg.max_bet_usd,
                 self.cfg.kelly_fraction * 100, self.cfg.min_edge * 100)
        log.info("V2 features: compound=%s, news=%s, max_risk=%d, cycle=%dm, weather=%s",
                 self.cfg.compound_bankroll, self.cfg.news_enrichment,
                 self.cfg.max_risk_score, self.cfg.cycle_minutes, self.cfg.weather_enabled)
        log.info("Mode: %s", "DRY RUN" if self.cfg.dry_run else "LIVE TRADING")

        self._init_executor()
        _save_status(self.tracker, self.risk, running=True, cycle=0)

        while True:
            self.cycle += 1
            log.info("=== Hawk V8 Cycle %d ===", self.cycle)

            try:
                self._check_mode_toggle()

                notes = _load_brain_notes()
                if notes:
                    latest = notes[-1]
                    log.info("Brain note: [%s] %s", latest.get("topic", "?"), latest.get("content", "")[:100])

                # Atlas intelligence feed — learnings from research cycles
                from bot.atlas_feed import get_actionable_insights
                atlas_insights = get_actionable_insights("hawk")
                if atlas_insights:
                    log.info("[ATLAS] %d actionable insights for Hawk:", len(atlas_insights))
                    for insight in atlas_insights[:3]:
                        log.info("[ATLAS] → %s", insight[:150])

                self.risk.daily_reset()

                if self.risk.is_shutdown():
                    log.warning("Daily loss cap hit — skipping cycle")
                    _save_status(self.tracker, self.risk, running=True, cycle=self.cycle)
                    await asyncio.sleep(self.cfg.cycle_minutes_normal * 60)
                    continue

                # V7 Phase 2: Regime check — skip or reduce sizing if market conditions are bad
                try:
                    from hawk.regime import check_regime
                    _regime = check_regime(consecutive_losses=self.risk.consecutive_losses)
                    if _regime.should_skip_cycle:
                        log.warning("[REGIME] PAUSED — skipping cycle: %s", ", ".join(_regime.reasons))
                        _save_status(self.tracker, self.risk, running=True, cycle=self.cycle)
                        await asyncio.sleep(self.cfg.cycle_minutes_normal * 60)
                        continue
                    _regime_mult = _regime.size_multiplier
                    if _regime.regime != "normal":
                        log.info("[REGIME] %s (%.2fx): %s", _regime.regime.upper(),
                                 _regime_mult, ", ".join(_regime.reasons))
                except Exception:
                    _regime_mult = 1.0
                    log.debug("[REGIME] Check failed (non-fatal)")

                # 1. Scan all markets
                log.info("Scanning Polymarket markets...")
                markets = scan_all_markets(self.cfg)
                log.info("Found %d eligible markets", len(markets))

                if not markets:
                    _save_status(self.tracker, self.risk, running=True, cycle=self.cycle)
                    await asyncio.sleep(self.cfg.cycle_minutes_normal * 60)
                    continue

                # 2. Filter contested (12-88% YES price)
                contested = []
                for m in markets:
                    yes_price = _get_yes_price(m)
                    if 0.12 <= yes_price <= 0.88:
                        contested.append(m)

                log.info("Contested markets (12-88%%): %d / %d total", len(contested), len(markets))

                # 3. V2: Urgency-weighted ranking (ending-soon first)
                ranked_markets = _urgency_rank(contested)

                # V7: All categories analyzed ($0 cost — all data-driven)
                # Cap non-sports at 30 to limit cross-platform API calls
                sports_markets = [m for m in ranked_markets if m.category == "sports"]
                weather_markets = [m for m in ranked_markets if m.category == "weather"]
                non_sports_markets = [m for m in ranked_markets if m.category not in ("sports", "weather")]
                target_markets = sports_markets + weather_markets + non_sports_markets[:30]
                log.info("V8 Analyzing %d markets: %d sports + %d weather + %d/%d non-sports (all $0)",
                         len(target_markets), len(sports_markets), len(weather_markets),
                         min(len(non_sports_markets), 30), len(non_sports_markets))

                # Track scan stats for dashboard
                _scan_stats = {
                    "total_eligible": len(markets),
                    "contested": len(contested),
                    "sports_analyzed": len(sports_markets),
                    "weather_analyzed": len(weather_markets),
                    "non_sports_analyzed": min(len(non_sports_markets), 30),
                    "total_analyzed": len(target_markets),
                    "non_sports_total": len(non_sports_markets),
                }

                # 4. Analyze with LLM (local Qwen2.5-14B for non-sports, sportsbook for sports)
                estimates = batch_analyze(self.cfg, target_markets, max_concurrent=5)

                # 5. Calculate edges with compound bankroll
                eff_bankroll = self.risk.effective_bankroll()
                log.info("Effective bankroll: $%.2f (base=$%.0f + P&L)", eff_bankroll, self.cfg.bankroll_usd)

                opportunities = []
                estimate_map = {e.market_id: e for e in estimates}
                for market in target_markets:
                    est = estimate_map.get(market.condition_id)
                    if est:
                        opp = calculate_edge(market, est, self.cfg, bankroll=eff_bankroll)
                        if opp:
                            opportunities.append(opp)

                ranked = rank_opportunities(opportunities)
                log.info("Found %d opportunities with edge >= %.0f%%", len(ranked), self.cfg.min_edge * 100)

                # Save opportunities for dashboard
                opp_data = []
                for o in ranked:
                    opp_data.append({
                        "question": o.market.question[:200],
                        "category": o.market.category,
                        "condition_id": o.market.condition_id,
                        "market_price": _get_yes_price(o.market),
                        "estimated_prob": o.estimate.estimated_prob,
                        "edge": o.edge,
                        "direction": o.direction,
                        "position_size": o.position_size_usd,
                        "expected_value": o.expected_value,
                        "reasoning": o.estimate.reasoning[:200],
                        "risk_score": o.risk_score,
                        "time_left_hours": o.time_left_hours,
                        "urgency_label": o.urgency_label,
                        "edge_source": o.estimate.edge_source,
                    })
                _save_opportunities(opp_data)

                # 6. Build suggestions + auto-execute in single pass (one risk check per opp)
                # ML scoring — predict win probability with XGBoost
                try:
                    from quant.ml_predictor import predict_trade
                    for opp in ranked:
                        ml_prob = predict_trade({
                            "edge": opp.edge,
                            "confidence": opp.estimate.confidence,
                            "category": opp.market.category,
                            "direction": opp.direction,
                            "entry_price": _get_yes_price(opp.market),
                            "size_usd": opp.position_size_usd,
                            "risk_score": opp.risk_score,
                            "time_left_hours": opp.time_left_hours,
                            "estimated_prob": opp.estimate.estimated_prob,
                            "expected_value": opp.expected_value,
                            "edge_source": opp.estimate.edge_source,
                            "volume": opp.market.volume,
                            "kelly_fraction": getattr(self.cfg, 'kelly_fraction', 0.2),
                        })
                        if ml_prob is not None:
                            opp._ml_win_prob = ml_prob
                            log.info("[ML] %s: win_prob=%.1f%% | %s",
                                     opp.direction.upper(), ml_prob * 100, opp.market.question[:50])
                except Exception:
                    pass

                intel_ctx = _load_all_intel(target_markets)
                suggestions = []
                trades_placed = 0
                placed_cids: set[str] = set()  # Per-cycle dedup
                for opp in ranked:
                    # Per-cycle duplicate guard
                    if opp.market.condition_id in placed_cids:
                        log.info("Cycle dedup: already placed trade for %s", opp.market.condition_id[:12])
                        continue

                    allowed, reason = self.risk.check_trade(opp)
                    if not allowed:
                        log.info("Risk blocked: %s", reason)
                        continue

                    cid = opp.market.condition_id
                    intel_items = intel_ctx.get(cid, [])
                    has_viper = len(intel_items) > 0
                    tier_info = calculate_confidence_tier(opp, has_viper_intel=has_viper,
                                                         viper_intel_count=len(intel_items))
                    suggestions.append({
                        "condition_id": cid,
                        "token_id": opp.token_id,
                        "question": opp.market.question[:200],
                        "category": opp.market.category,
                        "direction": opp.direction,
                        "position_size": round(opp.position_size_usd, 2),
                        "edge": round(opp.edge, 4),
                        "expected_value": round(opp.expected_value, 4),
                        "market_price": _get_yes_price(opp.market),
                        "estimated_prob": opp.estimate.estimated_prob,
                        "confidence": opp.estimate.confidence,
                        "reasoning": opp.estimate.reasoning[:300],
                        "score": tier_info["score"],
                        "tier": tier_info["tier"],
                        "viper_intel_count": len(intel_items),
                        "end_date": opp.market.end_date,
                        "volume": opp.market.volume,
                        "event_title": opp.market.event_title,
                        # V2 new fields
                        "risk_score": opp.risk_score,
                        "time_left_hours": round(opp.time_left_hours, 1),
                        "urgency_label": opp.urgency_label,
                        "edge_source": opp.estimate.edge_source,
                        "money_thesis": opp.estimate.money_thesis[:300],
                        "news_factor": opp.estimate.news_factor[:300],
                    })

                    # ── Learner Dimension Consultation (pre-decision) ──
                    learner_adj = 0.0
                    learner_blocked = []
                    try:
                        trade_ctx = {
                            "edge_source": opp.estimate.edge_source,
                            "category": opp.market.category,
                            "confidence": opp.estimate.confidence,
                            "risk_score": opp.risk_score,
                            "direction": opp.direction,
                            "time_left_hours": opp.time_left_hours,
                        }
                        learner_adj, learner_blocked = get_dimension_adjustments(trade_ctx)
                        if learner_blocked:
                            log.info("[LEARNER] BLOCKED trade — toxic dimensions: %s | %s",
                                     ", ".join(learner_blocked), opp.market.question[:60])
                            continue
                    except Exception:
                        log.debug("[LEARNER] Consultation failed (non-fatal)")

                    # ── Brain V2: Hard Guardrails + Dynamic Penalties + Domain Memory ──
                    brain_edge_adj = 0.0
                    brain_blocked = False
                    market_price = _get_yes_price(opp.market)
                    model_prob = opp.estimate.estimated_prob
                    category = opp.market.category or "unknown"

                    # ── Category-specific config override ──
                    try:
                        from hawk.config import get_effective_config
                        _cat_cfg = get_effective_config(self.cfg, category)
                        if not _cat_cfg["enabled"]:
                            log.info("[TUNER] Category '%s' DISABLED by override — skipping: %s",
                                     category, opp.market.question[:60])
                            continue
                        effective_min_edge = _cat_cfg["min_edge"]
                    except Exception:
                        effective_min_edge = self.cfg.min_edge

                    # ── HARD BLOCK: Model-Market Divergence > 3x ──
                    divergence_ratio = 0.0
                    if market_price > 0.01:
                        if opp.direction.lower() == "yes":
                            divergence_ratio = model_prob / market_price
                        else:
                            divergence_ratio = (1 - model_prob) / (1 - market_price) if market_price < 0.99 else 1.0

                    if divergence_ratio > 3.0:
                        log.warning("[BRAIN-V2] HARD BLOCK: Model-Market gap %.1fx (model=%.0f%% market=%.0f%%) | %s",
                                    divergence_ratio, model_prob * 100, market_price * 100,
                                    opp.market.question[:80])
                        # Telegram alert
                        try:
                            from hawk.executor import _notify_tg
                            _notify_tg(
                                f"🚫 <b>HAWK HARD BLOCK</b>\n"
                                f"Model-Market Gap: <b>{divergence_ratio:.1f}x</b>\n"
                                f"Model: {model_prob*100:.0f}% | Market: {market_price*100:.0f}%\n"
                                f"{opp.market.question[:100]}\n"
                                f"Edge: {opp.edge*100:.1f}% — BLOCKED (gap > 3x)"
                            )
                        except Exception:
                            pass
                        brain_blocked = True

                    if brain_blocked:
                        continue

                    # ── Divergence Skepticism Penalty (2x-3x range) ──
                    if divergence_ratio > 2.0:
                        # Scale: 2x = -5%, 2.5x = -10%, 3x = -15%
                        skepticism_penalty = -0.05 * (divergence_ratio - 1.0)
                        skepticism_penalty = max(-0.15, skepticism_penalty)
                        brain_edge_adj += skepticism_penalty
                        log.info("[BRAIN-V2] Divergence skepticism: gap=%.1fx → penalty %.1f%% | %s",
                                 divergence_ratio, skepticism_penalty * 100, opp.market.question[:60])

                    if self._brain:
                        try:
                            # ── 1. Granular Domain Memory (weather_exact_degree, sports_team_win, etc.) ──
                            domain_tag = _get_domain_tag(category, opp.market.question)
                            domain_patterns = self._brain.memory.get_active_patterns(
                                pattern_type="hawk_domain_outcome"
                            )
                            domain_wins = 0
                            domain_losses = 0
                            for pat in domain_patterns:
                                desc = pat.get("description", "")
                                if f"'{domain_tag}'" in desc:
                                    ev = pat.get("evidence_count", 1)
                                    if "WON" in desc.upper():
                                        domain_wins += ev
                                    elif "LOST" in desc.upper():
                                        domain_losses += ev

                            domain_total = domain_wins + domain_losses
                            if domain_total >= 2:
                                domain_wr = domain_wins / domain_total if domain_total > 0 else 0
                                # Dynamic penalty: scales with loss rate AND edge size AND streak
                                if domain_wr < 0.4:
                                    # Aggressive: up to -80% penalty for heavily losing domains
                                    loss_severity = (1 - domain_wr)  # 0.6 to 1.0
                                    edge_scale = min(opp.edge / 0.10, 4.0)  # Higher edge = bigger penalty
                                    streak_scale = min(domain_losses / 3.0, 3.0)  # More losses = stronger
                                    domain_penalty = -0.05 * loss_severity * edge_scale * streak_scale
                                    domain_penalty = max(-0.80, domain_penalty)
                                    brain_edge_adj += domain_penalty
                                    log.info("[BRAIN-V2] Domain '%s' penalty: WR=%.0f%% (%dW/%dL) → %.1f%% | %s",
                                             domain_tag, domain_wr * 100, domain_wins, domain_losses,
                                             domain_penalty * 100, opp.market.question[:60])
                                elif domain_wr >= 0.6 and domain_total >= 3:
                                    domain_boost = min(0.03, 0.01 * (domain_wr - 0.5) * 10)
                                    brain_edge_adj += domain_boost
                                    log.info("[BRAIN-V2] Domain '%s' boost: WR=%.0f%% (%dW/%dL) → +%.1f%%",
                                             domain_tag, domain_wr * 100, domain_wins, domain_losses,
                                             domain_boost * 100)

                            # ── 2. Category-level patterns (existing V1 logic, upgraded caps) ──
                            cat_patterns = self._brain.memory.get_active_patterns(
                                pattern_type="hawk_category_outcome"
                            )
                            cat_wins = 0
                            cat_losses = 0
                            for pat in cat_patterns:
                                desc = pat.get("description", "")
                                if f"'{category}'" in desc.lower() or f"'{category}'" in desc:
                                    if "WON" in desc.upper():
                                        cat_wins += 1
                                    elif "LOST" in desc.upper():
                                        cat_losses += 1

                            cat_total = cat_wins + cat_losses
                            if cat_total > 0:
                                cat_wr = cat_wins / cat_total
                                if cat_wr < 0.35 and cat_losses > cat_wins:
                                    cat_penalty = -0.05 * (1 - cat_wr) * min(cat_losses / 2.0, 3.0)
                                    cat_penalty = max(-0.30, cat_penalty)
                                    brain_edge_adj += cat_penalty
                                    log.info("[BRAIN-V2] Category '%s' losing hard (WR=%.0f%%, %dW/%dL) → %.1f%%",
                                             category, cat_wr * 100, cat_wins, cat_losses, cat_penalty * 100)
                                elif cat_wr < 0.5 and cat_losses > cat_wins:
                                    cat_penalty = -0.02 * min(cat_losses, 5)
                                    cat_penalty = max(-0.15, cat_penalty)
                                    brain_edge_adj += cat_penalty
                                    log.info("[BRAIN-V2] Category '%s' weak (WR=%.0f%%, %dW/%dL) → %.1f%%",
                                             category, cat_wr * 100, cat_wins, cat_losses, cat_penalty * 100)
                                elif cat_wr >= 0.6 and cat_wins > cat_losses:
                                    brain_edge_adj += 0.02
                                    log.info("[BRAIN-V2] Category '%s' winning (WR=%.0f%%, %dW/%dL) → +2.0%%",
                                             category, cat_wr * 100, cat_wins, cat_losses)

                            # ── 3. Similar past decisions ──
                            situation_str = f"{category}: {opp.market.question[:120]}"
                            similar = self._brain.memory.get_relevant_context(situation_str, limit=5)
                            if similar:
                                resolved = [d for d in similar if d.get("resolved")]
                                if resolved:
                                    avg_score = sum(d.get("outcome_score", 0) for d in resolved) / len(resolved)
                                    if avg_score < -0.3:
                                        sim_adj = -0.03 * len(resolved)
                                        sim_adj = max(-0.15, sim_adj)
                                    elif avg_score < 0:
                                        sim_adj = -0.015 * len(resolved)
                                        sim_adj = max(-0.08, sim_adj)
                                    elif avg_score > 0.3:
                                        sim_adj = 0.01
                                    else:
                                        sim_adj = 0.0
                                    if sim_adj != 0:
                                        brain_edge_adj += sim_adj
                                        log.info("[BRAIN-V2] %d similar decisions (avg_score=%.2f) → %.1f%%",
                                                 len(resolved), avg_score, sim_adj * 100)

                        except Exception:
                            log.debug("[BRAIN-V2] Memory consultation failed (non-fatal)")

                    # ── Clamp total brain adjustment to -80% / +5% ──
                    brain_edge_adj = max(-0.80, min(0.05, brain_edge_adj))

                    # ── Dashboard warning flag for divergence > 2x ──
                    if divergence_ratio > 2.0:
                        try:
                            _warn_file = DATA_DIR / "hawk_warnings.json"
                            _warnings = []
                            if _warn_file.exists():
                                _warnings = json.loads(_warn_file.read_text()).get("warnings", [])
                            _warnings = [w for w in _warnings if time.time() - w.get("ts", 0) < 3600]
                            _warnings.append({
                                "ts": time.time(),
                                "question": opp.market.question[:150],
                                "divergence": round(divergence_ratio, 2),
                                "model_prob": round(model_prob, 3),
                                "market_price": round(market_price, 3),
                                "edge": round(opp.edge, 4),
                                "action": "BLOCKED" if divergence_ratio > 3.0 else f"PENALTY {brain_edge_adj*100:.1f}%",
                                "category": category,
                            })
                            _warn_file.write_text(json.dumps({"warnings": _warnings}, indent=2))
                        except Exception:
                            pass

                    # ── Telegram alert for high-risk bets that pass through ──
                    if divergence_ratio > 2.0 and brain_edge_adj > -opp.edge:
                        try:
                            from hawk.executor import _notify_tg
                            _notify_tg(
                                f"⚠️ <b>HAWK HIGH-RISK BET</b>\n"
                                f"Gap: <b>{divergence_ratio:.1f}x</b> | Penalty: {brain_edge_adj*100:.1f}%\n"
                                f"Edge: {opp.edge*100:.1f}% → {(opp.edge+brain_edge_adj)*100:.1f}%\n"
                                f"{opp.market.question[:100]}"
                            )
                        except Exception:
                            pass

                    # ── Snipe Assist timing gate (crypto-adjacent only, advisory) ──
                    if category in ("crypto", "cryptocurrency", "crypto_event"):
                        try:
                            import json as _json
                            from pathlib import Path as _Path
                            _assist_file = _Path(__file__).parent.parent / "data" / "snipe_assist.json"
                            if _assist_file.exists():
                                _assist = _json.loads(_assist_file.read_text())
                                import time as _time
                                _age = _time.time() - _assist.get("timestamp", 0)
                                if _age < 120:
                                    _hawk_ovr = (_assist.get("agent_overrides") or {}).get("hawk", {})
                                    _ta_action = _hawk_ovr.get("action", _assist.get("action", ""))
                                    _ta_score = _assist.get("timing_score", 0)
                                    if _ta_action == "auto_skip" and _ta_score < 65:
                                        log.info("[SNIPE-ASSIST] AUTO-SKIP crypto market (score=%.0f): %s",
                                                 _ta_score, opp.market.question[:60])
                                        continue
                                    elif _ta_action == "conservative":
                                        _old = opp.position_size_usd
                                        opp.position_size_usd *= 0.70
                                        log.info("[SNIPE-ASSIST] CONSERVATIVE (score=%.0f) $%.2f->$%.2f: %s",
                                                 _ta_score, _old, opp.position_size_usd, opp.market.question[:60])
                        except Exception:
                            pass

                    # ── Apply combined learner + brain adjustment ──
                    combined_adj = learner_adj + brain_edge_adj
                    if combined_adj < 0:
                        effective_edge = opp.edge + combined_adj
                        if effective_edge < effective_min_edge:
                            log.info("[BRAIN-V2] BLOCKED: edge %.1f%% + learner %.1f%% + brain %.1f%% = %.1f%% < min %.1f%% | %s",
                                     opp.edge * 100, learner_adj * 100, brain_edge_adj * 100,
                                     effective_edge * 100, effective_min_edge * 100, opp.market.question[:60])
                            continue

                    # V7: Only bet with real data-backed edge
                    _ALLOWED_EDGE_SOURCES = {
                        "sportsbook_divergence", "weather_model",
                        "live_score_shift", "cross_platform",
                    }
                    _esrc = (opp.estimate.edge_source or "").lower()
                    if _esrc not in _ALLOWED_EDGE_SOURCES:
                        log.info("[FILTER] Blocked %s edge source (need data-backed): %s",
                                 _esrc, opp.market.question[:60])
                        continue

                    # V7: VPIN toxicity check — detect informed flow
                    try:
                        from hawk.vpin import compute_vpin
                        _vpin = compute_vpin(opp.market.condition_id)
                        if _vpin.recommendation == "block":
                            log.info("[VPIN] BLOCKED: VPIN=%.4f (%s) | %s",
                                     _vpin.vpin, _vpin.toxicity, opp.market.question[:60])
                            continue
                        if _vpin.size_multiplier < 1.0:
                            old_sz = opp.position_size_usd
                            opp.position_size_usd = round(opp.position_size_usd * _vpin.size_multiplier, 2)
                            log.info("[VPIN] Size reduced: $%.2f -> $%.2f (VPIN=%.4f %s) | %s",
                                     old_sz, opp.position_size_usd, _vpin.vpin,
                                     _vpin.toxicity, opp.market.question[:60])
                    except Exception:
                        log.debug("[VPIN] Check failed (non-fatal)")

                    # V7 Phase 2: Odds movement tracking + sizing
                    try:
                        from hawk.odds_movement import record_odds, get_movement
                        _yes_price = _get_yes_price(opp.market)
                        record_odds(opp.market.condition_id, _yes_price)
                        _mv = get_movement(opp.market.condition_id, _yes_price, opp.direction.lower())
                        if _mv.direction != "neutral":
                            old_sz = opp.position_size_usd
                            opp.position_size_usd = round(opp.position_size_usd * _mv.size_multiplier, 2)
                            if _mv.is_steam and _mv.direction == "weakening":
                                log.warning("[ODDS-MOVE] REVERSE STEAM — skipping | %s", opp.market.question[:60])
                                continue
                            log.info("[ODDS-MOVE] %s: $%.2f→$%.2f (%.1fx) | %s",
                                     _mv.direction.upper(), old_sz, opp.position_size_usd,
                                     _mv.size_multiplier, opp.market.question[:60])
                    except Exception:
                        log.debug("[ODDS-MOVE] Check failed (non-fatal)")

                    # V8: Per-category regime check — block/reduce cold categories
                    try:
                        _cat_regime = check_regime(category=opp.market.category)
                        _cat_mult = _cat_regime.size_multiplier
                        if _cat_regime.should_skip_cycle:
                            log.info("[REGIME-CAT] BLOCKED category '%s': %s | %s",
                                     opp.market.category, ", ".join(_cat_regime.reasons),
                                     opp.market.question[:60])
                            continue
                        if _cat_mult < 1.0:
                            old_sz = opp.position_size_usd
                            opp.position_size_usd = round(opp.position_size_usd * _cat_mult, 2)
                            log.info("[REGIME-CAT] '%s' cold: $%.2f→$%.2f (%.2fx) | %s",
                                     opp.market.category, old_sz, opp.position_size_usd,
                                     _cat_mult, opp.market.question[:60])
                    except Exception:
                        log.debug("[REGIME-CAT] Check failed (non-fatal)")

                    # V8 Phase 2: Apply global regime multiplier to sizing
                    if _regime_mult < 1.0:
                        old_sz = opp.position_size_usd
                        opp.position_size_usd = round(opp.position_size_usd * _regime_mult, 2)
                        log.info("[REGIME] Size: $%.2f→$%.2f (%.2fx) | %s",
                                 old_sz, opp.position_size_usd, _regime_mult, opp.market.question[:60])

                    # V6: Atlas pre-bet gate — check alignment before execution
                    atlas_mult, atlas_reason = self._check_atlas_alignment(opp)
                    if atlas_mult == 0.0:
                        log.info("[ATLAS-GATE] BLOCKED: %s | %s", atlas_reason, opp.market.question[:60])
                        continue
                    elif atlas_mult < 1.0:
                        old_size = opp.position_size_usd
                        opp.position_size_usd *= atlas_mult
                        log.info("[ATLAS-GATE] Reduced: $%.2f → $%.2f (%.0fx) | %s | %s",
                                 old_size, opp.position_size_usd, atlas_mult,
                                 atlas_reason, opp.market.question[:60])

                    # Auto-execute immediately after risk approval
                    if self.executor:
                        order_id = self.executor.place_order(opp)
                        if order_id:
                            placed_cids.add(cid)
                            trades_placed += 1
                            adj_tag = ""
                            if learner_adj != 0 or brain_edge_adj != 0:
                                adj_tag = f" | learner={learner_adj*100:+.1f}% brain={brain_edge_adj*100:+.1f}%"
                            log.info("TRADE PLACED: %s %s | $%.2f | edge=%.1f%%%s | %s",
                                     opp.direction.upper(), opp.market.question[:60],
                                     opp.position_size_usd, opp.edge * 100, adj_tag, order_id)
                            # V7: CLV tracking — record entry price
                            try:
                                from hawk.clv import record_entry
                                record_entry(
                                    condition_id=opp.market.condition_id,
                                    token_id=opp.token_id,
                                    direction=opp.direction.upper(),
                                    entry_price=opp.entry_price if hasattr(opp, 'entry_price') else _get_yes_price(opp.market),
                                    question=opp.market.question,
                                )
                            except Exception:
                                log.debug("[CLV] Entry recording failed (non-fatal)")

                            # Brain: record trade decision
                            if self._brain:
                                try:
                                    _ctx = f"{opp.market.category}: {opp.market.question[:100]} | price={_get_yes_price(opp.market):.2f} edge={opp.edge*100:.1f}%"
                                    _dec = f"{opp.direction.upper()} ${opp.position_size_usd:.2f} | est_prob={opp.estimate.estimated_prob:.2f} conf={opp.estimate.confidence}"
                                    _did = self._brain.remember_decision(_ctx, _dec, reasoning=opp.estimate.reasoning[:200], confidence=opp.estimate.confidence / 10.0 if opp.estimate.confidence > 1 else opp.estimate.confidence, tags=[opp.market.category, opp.direction])
                                    # Store for outcome tracking
                                    self.tracker.set_decision_id(opp.market.condition_id, _did)
                                except Exception:
                                    pass

                _save_suggestions(suggestions)
                log.info("Saved %d trade suggestions (HIGH: %d, MEDIUM: %d, SPEC: %d)",
                         len(suggestions),
                         sum(1 for s in suggestions if s["tier"] == "HIGH"),
                         sum(1 for s in suggestions if s["tier"] == "MEDIUM"),
                         sum(1 for s in suggestions if s["tier"] == "SPECULATIVE"))
                if trades_placed > 0:
                    log.info("Placed %d trades this cycle", trades_placed)

                # Check fills (live mode only)
                if self.executor and not self.cfg.dry_run:
                    self.executor.check_fills()

                # V8: In-play live mispricing scan (disabled by default)
                if self.cfg.inplay_enabled:
                    try:
                        from hawk.inplay import scan_live_mispricing
                        _inplay_signals = scan_live_mispricing(
                            self.tracker.open_positions,
                            target_markets,
                            odds_api_key=self.cfg.odds_api_key,
                        )
                        for _sig in _inplay_signals:
                            if _sig.edge >= self.cfg.inplay_min_edge:
                                log.info("[INPLAY] Signal: %s %s | edge=%.1f%% | poly=$%.2f fair=$%.2f | %s | %s",
                                         _sig.direction.upper(), _sig.question[:60],
                                         _sig.edge * 100, _sig.polymarket_price,
                                         _sig.implied_fair, _sig.live_score, _sig.reason)
                            else:
                                log.debug("[INPLAY] Sub-threshold: %s edge=%.1f%%",
                                          _sig.question[:40], _sig.edge * 100)
                    except Exception:
                        log.debug("[INPLAY] Scan failed (non-fatal)")

                # V8: CLV-based early exit check (runs before price-drop checks)
                try:
                    from hawk.clv import should_exit_on_clv
                    for _pos in self.tracker.open_positions:
                        _cid = _pos.get("condition_id") or _pos.get("market_id", "")
                        _tid = _pos.get("token_id", "")
                        if not _cid or not _tid or _pos.get("_early_exit"):
                            continue
                        _clv_exit, _clv_reason = should_exit_on_clv(_cid, _tid)
                        if _clv_exit:
                            log.warning("[CLV-EXIT] %s | %s", _clv_reason, _pos.get("question", "")[:60])
                            _pos["_early_exit"] = True
                            _pos["_exit_reason"] = _clv_reason
                except Exception:
                    log.debug("[CLV-EXIT] Check failed (non-fatal)")

                # V8 Phase 2: Enhanced early exit — graduated levels + time-based urgency
                try:
                    from hawk.vpin import compute_vpin
                    from bot.http_session import get_session
                    _exit_session = get_session()
                    for _pos in self.tracker.open_positions:
                        _cid = _pos.get("condition_id") or _pos.get("market_id", "")
                        _tid = _pos.get("token_id", "")
                        _entry = _pos.get("entry_price", 0.5)
                        if not _cid or not _tid:
                            continue
                        try:
                            _resp = _exit_session.get(
                                f"https://clob.polymarket.com/markets/{_cid}", timeout=5)
                            if _resp.status_code != 200:
                                continue
                            _mdata = _resp.json()
                            _cur_price = None
                            for _tk in _mdata.get("tokens", []):
                                if _tk.get("token_id") == _tid:
                                    _cur_price = float(_tk.get("price", 0.5))
                                    break
                            if _cur_price is None:
                                continue
                            _drop = (_entry - _cur_price) / max(_entry, 0.01)
                            _q = _pos.get("question", "")[:60]

                            # Level 1: Flag (>5% drop)
                            if _drop > 0.05 and not _pos.get("_flagged"):
                                log.info("[EARLY-EXIT] FLAG: price down %.0f%% ($%.2f→$%.2f) | %s",
                                         _drop * 100, _entry, _cur_price, _q)
                                _pos["_flagged"] = True

                            # Level 2: Hard exit (>15% drop — edge gone)
                            if _drop > 0.15:
                                log.warning("[EARLY-EXIT] EXIT: price dropped %.0f%% ($%.2f→$%.2f) | %s",
                                            _drop * 100, _entry, _cur_price, _q)
                                _pos["_early_exit"] = True
                                _pos["_exit_reason"] = f"price_drop_{_drop:.0%}"

                            # VPIN spike + any adverse movement
                            if not _pos.get("_early_exit"):
                                _vpin = compute_vpin(_cid, _tid)
                                if _vpin.toxicity == "high" and _drop > 0.05:
                                    log.warning("[EARLY-EXIT] VPIN spike + adverse | VPIN=%.4f | %s",
                                                _vpin.vpin, _q)
                                    _pos["_early_exit"] = True
                                    _pos["_exit_reason"] = f"vpin_spike_{_vpin.vpin:.2f}"

                            # Time-based urgency: <2h to resolution + underwater = cut losses
                            if not _pos.get("_early_exit"):
                                _end = _pos.get("end_date", "")
                                if _end:
                                    try:
                                        from datetime import datetime, timezone
                                        _end_dt = datetime.fromisoformat(_end.replace("Z", "+00:00"))
                                        _hours_left = (_end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                                        if _hours_left < 2 and _drop > 0.02:
                                            log.warning("[EARLY-EXIT] URGENCY: %.1fh left + down %.0f%% | %s",
                                                        _hours_left, _drop * 100, _q)
                                            _pos["_early_exit"] = True
                                            _pos["_exit_reason"] = f"time_urgency_{_hours_left:.1f}h"
                                    except (ValueError, TypeError):
                                        pass

                            # Sportsbook re-check for sports positions
                            if not _pos.get("_early_exit") and _drop > 0.05:
                                _esrc = (_pos.get("edge_source") or "").lower()
                                if "sportsbook" in _esrc:
                                    try:
                                        from hawk.odds_movement import get_movement
                                        _mv = get_movement(_cid, _cur_price, "yes")
                                        if _mv.direction == "weakening" and _mv.is_steam:
                                            log.warning("[EARLY-EXIT] REVERSE STEAM on sports pos | %s", _q)
                                            _pos["_early_exit"] = True
                                            _pos["_exit_reason"] = "reverse_steam"
                                    except Exception:
                                        pass
                        except Exception:
                            continue
                except Exception:
                    log.debug("[EARLY-EXIT] Check failed (non-fatal)")

                # Resolve trades (check market outcomes)
                res = resolve_paper_trades()
                if res["resolved"] > 0:
                    log.info(
                        "Resolved %d trades: %d W / %d L | P&L: $%.2f",
                        res["resolved"], res["wins"], res["losses"],
                        res.get("total_pnl", 0.0),
                    )
                    # Record per-trade PnL for accurate streak detection
                    for trade_pnl in res.get("per_trade_pnl", []):
                        self.risk.record_pnl(trade_pnl)
                    # Learner: record dimension outcomes
                    for resolved_trade in res.get("resolved_trades", []):
                        try:
                            record_trade_outcome(resolved_trade)
                        except Exception:
                            log.debug("[LEARNER] Failed to record outcome (non-fatal)")
                    # Brain V2: record resolved outcomes with granular domain patterns
                    if self._brain:
                        try:
                            for resolved_trade in res.get("resolved_trades", []):
                                _did = self.tracker.get_decision_id(resolved_trade.get("condition_id", ""))
                                _won = resolved_trade.get("won", False)
                                _pnl = resolved_trade.get("pnl", 0)
                                _conf = 0.6 if _won else 0.4
                                _label = "WON" if _won else "LOST"

                                if _did:
                                    self._brain.remember_outcome(_did, f"{_label} PnL=${_pnl:.2f}", score=1.0 if _won else -1.0)

                                # 1. Category pattern (existing)
                                cat = resolved_trade.get("category", "unknown")
                                self._brain.learn_pattern("hawk_category_outcome",
                                    f"Category '{cat}': {_label}", confidence=_conf)

                                # 2. Edge source pattern
                                esrc = resolved_trade.get("edge_source", "unknown")
                                self._brain.learn_pattern("hawk_edge_source_outcome",
                                    f"Edge source '{esrc}': {_label}", confidence=_conf)

                                # 3. Direction pattern
                                direction = resolved_trade.get("direction", "unknown")
                                self._brain.learn_pattern("hawk_direction_outcome",
                                    f"Direction '{direction}': {_label}", confidence=_conf)

                                # 4. Confidence pattern
                                conf_val = resolved_trade.get("confidence", 0.5)
                                conf_band = "high" if conf_val > 0.7 else "medium" if conf_val >= 0.5 else "low"
                                self._brain.learn_pattern("hawk_confidence_outcome",
                                    f"Confidence '{conf_band}': {_label}", confidence=_conf)

                                # 5. Risk pattern
                                risk = resolved_trade.get("risk_score", 5)
                                risk_band = "low" if risk <= 3 else "medium" if risk <= 6 else "high"
                                self._brain.learn_pattern("hawk_risk_outcome",
                                    f"Risk '{risk_band}': {_label}", confidence=_conf)

                                # 6. Time pattern
                                hours = resolved_trade.get("time_left_hours", 24)
                                time_band = "ending_soon" if hours < 6 else "today" if hours <= 24 else "tomorrow" if hours <= 48 else "this_week"
                                self._brain.learn_pattern("hawk_time_outcome",
                                    f"Time '{time_band}': {_label}", confidence=_conf)

                                # 7. V2: Granular domain pattern
                                _question = resolved_trade.get("question", "")
                                _domain_tag = _get_domain_tag(cat, _question)
                                self._brain.learn_pattern("hawk_domain_outcome",
                                    f"Domain '{_domain_tag}': {_label}", confidence=_conf)

                                # 8. V2: Divergence lesson — record when model-market gap was big
                                _entry = resolved_trade.get("entry_price", 0)
                                _est = resolved_trade.get("estimated_prob", 0)
                                if _entry > 0.01 and _est > 0:
                                    _div = _est / _entry if resolved_trade.get("direction", "").lower() == "yes" else (1 - _est) / (1 - _entry) if _entry < 0.99 else 1.0
                                    if _div > 2.0:
                                        _div_label = f"high_divergence_{_domain_tag}"
                                        self._brain.learn_pattern("hawk_divergence_outcome",
                                            f"Divergence '{_div_label}' gap={_div:.1f}x: {_label}",
                                            confidence=_conf)
                                        log.info("[BRAIN-V2] Divergence lesson: %s gap=%.1fx %s | %s",
                                                 _domain_tag, _div, _label, _question[:60])

                                log.info("[BRAIN-V2] Recorded 8 patterns for %s trade: %s | %s | domain=%s",
                                         _label, cat, esrc, _domain_tag)
                        except Exception:
                            log.debug("[BRAIN-V2] Outcome recording failed (non-fatal)")
                    # Reload tracker
                    self.tracker._positions = []
                    self.tracker._load_positions()

                    # Post-trade review
                    try:
                        from hawk.reviewer import review_resolved_trades
                        review = review_resolved_trades()
                        if review.get("total_reviewed", 0) > 0:
                            log.info("Post-trade review: %d trades, %.1f%% WR, calibration=%.3f",
                                     review["total_reviewed"], review.get("win_rate", 0),
                                     review.get("calibration_score", 0))
                            if review.get("recommendations"):
                                for rec in review["recommendations"]:
                                    log.info("REVIEW REC: %s", rec)
                    except Exception:
                        log.exception("Post-trade review failed")

                # Pattern mining — every 6 cycles (~6 hours)
                if self.cycle % 6 == 0:
                    try:
                        import sys as _sys
                        _sys.path.insert(0, str(Path.home() / "shared"))
                        from pattern_miner import mine_agent as _mine_agent
                        mine_result = _mine_agent("hawk")
                        if not mine_result.get("skipped"):
                            log.info("[PATTERN MINER] Extracted %d patterns from %d resolved decisions",
                                     mine_result.get("patterns_extracted", 0),
                                     mine_result.get("resolved_decisions", 0))
                            try:
                                from shared.events import publish as bus_publish
                                bus_publish(
                                    agent="hawk",
                                    event_type="pattern_mining",
                                    data=mine_result,
                                    summary=f"Hawk pattern mining: {mine_result.get('patterns_extracted', 0)} new patterns",
                                )
                            except Exception:
                                pass
                        else:
                            log.info("[PATTERN MINER] Skipped: %s", mine_result.get("reason", "unknown"))
                    except Exception:
                        log.debug("[PATTERN MINER] Failed (non-fatal)")

                _regime_info = None
                try:
                    _regime_info = {"state": _regime.regime, "multiplier": _regime.size_multiplier,
                                    "reasons": _regime.reasons}
                except Exception:
                    pass
                _save_status(self.tracker, self.risk, running=True, cycle=self.cycle,
                             scan_stats=_scan_stats, regime=_regime_info)

                # Signal cycle status for dashboard badge
                try:
                    _sc_file = Path(__file__).parent.parent / "data" / "hawk_signal_cycle.json"
                    _sc_file.write_text(json.dumps({
                        "last_eval_at": time.time(),
                        "cycle": self.cycle,
                        "markets_scanned": _scan_stats.get("total_analyzed", 0),
                        "markets_eligible": _scan_stats.get("total_eligible", 0),
                        "trades_placed": trades_placed,
                        "regime": _regime.regime if _regime else "unknown",
                    }))
                except Exception:
                    pass

            except Exception:
                log.exception("Hawk V8 cycle %d failed", self.cycle)
                _save_status(self.tracker, self.risk, running=True, cycle=self.cycle)

            # V6: Dynamic cycle timing
            _cycle_markets = target_markets if 'target_markets' in locals() else []
            next_min = self._calculate_next_cycle(_cycle_markets)

            # V8: Smart cache — 5min during live games, 25min otherwise
            try:
                from hawk.odds import set_cache_mode
                set_cache_mode(fast=(next_min <= self.cfg.cycle_minutes_fast))
            except Exception:
                pass
            _save_next_cycle(next_min)
            log.info("Hawk V8 cycle %d complete. Next cycle in %d minutes (%s mode)...",
                     self.cycle, next_min, "fast" if next_min <= self.cfg.cycle_minutes_fast else "normal")
            await asyncio.sleep(next_min * 60)


def _get_yes_price(market) -> float:
    """Get YES/Over/first-token price from market tokens."""
    for t in market.tokens:
        outcome = (t.get("outcome") or "").lower()
        if outcome in ("yes", "up", "over"):
            try:
                return float(t.get("price", 0.5))
            except (ValueError, TypeError):
                return 0.5
    # Fallback: first token is the "yes" equivalent
    if market.tokens:
        try:
            return float(market.tokens[0].get("price", 0.5))
        except (ValueError, TypeError):
            return 0.5
    return 0.5
