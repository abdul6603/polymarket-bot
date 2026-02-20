"""Hawk V2 Main Bot Loop — The Smart Degen."""
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
from hawk.briefing import generate_briefing
from hawk.arb import ArbEngine
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
                 arb_engine: ArbEngine | None = None) -> None:
    """Save current status to data/hawk_status.json for dashboard."""
    DATA_DIR.mkdir(exist_ok=True)
    summary = tracker.summary()
    summary["running"] = running
    summary["cycle"] = cycle
    summary["last_update"] = datetime.now(ET).isoformat()
    if risk:
        summary["effective_bankroll"] = round(risk.effective_bankroll(), 2)
        summary["consecutive_losses"] = risk.consecutive_losses
    if arb_engine:
        summary.update(arb_engine.summary())
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


class HawkBot:
    """The Poker Shark V2 — The Smart Degen."""

    def __init__(self):
        self.cfg = HawkConfig()
        self.tracker = HawkTracker()
        self.risk = HawkRiskManager(self.cfg, self.tracker)
        self.executor: HawkExecutor | None = None
        self.arb: ArbEngine | None = None
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
        self.arb = ArbEngine(self.cfg, client)

    async def _fast_arb_loop(self) -> None:
        """Independent fast arb scanner — runs every 60s, zero GPT cost.

        Scans Polymarket binary markets for price mismatches where buying
        both sides guarantees profit. Uses only Gamma API + CLOB orderbook
        (both free). Completely independent from the main GPT analysis cycle.
        """
        log.info("[ARB] Fast arb loop started — scanning every %ds", self.cfg.arb_scan_interval)
        while True:
            try:
                if not self.cfg.arb_enabled or self.risk.is_shutdown():
                    await asyncio.sleep(self.cfg.arb_scan_interval)
                    continue

                markets = scan_all_markets(self.cfg)
                if markets:
                    arb_opps = self.arb.scan(markets)
                    for arb_opp in arb_opps[:self.cfg.arb_max_concurrent]:
                        result = self.arb.execute(arb_opp)
                        if result:
                            log.info("[ARB] Executed arb trade! profit=$%.2f", arb_opp.expected_profit_usd)

                    # Resolve settled arbs
                    arb_res = self.arb.resolve()
                    if arb_res["resolved"] > 0:
                        log.info("[ARB] Resolved %d arbs | profit: $%.2f",
                                 arb_res["resolved"], arb_res["profit"])
                    self.arb.save_status()
                    _save_status(self.tracker, self.risk, running=True, cycle=self.cycle, arb_engine=self.arb)

            except Exception:
                log.exception("[ARB] Fast arb loop error")

            await asyncio.sleep(self.cfg.arb_scan_interval)

    async def run(self) -> None:
        """Main loop — V2 Smart Degen."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s [HAWK] %(message)s",
            datefmt="%H:%M:%S",
        )
        log.info("Hawk V2 starting — The Smart Degen")
        log.info("Config: bankroll=$%.0f, max_bet=$%.0f, kelly=%.0f%%, min_edge=%.0f%%",
                 self.cfg.bankroll_usd, self.cfg.max_bet_usd,
                 self.cfg.kelly_fraction * 100, self.cfg.min_edge * 100)
        log.info("V2 features: compound=%s, news=%s, max_risk=%d, cycle=%dm, arb_interval=%ds",
                 self.cfg.compound_bankroll, self.cfg.news_enrichment,
                 self.cfg.max_risk_score, self.cfg.cycle_minutes, self.cfg.arb_scan_interval)
        log.info("Mode: %s", "DRY RUN" if self.cfg.dry_run else "LIVE TRADING")

        self._init_executor()
        _save_status(self.tracker, self.risk, running=True, cycle=0, arb_engine=self.arb)

        # Launch fast arb scanner as independent background task
        asyncio.create_task(self._fast_arb_loop())

        while True:
            self.cycle += 1
            log.info("=== Hawk V2 Cycle %d ===", self.cycle)

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
                    _save_status(self.tracker, self.risk, running=True, cycle=self.cycle, arb_engine=self.arb)
                    await asyncio.sleep(self.cfg.cycle_minutes * 60)
                    continue

                # 1. Scan all markets
                log.info("Scanning Polymarket markets...")
                markets = scan_all_markets(self.cfg)
                log.info("Found %d eligible markets", len(markets))

                if not markets:
                    _save_status(self.tracker, self.risk, running=True, cycle=self.cycle, arb_engine=self.arb)
                    await asyncio.sleep(self.cfg.cycle_minutes * 60)
                    continue

                # NOTE: Arb scanning moved to independent fast loop (_fast_arb_loop)
                # running every 60s — no longer tied to the main GPT cycle.

                # 2. Filter contested (12-88% YES price)
                contested = []
                for m in markets:
                    yes_price = _get_yes_price(m)
                    if 0.12 <= yes_price <= 0.88:
                        contested.append(m)

                log.info("Contested markets (12-88%%): %d / %d total", len(contested), len(markets))

                # 3. V2: Urgency-weighted ranking (ending-soon first)
                ranked_markets = _urgency_rank(contested)

                # Cap at 30 for GPT-4o analysis
                target_markets = ranked_markets[:30]
                log.info("Analyzing %d urgency-ranked markets with GPT-4o V2...", len(target_markets))

                # 4. Analyze with GPT-4o (V2 wise degen personality)
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

                # Generate briefing for Viper (always, not just when opps exist)
                try:
                    all_market_data = [{
                        "question": m.question[:200],
                        "condition_id": m.condition_id,
                        "category": m.category,
                        "volume": m.volume,
                    } for m in target_markets]
                    generate_briefing(all_market_data if not opp_data else opp_data, self.cycle)
                except Exception:
                    log.exception("Failed to generate Hawk briefing")

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

                    # ── Brain Memory Consultation (pre-decision) ──
                    # Query learned patterns + past decisions BEFORE placing bet
                    brain_edge_adj = 0.0
                    if self._brain:
                        try:
                            category = opp.market.category or "unknown"

                            # 1. Check category-level patterns (e.g., "Category 'politics': LOST")
                            cat_patterns = self._brain.memory.get_active_patterns(
                                pattern_type="hawk_category_outcome"
                            )
                            cat_wins = 0
                            cat_losses = 0
                            cat_avg_conf = 0.0
                            cat_count = 0
                            for pat in cat_patterns:
                                desc = pat.get("description", "")
                                if f"'{category}'" in desc.lower() or f"'{category}'" in desc:
                                    cat_count += 1
                                    cat_avg_conf += pat.get("confidence", 0.5)
                                    if "WON" in desc.upper():
                                        cat_wins += 1
                                    elif "LOST" in desc.upper():
                                        cat_losses += 1

                            if cat_count > 0:
                                cat_avg_conf /= cat_count

                                # Losing category (avg confidence < 0.5) → penalize edge
                                # Winning category (avg confidence >= 0.5) → small boost
                                if cat_avg_conf < 0.45 and cat_losses > cat_wins:
                                    brain_edge_adj = -0.03  # Max penalty
                                    log.info("[BRAIN] Category '%s' losing pattern (conf=%.2f, W=%d L=%d) → edge penalty %.1f%%",
                                             category, cat_avg_conf, cat_wins, cat_losses, brain_edge_adj * 100)
                                elif cat_avg_conf < 0.5 and cat_losses > cat_wins:
                                    brain_edge_adj = -0.015
                                    log.info("[BRAIN] Category '%s' weak pattern (conf=%.2f, W=%d L=%d) → edge penalty %.1f%%",
                                             category, cat_avg_conf, cat_wins, cat_losses, brain_edge_adj * 100)
                                elif cat_avg_conf >= 0.55 and cat_wins > cat_losses:
                                    brain_edge_adj = 0.02  # Small boost for winning categories
                                    log.info("[BRAIN] Category '%s' winning pattern (conf=%.2f, W=%d L=%d) → edge boost +%.1f%%",
                                             category, cat_avg_conf, cat_wins, cat_losses, brain_edge_adj * 100)

                            # 2. Check similar past decisions for this market
                            situation_str = f"{category}: {opp.market.question[:120]}"
                            similar = self._brain.memory.get_relevant_context(situation_str, limit=5)
                            if similar:
                                resolved = [d for d in similar if d.get("resolved")]
                                if resolved:
                                    avg_score = sum(d.get("outcome_score", 0) for d in resolved) / len(resolved)
                                    # Similar markets that lost → additional penalty (up to -0.015)
                                    # Similar markets that won → additional boost (up to +0.01)
                                    if avg_score < -0.3:
                                        sim_adj = -0.015
                                    elif avg_score < 0:
                                        sim_adj = -0.008
                                    elif avg_score > 0.3:
                                        sim_adj = 0.01
                                    else:
                                        sim_adj = 0.0

                                    if sim_adj != 0:
                                        # Clamp total adjustment to +/- 0.03
                                        brain_edge_adj = max(-0.03, min(0.03, brain_edge_adj + sim_adj))
                                        log.info("[BRAIN] %d similar past decisions (avg_score=%.2f) → adj %.1f%% | total brain adj: %.1f%%",
                                                 len(resolved), avg_score, sim_adj * 100, brain_edge_adj * 100)

                            # 3. Apply combined learner + brain adjustment
                            combined_adj = learner_adj + brain_edge_adj
                            if combined_adj < 0:
                                effective_edge = opp.edge + combined_adj
                                if effective_edge < self.cfg.min_edge:
                                    log.info("[BRAIN+LEARNER] BLOCKED: edge %.1f%% + learner %.1f%% + brain %.1f%% = %.1f%% < min %.1f%% | %s",
                                             opp.edge * 100, learner_adj * 100, brain_edge_adj * 100,
                                             effective_edge * 100, self.cfg.min_edge * 100, opp.market.question[:60])
                                    continue

                        except Exception:
                            log.debug("[BRAIN] Memory consultation failed (non-fatal)")
                            brain_edge_adj = 0.0

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

                # Resolve paper trades
                if self.cfg.dry_run:
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
                        # Brain: record resolved outcomes
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

                                    log.info("[BRAIN] Recorded 6 patterns for %s trade: %s | %s | %s",
                                             _label, cat, esrc, direction)
                            except Exception:
                                log.debug("[BRAIN] Outcome recording failed (non-fatal)")
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

                _save_status(self.tracker, self.risk, running=True, cycle=self.cycle, arb_engine=self.arb)

            except Exception:
                log.exception("Hawk V2 cycle %d failed", self.cycle)
                _save_status(self.tracker, self.risk, running=True, cycle=self.cycle, arb_engine=self.arb)

            log.info("Hawk V2 cycle %d complete. Sleeping %d minutes...", self.cycle, self.cfg.cycle_minutes)
            await asyncio.sleep(self.cfg.cycle_minutes * 60)


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
