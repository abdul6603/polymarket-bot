"""Arbiter Main Loop — scan, analyze, execute, track. Every 5 minutes."""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from arbiter.config import ArbiterConfig
from arbiter.scanner import scan_all_events
from arbiter.analyzer import check_sum_to_one, check_monotonic, check_complement
from arbiter.executor import ArbiterExecutor
from arbiter.tracker import ArbiterTracker

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
STATUS_FILE = DATA_DIR / "arbiter_status.json"
ET = ZoneInfo("America/New_York")

_TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# Brotherhood kill switch
_killswitch_check = None
try:
    import sys as _ks_sys
    _ks_sys.path.insert(0, str(Path.home() / "shared"))
    from killswitch import is_killed as _killswitch_check
except ImportError:
    pass


def _notify_tg(text: str) -> None:
    if not _TG_TOKEN or not _TG_CHAT:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage",
            json={"chat_id": _TG_CHAT, "text": text, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception:
        pass


def _save_status(cfg: ArbiterConfig, tracker: ArbiterTracker,
                 cycle: int = 0, events_scanned: int = 0,
                 arbs_found: int = 0, arbs_executed: int = 0) -> None:
    """Save current status for dashboard."""
    DATA_DIR.mkdir(exist_ok=True)
    summary = tracker.summary()
    summary["running"] = True
    summary["cycle"] = cycle
    summary["events_scanned"] = events_scanned
    summary["arbs_found_this_cycle"] = arbs_found
    summary["arbs_executed_this_cycle"] = arbs_executed
    summary["mode"] = "DRY RUN" if cfg.dry_run else "LIVE"
    summary["bankroll"] = cfg.bankroll_usd
    summary["cycle_minutes"] = cfg.cycle_minutes
    summary["min_deviation_pct"] = cfg.min_deviation_pct
    summary["last_update"] = datetime.now(ET).isoformat()
    try:
        STATUS_FILE.write_text(json.dumps(summary, indent=2))
    except Exception:
        log.exception("Failed to save Arbiter status")


def run() -> None:
    """Main loop — scan, analyze, execute, track."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s [ARBITER] %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = ArbiterConfig()
    executor = ArbiterExecutor(cfg)
    tracker = ArbiterTracker()
    cycle = 0

    log.info("Arbiter starting — Cross-Market Inconsistency Scanner")
    log.info("Config: bankroll=$%.0f, max_per_arb=$%.0f, min_dev=%.1f%%, cycle=%dm",
             cfg.bankroll_usd, cfg.max_per_arb_usd, cfg.min_deviation_pct, cfg.cycle_minutes)
    log.info("Mode: %s", "DRY RUN" if cfg.dry_run else "LIVE TRADING")

    _notify_tg(
        "\U0001f50d <b>ARBITER STARTED</b>\n"
        f"\n"
        f"Mode: <b>{'DRY RUN' if cfg.dry_run else 'LIVE'}</b>\n"
        f"Bankroll: ${cfg.bankroll_usd:.0f}\n"
        f"Min deviation: {cfg.min_deviation_pct:.1f}%\n"
        f"Cycle: every {cfg.cycle_minutes}min"
    )

    _save_status(cfg, tracker, cycle=0)

    while True:
        cycle += 1
        log.info("=== Arbiter Cycle %d ===", cycle)

        events_scanned = 0
        arbs_found = 0
        arbs_executed = 0

        try:
            # Brotherhood kill switch
            if _killswitch_check:
                ks = _killswitch_check()
                if ks:
                    log.warning("[KILLSWITCH] Trading halted: %s", ks.get("reason", "?"))
                    time.sleep(cfg.cycle_minutes * 60)
                    continue

            # 1. Scan all events with bracket markets
            groups = scan_all_events(cfg)
            events_scanned = len(groups)
            log.info("Scanned %d bracket groups", events_scanned)

            if not groups:
                _save_status(cfg, tracker, cycle, events_scanned)
                time.sleep(cfg.cycle_minutes * 60)
                continue

            # 2. Analyze for inconsistencies
            opportunities = []
            for group in groups:
                # Sum-to-one check (primary strategy)
                opp = check_sum_to_one(group, cfg)
                if opp:
                    opportunities.append(opp)

                # Monotonic check
                mono_opps = check_monotonic(group, cfg)
                opportunities.extend(mono_opps)

                # Complement check
                comp_opps = check_complement(group, cfg)
                opportunities.extend(comp_opps)

            arbs_found = len(opportunities)
            log.info("Found %d arb opportunities (min deviation: %.1f%%)",
                     arbs_found, cfg.min_deviation_pct)

            # 3. Rank by profit potential
            opportunities.sort(key=lambda o: o.expected_profit_pct, reverse=True)

            # Log top opportunities even if not executing
            for i, opp in enumerate(opportunities[:10]):
                log.info("  #%d: %s | %s | dev=%.1f%% | profit=%.1f%% | %d legs",
                         i + 1, opp.arb_type, opp.event_title[:50],
                         opp.deviation_pct, opp.expected_profit_pct, len(opp.legs))

            # 4. Execute top opportunities (respect max concurrent + dedup)
            for opp in opportunities:
                if arbs_executed >= cfg.max_concurrent_arbs:
                    break

                if not tracker.can_open(opp.event_slug, cfg.max_concurrent_arbs):
                    continue

                # Check bankroll
                if tracker.total_exposure() + opp.total_cost * cfg.max_per_arb_usd > cfg.bankroll_usd:
                    log.info("Bankroll limit — skipping %s", opp.event_slug)
                    continue

                result = executor.execute_arb(opp)

                if result["status"] in ("success", "dry_run_success"):
                    leg_dicts = [
                        {"condition_id": l.condition_id, "token_id": l.token_id,
                         "side": l.side, "price": l.price, "size_usd": l.size_usd}
                        for l in opp.legs
                    ]
                    tracker.record_arb(
                        event_slug=opp.event_slug,
                        event_title=opp.event_title,
                        arb_type=opp.arb_type,
                        legs=leg_dicts,
                        order_ids=result["order_ids"],
                        total_cost=opp.total_cost,
                        expected_profit_pct=opp.expected_profit_pct,
                        deviation_pct=opp.deviation_pct,
                        status=result["status"],
                    )
                    arbs_executed += 1
                    log.info("ARB EXECUTED: %s | %s | profit=%.1f%%",
                             opp.arb_type, opp.event_slug, opp.expected_profit_pct)

            # 5. Check resolutions on existing positions
            resolved = tracker.check_resolutions()
            if resolved:
                total_pnl = sum(r.get("pnl", 0) for r in resolved)
                log.info("Resolved %d arbs | P&L: $%.2f", len(resolved), total_pnl)

            # 6. Cycle summary
            _send_cycle_summary(cycle, events_scanned, arbs_found,
                                arbs_executed, tracker, cfg)

            _save_status(cfg, tracker, cycle, events_scanned, arbs_found, arbs_executed)

            # Publish to event bus
            try:
                import sys
                sys.path.insert(0, str(Path.home() / "shared"))
                from events import publish as bus_publish
                bus_publish(
                    agent="arbiter",
                    event_type="cycle_complete",
                    data={
                        "cycle": cycle,
                        "events_scanned": events_scanned,
                        "arbs_found": arbs_found,
                        "arbs_executed": arbs_executed,
                        "active_arbs": tracker.active_count,
                        "total_pnl": tracker.summary()["total_pnl"],
                    },
                    summary=f"Arbiter cycle {cycle}: scanned {events_scanned} groups, found {arbs_found} arbs, executed {arbs_executed}",
                )
            except Exception:
                pass

        except Exception:
            log.exception("Arbiter cycle %d failed", cycle)
            _save_status(cfg, tracker, cycle, events_scanned)

        log.info("Arbiter cycle %d complete. Next in %d minutes.",
                 cycle, cfg.cycle_minutes)
        time.sleep(cfg.cycle_minutes * 60)


def _send_cycle_summary(cycle: int, events_scanned: int, arbs_found: int,
                        arbs_executed: int, tracker: ArbiterTracker,
                        cfg: ArbiterConfig) -> None:
    """Send TG cycle summary (only if arbs were found)."""
    if arbs_found == 0 and cycle % 12 != 0:
        return  # Only send empty summaries every hour

    summary = tracker.summary()
    mode = "DRY RUN" if cfg.dry_run else "LIVE"

    _notify_tg(
        f"\U0001f50d <b>ARBITER CYCLE #{cycle}</b> [{mode}]\n"
        f"\n"
        f"\U0001f4ca Events scanned: {events_scanned}\n"
        f"\U0001f4a1 Arbs found: {arbs_found}\n"
        f"\u2705 Arbs executed: {arbs_executed}\n"
        f"\U0001f4b0 Active: {summary['active_arbs']} | P&L: ${summary['total_pnl']:.2f}\n"
        f"\U0001f4b5 Exposure: ${summary['total_exposure']:.2f} / ${cfg.bankroll_usd:.0f}"
    )
