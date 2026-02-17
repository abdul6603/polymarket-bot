"""Hawk (market predator) routes: /api/hawk/*"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)
hawk_bp = Blueprint("hawk", __name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data"
TRADES_FILE = DATA_DIR / "hawk_trades.jsonl"
OPPS_FILE = DATA_DIR / "hawk_opportunities.json"
STATUS_FILE = DATA_DIR / "hawk_status.json"
BRIEFING_FILE = DATA_DIR / "hawk_briefing.json"
MARKET_CONTEXT_FILE = DATA_DIR / "viper_market_context.json"
MODE_FILE = DATA_DIR / "hawk_mode.json"
SUGGESTIONS_FILE = DATA_DIR / "hawk_suggestions.json"
ET = ZoneInfo("America/New_York")

_scan_lock = threading.Lock()
_scan_running = False
_scan_progress = {"step": "", "detail": "", "pct": 0, "done": False, "ts": 0}


def _set_progress(step: str, detail: str = "", pct: int = 0, done: bool = False):
    global _scan_progress
    _scan_progress = {"step": step, "detail": detail, "pct": pct, "done": done, "ts": time.time()}


def _load_trades() -> list[dict]:
    if not TRADES_FILE.exists():
        return []
    trades = []
    try:
        with open(TRADES_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    trades.append(json.loads(line))
    except Exception:
        pass
    return trades


def _load_status() -> dict:
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text())
        except Exception:
            pass
    return {"running": False}


@hawk_bp.route("/api/hawk/mode")
def api_hawk_mode():
    """Current Hawk trading mode."""
    if MODE_FILE.exists():
        try:
            data = json.loads(MODE_FILE.read_text())
            return jsonify(data)
        except Exception:
            pass
    import os
    dry_run = os.getenv("HAWK_DRY_RUN", "true").lower() in ("true", "1", "yes")
    return jsonify({"dry_run": dry_run})


@hawk_bp.route("/api/hawk/toggle-mode", methods=["POST"])
def api_hawk_toggle_mode():
    """Toggle Hawk between live and paper trading."""
    current_dry = True
    if MODE_FILE.exists():
        try:
            current_dry = json.loads(MODE_FILE.read_text()).get("dry_run", True)
        except Exception:
            pass
    else:
        import os
        current_dry = os.getenv("HAWK_DRY_RUN", "true").lower() in ("true", "1", "yes")

    new_dry = not current_dry
    DATA_DIR.mkdir(exist_ok=True)
    MODE_FILE.write_text(json.dumps({
        "dry_run": new_dry,
        "toggled_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))
    mode_label = "PAPER" if new_dry else "LIVE"
    return jsonify({"success": True, "dry_run": new_dry, "mode": mode_label})


@hawk_bp.route("/api/hawk/suggestions")
def api_hawk_suggestions():
    """Trade suggestions with confidence tiers for Jordan to review."""
    if SUGGESTIONS_FILE.exists():
        try:
            data = json.loads(SUGGESTIONS_FILE.read_text())
            return jsonify(data)
        except Exception:
            pass
    return jsonify({"suggestions": [], "updated": 0})


@hawk_bp.route("/api/hawk/approve", methods=["POST"])
def api_hawk_approve():
    """Approve a suggested trade for execution."""
    body = request.get_json(force=True, silent=True) or {}
    condition_id = body.get("condition_id", "")
    if not condition_id:
        return jsonify({"success": False, "error": "Missing condition_id"}), 400

    # Load suggestions
    if not SUGGESTIONS_FILE.exists():
        return jsonify({"success": False, "error": "No suggestions file"}), 404
    try:
        sdata = json.loads(SUGGESTIONS_FILE.read_text())
    except Exception:
        return jsonify({"success": False, "error": "Cannot read suggestions"}), 500

    suggestions = sdata.get("suggestions", [])
    target = None
    for s in suggestions:
        if s.get("condition_id") == condition_id:
            target = s
            break

    if not target:
        return jsonify({"success": False, "error": "Suggestion not found"}), 404

    # Determine mode
    import os
    is_dry = True
    if MODE_FILE.exists():
        try:
            is_dry = json.loads(MODE_FILE.read_text()).get("dry_run", True)
        except Exception:
            pass
    else:
        is_dry = os.getenv("HAWK_DRY_RUN", "true").lower() in ("true", "1", "yes")

    try:
        if is_dry:
            # Dry-run: record paper trade directly (no CLOB client needed)
            order_id = f"hawk-dry-{condition_id[:8]}-{int(time.time())}"
            entry_price = target.get("market_price", 0.5)
            if target["direction"] == "no":
                entry_price = 1 - entry_price

            trade_record = {
                "order_id": order_id,
                "condition_id": condition_id,
                "token_id": target.get("token_id", ""),
                "question": target.get("question", ""),
                "category": target.get("category", "other"),
                "direction": target["direction"],
                "size_usd": target.get("position_size", 10),
                "entry_price": round(entry_price, 4),
                "edge": target.get("edge", 0),
                "estimated_prob": target.get("estimated_prob", 0.5),
                "confidence": target.get("confidence", 0.5),
                "reasoning": target.get("reasoning", "")[:200],
                "tier": target.get("tier", "SPECULATIVE"),
                "score": target.get("score", 0),
                # V2 fields
                "risk_score": target.get("risk_score", 5),
                "edge_source": target.get("edge_source", ""),
                "time_left_hours": target.get("time_left_hours", 0),
                "urgency_label": target.get("urgency_label", ""),
                "money_thesis": target.get("money_thesis", "")[:300],
                "news_factor": target.get("news_factor", "")[:300],
                "dry_run": True,
                "resolved": False,
                "time_str": datetime.now(ET).strftime("%Y-%m-%d %H:%M"),
                "timestamp": time.time(),
            }
            DATA_DIR.mkdir(exist_ok=True)
            with open(TRADES_FILE, "a") as f:
                f.write(json.dumps(trade_record) + "\n")
            log.info("Hawk paper trade approved: %s %s | %s", target["direction"].upper(), condition_id[:12], target.get("question", "")[:60])
        else:
            # Live mode: use executor with CLOB client
            from hawk.config import HawkConfig
            from hawk.scanner import HawkMarket
            from hawk.analyst import ProbabilityEstimate
            from hawk.edge import TradeOpportunity
            from hawk.executor import HawkExecutor
            from hawk.tracker import HawkTracker

            cfg = HawkConfig()
            tracker = HawkTracker()
            client = None
            try:
                from bot.auth import build_client
                from bot.config import Config
                client = build_client(Config())
            except Exception:
                log.warning("Could not init CLOB client for approve")

            executor = HawkExecutor(cfg, client, tracker)
            market = HawkMarket(
                condition_id=target["condition_id"],
                question=target["question"],
                category=target.get("category", "other"),
                volume=target.get("volume", 0),
                liquidity=0,
                tokens=[
                    {"outcome": target["direction"], "price": str(target.get("market_price", 0.5)),
                     "token_id": target.get("token_id", "")},
                ],
                end_date=target.get("end_date", ""),
                event_title=target.get("event_title", ""),
            )
            estimate = ProbabilityEstimate(
                market_id=target["condition_id"],
                question=target.get("question", ""),
                estimated_prob=target.get("estimated_prob", 0.5),
                confidence=target.get("confidence", 0.5),
                reasoning=target.get("reasoning", ""),
                category=target.get("category", "other"),
            )
            opp = TradeOpportunity(
                market=market,
                estimate=estimate,
                edge=target.get("edge", 0),
                direction=target["direction"],
                token_id=target.get("token_id", ""),
                kelly_fraction=target.get("position_size", 10) / cfg.bankroll_usd,
                position_size_usd=target.get("position_size", 10),
                expected_value=target.get("expected_value", 0),
            )
            order_id = executor.place_order(opp)
            if not order_id:
                return jsonify({"success": False, "error": "Order placement failed"}), 500

        # Remove from suggestions
        remaining = [s for s in suggestions if s.get("condition_id") != condition_id]
        sdata["suggestions"] = remaining
        SUGGESTIONS_FILE.write_text(json.dumps(sdata, indent=2))
        return jsonify({"success": True, "order_id": order_id, "mode": "dry_run" if is_dry else "live"})
    except Exception as e:
        log.exception("Failed to approve hawk trade")
        return jsonify({"success": False, "error": str(e)[:200]}), 500


@hawk_bp.route("/api/hawk/dismiss", methods=["POST"])
def api_hawk_dismiss():
    """Dismiss a suggested trade."""
    body = request.get_json(force=True, silent=True) or {}
    condition_id = body.get("condition_id", "")
    if not condition_id:
        return jsonify({"success": False, "error": "Missing condition_id"}), 400

    if not SUGGESTIONS_FILE.exists():
        return jsonify({"success": False, "error": "No suggestions"}), 404

    try:
        sdata = json.loads(SUGGESTIONS_FILE.read_text())
        suggestions = sdata.get("suggestions", [])
        remaining = [s for s in suggestions if s.get("condition_id") != condition_id]
        sdata["suggestions"] = remaining
        SUGGESTIONS_FILE.write_text(json.dumps(sdata, indent=2))
        return jsonify({"success": True, "remaining": len(remaining)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)[:200]}), 500


@hawk_bp.route("/api/hawk")
def api_hawk():
    """Full Hawk status — positions, P&L, categories."""
    status = _load_status()
    trades = _load_trades()
    resolved = [t for t in trades if t.get("resolved") and t.get("outcome")]
    wins = sum(1 for t in resolved if t.get("won"))
    losses = len(resolved) - wins
    total_pnl = sum(t.get("pnl", 0) for t in resolved)
    wr = (wins / len(resolved) * 100) if resolved else 0
    open_pos = [t for t in trades if not t.get("resolved")]

    today = datetime.now(ET).strftime("%Y-%m-%d")
    daily_resolved = [t for t in resolved if t.get("time_str", "").startswith(today)]
    daily_pnl = sum(t.get("pnl", 0) for t in daily_resolved)

    return jsonify({
        "summary": {
            "total_trades": len(trades),
            "resolved": len(resolved),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wr, 1),
            "pnl": round(total_pnl, 2),
            "open_positions": len(open_pos),
            "daily_pnl": round(daily_pnl, 2),
        },
        "status": status,
    })


@hawk_bp.route("/api/hawk/opportunities")
def api_hawk_opportunities():
    """Latest scan results with edge."""
    if OPPS_FILE.exists():
        try:
            data = json.loads(OPPS_FILE.read_text())
            return jsonify(data)
        except Exception:
            pass
    return jsonify({"opportunities": [], "updated": 0})


@hawk_bp.route("/api/hawk/positions")
def api_hawk_positions():
    """Open positions."""
    trades = _load_trades()
    open_pos = [t for t in trades if not t.get("resolved")]
    return jsonify({"positions": open_pos[-20:]})


@hawk_bp.route("/api/hawk/history")
def api_hawk_history():
    """Trade history with outcomes."""
    trades = _load_trades()
    resolved = [t for t in trades if t.get("resolved")]
    resolved.reverse()
    return jsonify({"trades": resolved[:50]})


@hawk_bp.route("/api/hawk/categories")
def api_hawk_categories():
    """Category heatmap — from resolved trades + live opportunity breakdown."""
    trades = _load_trades()
    resolved = [t for t in trades if t.get("resolved")]
    cats: dict[str, dict] = {}
    for t in resolved:
        cat = t.get("category", "other")
        if cat not in cats:
            cats[cat] = {"wins": 0, "losses": 0, "pnl": 0.0}
        if t.get("won"):
            cats[cat]["wins"] += 1
        else:
            cats[cat]["losses"] += 1
        cats[cat]["pnl"] = round(cats[cat]["pnl"] + t.get("pnl", 0), 2)

    # Also build opportunity-based category stats from latest scan
    opp_cats: dict[str, dict] = {}
    if OPPS_FILE.exists():
        try:
            data = json.loads(OPPS_FILE.read_text())
            for o in data.get("opportunities", []):
                cat = o.get("category", "other")
                if cat not in opp_cats:
                    opp_cats[cat] = {"count": 0, "total_edge": 0.0, "total_ev": 0.0,
                                     "avg_edge": 0.0, "potential_30": 0.0}
                opp_cats[cat]["count"] += 1
                opp_cats[cat]["total_edge"] += o.get("edge", 0)
                opp_cats[cat]["total_ev"] += o.get("expected_value", 0)
                # $30 profit: buy at market price on the side Hawk picks
                mp = o.get("market_price", 0.5)
                ep = o.get("estimated_prob", 0.5)
                direction = o.get("direction", "no")
                if direction == "yes":
                    buy_price = mp
                    win_prob = ep
                else:
                    buy_price = 1 - mp
                    win_prob = 1 - ep
                if buy_price > 0:
                    shares = 30.0 / buy_price
                    profit = (shares * 1.0) - 30.0  # payout $1/share
                    opp_cats[cat]["potential_30"] += round(profit * win_prob, 2)
            for cat in opp_cats:
                c = opp_cats[cat]
                c["avg_edge"] = round(c["total_edge"] / c["count"] * 100, 1) if c["count"] else 0
                c["total_ev"] = round(c["total_ev"], 2)
                c["total_edge"] = round(c["total_edge"] * 100, 1)
                c["potential_30"] = round(c["potential_30"], 2)
        except Exception:
            pass

    return jsonify({"categories": cats, "opp_categories": opp_cats})


@hawk_bp.route("/api/hawk/scan-status")
def api_hawk_scan_status():
    """Poll scan progress."""
    return jsonify({"scanning": _scan_running, **_scan_progress})


@hawk_bp.route("/api/hawk/scan", methods=["POST"])
def api_hawk_scan():
    """Trigger immediate market scan + analysis in background thread."""
    global _scan_running

    if _scan_running:
        return jsonify({"success": False, "message": "Scan already running"})

    def _run_scan():
        global _scan_running
        try:
            _scan_running = True
            from hawk.config import HawkConfig
            from hawk.scanner import scan_all_markets
            from hawk.analyst import batch_analyze
            from hawk.edge import calculate_edge, rank_opportunities, urgency_label as _urgency_label

            cfg = HawkConfig()
            _set_progress("Scanning Polymarket...", "Fetching active markets from Gamma API", 10)

            # 1. Scan markets
            markets = scan_all_markets(cfg)
            if not markets:
                _set_progress("No markets found", "", 100, done=True)
                return

            _set_progress("Filtering markets", f"Found {len(markets)} total markets, filtering contested...", 25)

            # 2. Filter contested markets (12-88% YES price)
            contested = []
            for m in markets:
                yes_price = 0.5
                for t in m.tokens:
                    if (t.get("outcome") or "").lower() in ("yes", "up"):
                        try:
                            yes_price = float(t.get("price", 0.5))
                        except (ValueError, TypeError):
                            pass
                        break
                if 0.12 <= yes_price <= 0.88:
                    contested.append(m)

            # V2: Urgency-weighted ranking
            from hawk.main import _urgency_rank
            target_markets = _urgency_rank(contested)[:30]

            _set_progress(
                "GPT-4o analysis",
                f"Analyzing {len(target_markets)} contested markets with AI...",
                40,
            )

            # 3. Analyze with GPT-4o
            estimates = batch_analyze(cfg, target_markets, max_concurrent=5)

            _set_progress("Calculating edges", f"Got {len(estimates)} estimates, finding mispriced markets...", 80)

            # 4. Calculate edges
            opportunities = []
            estimate_map = {e.market_id: e for e in estimates}
            for market in target_markets:
                est = estimate_map.get(market.condition_id)
                if est:
                    opp = calculate_edge(market, est, cfg)
                    if opp:
                        opportunities.append(opp)

            ranked = rank_opportunities(opportunities)

            # 5. Save
            opp_data = []
            for o in ranked:
                yes_price = 0.5
                for t in o.market.tokens:
                    if (t.get("outcome") or "").lower() in ("yes", "up"):
                        try:
                            yes_price = float(t.get("price", 0.5))
                        except (ValueError, TypeError):
                            pass
                opp_data.append({
                    "question": o.market.question[:200],
                    "category": o.market.category,
                    "market_price": yes_price,
                    "estimated_prob": o.estimate.estimated_prob,
                    "edge": o.edge,
                    "direction": o.direction,
                    "position_size": o.position_size_usd,
                    "expected_value": o.expected_value,
                    "reasoning": o.estimate.reasoning[:200],
                    "condition_id": o.market.condition_id,
                    "volume": o.market.volume,
                    "end_date": o.market.end_date,
                    "event_title": o.market.event_title,
                    "market_slug": o.market.market_slug,
                    "event_slug": o.market.event_slug,
                    "risk_score": o.risk_score,
                    "time_left_hours": o.time_left_hours,
                    "urgency_label": o.urgency_label,
                    "edge_source": o.estimate.edge_source,
                })

            OPPS_FILE.parent.mkdir(exist_ok=True)
            OPPS_FILE.write_text(json.dumps({
                "opportunities": opp_data,
                "updated": time.time(),
                "total_scanned": len(markets),
                "contested": len(contested),
                "analyzed": len(target_markets),
            }, indent=2))

            # Generate briefing for Viper — targeted intel queries
            try:
                from hawk.briefing import generate_briefing
                generate_briefing(opp_data)
            except Exception:
                log.exception("Failed to generate Hawk briefing from dashboard scan")

            # Generate suggestions with confidence tiers
            try:
                from hawk.edge import calculate_confidence_tier
                viper_ctx = {}
                if MARKET_CONTEXT_FILE.exists():
                    try:
                        viper_ctx = json.loads(MARKET_CONTEXT_FILE.read_text())
                    except Exception:
                        pass
                suggestions = []
                for o in ranked:
                    cid = o.market.condition_id
                    has_viper = len(viper_ctx.get(cid, [])) > 0
                    tier_info = calculate_confidence_tier(o, has_viper_intel=has_viper)
                    yes_price = 0.5
                    for t in o.market.tokens:
                        if (t.get("outcome") or "").lower() in ("yes", "up"):
                            try:
                                yes_price = float(t.get("price", 0.5))
                            except (ValueError, TypeError):
                                pass
                    suggestions.append({
                        "condition_id": cid,
                        "token_id": o.token_id,
                        "question": o.market.question[:200],
                        "category": o.market.category,
                        "direction": o.direction,
                        "position_size": round(o.position_size_usd, 2),
                        "edge": round(o.edge, 4),
                        "expected_value": round(o.expected_value, 4),
                        "market_price": yes_price,
                        "estimated_prob": o.estimate.estimated_prob,
                        "confidence": o.estimate.confidence,
                        "reasoning": o.estimate.reasoning[:300],
                        "score": tier_info["score"],
                        "tier": tier_info["tier"],
                        "viper_intel_count": len(viper_ctx.get(cid, [])),
                        "end_date": o.market.end_date,
                        "volume": o.market.volume,
                        "event_title": o.market.event_title,
                        "risk_score": o.risk_score,
                        "time_left_hours": round(o.time_left_hours, 1),
                        "urgency_label": o.urgency_label,
                        "edge_source": o.estimate.edge_source,
                        "money_thesis": o.estimate.money_thesis[:300],
                        "news_factor": o.estimate.news_factor[:300],
                    })
                SUGGESTIONS_FILE.write_text(json.dumps({
                    "suggestions": suggestions,
                    "updated": time.time(),
                }, indent=2))
                log.info("Hawk scan: saved %d suggestions", len(suggestions))
            except Exception:
                log.exception("Failed to generate suggestions from scan")

            total_ev = sum(o["expected_value"] for o in opp_data)
            _set_progress(
                "Scan complete",
                f"Found {len(opp_data)} opportunities | ${total_ev:.2f} total EV",
                100,
                done=True,
            )
            log.info("Hawk scan complete: %d opportunities with edge", len(opp_data))

        except Exception as e:
            log.exception("Triggered Hawk scan failed")
            _set_progress("Scan failed", str(e)[:200], 0, done=True)
        finally:
            _scan_running = False

    with _scan_lock:
        _set_progress("Starting scan...", "Initializing", 5)
        thread = threading.Thread(target=_run_scan, daemon=True)
        thread.start()

    return jsonify({"success": True, "message": "Scan started"})


@hawk_bp.route("/api/hawk/resolve", methods=["POST"])
def api_hawk_resolve():
    """Trigger resolution check on all unresolved paper trades."""
    try:
        from hawk.resolver import resolve_paper_trades
        result = resolve_paper_trades()
        return jsonify({"success": True, **result})
    except Exception as e:
        log.exception("Hawk resolve failed")
        return jsonify({"success": False, "error": str(e)[:200]})


@hawk_bp.route("/api/hawk/sim")
def api_hawk_sim():
    """Full simulation stats — paper trading performance evaluation."""
    trades = _load_trades()
    if not trades:
        return jsonify({
            "total_trades": 0, "open": 0, "resolved": 0,
            "wins": 0, "losses": 0, "win_rate": 0,
            "total_pnl": 0, "avg_edge": 0, "avg_pnl": 0,
            "best_trade": None, "worst_trade": None,
            "total_wagered": 0, "roi": 0,
            "categories": {}, "open_positions": [], "recent_resolved": [],
        })

    resolved = [t for t in trades if t.get("resolved") and t.get("outcome")]
    open_pos = [t for t in trades if not t.get("resolved")]
    wins = sum(1 for t in resolved if t.get("won"))
    losses = len(resolved) - wins
    total_pnl = sum(t.get("pnl", 0) for t in resolved)
    wr = (wins / len(resolved) * 100) if resolved else 0
    avg_edge = sum(t.get("edge", 0) for t in trades) / len(trades) if trades else 0
    avg_pnl = total_pnl / len(resolved) if resolved else 0
    total_wagered = sum(t.get("size_usd", 0) for t in trades)
    roi = (total_pnl / total_wagered * 100) if total_wagered > 0 else 0

    # Best/worst trade
    best = max(resolved, key=lambda t: t.get("pnl", 0)) if resolved else None
    worst = min(resolved, key=lambda t: t.get("pnl", 0)) if resolved else None

    # Category breakdown
    cats: dict[str, dict] = {}
    for t in resolved:
        cat = t.get("category", "other")
        if cat not in cats:
            cats[cat] = {"wins": 0, "losses": 0, "pnl": 0.0, "trades": 0}
        cats[cat]["trades"] += 1
        if t.get("won"):
            cats[cat]["wins"] += 1
        else:
            cats[cat]["losses"] += 1
        cats[cat]["pnl"] = round(cats[cat]["pnl"] + t.get("pnl", 0), 2)
    for cat in cats:
        total = cats[cat]["wins"] + cats[cat]["losses"]
        cats[cat]["win_rate"] = round(cats[cat]["wins"] / total * 100, 1) if total > 0 else 0

    # Trim for response
    def _trim(t):
        return {
            "question": t.get("question", "")[:120],
            "direction": t.get("direction", ""),
            "size_usd": t.get("size_usd", 0),
            "entry_price": t.get("entry_price", 0),
            "edge": t.get("edge", 0),
            "estimated_prob": t.get("estimated_prob", 0),
            "category": t.get("category", ""),
            "time_str": t.get("time_str", ""),
            "resolved": t.get("resolved", False),
            "won": t.get("won", False),
            "pnl": t.get("pnl", 0),
            "reasoning": t.get("reasoning", "")[:200],
        }

    recent = sorted(resolved, key=lambda t: t.get("resolve_time", 0), reverse=True)[:20]

    return jsonify({
        "total_trades": len(trades),
        "open": len(open_pos),
        "resolved": len(resolved),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wr, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_edge": round(avg_edge * 100, 1),
        "avg_pnl": round(avg_pnl, 2),
        "best_trade": _trim(best) if best else None,
        "worst_trade": _trim(worst) if worst else None,
        "total_wagered": round(total_wagered, 2),
        "roi": round(roi, 1),
        "categories": cats,
        "open_positions": [_trim(t) for t in open_pos[-20:]],
        "recent_resolved": [_trim(t) for t in recent],
    })


@hawk_bp.route("/api/hawk/intel-sync")
def api_hawk_intel_sync():
    """Hawk-Viper intel sync status — briefing + matched context."""
    result = {
        "briefing": None,
        "context": None,
        "sync_active": False,
    }

    # Load briefing
    if BRIEFING_FILE.exists():
        try:
            briefing = json.loads(BRIEFING_FILE.read_text())
            age = time.time() - briefing.get("generated_at", 0)
            result["briefing"] = {
                "generated_at": briefing.get("generated_at", 0),
                "age_minutes": round(age / 60, 1),
                "stale": age > 7200,
                "briefed_markets": briefing.get("briefed_markets", 0),
                "cycle": briefing.get("cycle", 0),
                "markets": briefing.get("markets", []),
            }
        except Exception:
            pass

    # Load market context from Viper
    if MARKET_CONTEXT_FILE.exists():
        try:
            ctx = json.loads(MARKET_CONTEXT_FILE.read_text())
            total_links = sum(len(v) for v in ctx.values())
            markets_with_intel = len(ctx)
            # Enrich: show which briefed markets have intel
            market_intel = []
            if result["briefing"]:
                for m in result["briefing"]["markets"]:
                    cid = m.get("condition_id", "")
                    intel_items = ctx.get(cid, [])
                    market_intel.append({
                        "question": m.get("question", "")[:120],
                        "condition_id": cid,
                        "priority": m.get("priority", 0),
                        "entities": m.get("entities", []),
                        "intel_count": len(intel_items),
                        "intel_items": intel_items[:3],  # Top 3 per market
                    })
            result["context"] = {
                "markets_with_intel": markets_with_intel,
                "total_links": total_links,
                "market_intel": market_intel,
            }
        except Exception:
            pass

    result["sync_active"] = (
        result["briefing"] is not None
        and not result["briefing"].get("stale", True)
        and result["context"] is not None
        and result["context"].get("total_links", 0) > 0
    )

    return jsonify(result)


# ── V2 New Endpoints ──

REVIEWS_FILE = DATA_DIR / "hawk_reviews.json"


@hawk_bp.route("/api/hawk/risk-meter")
def api_hawk_risk_meter():
    """Risk distribution chart data from current suggestions."""
    if not SUGGESTIONS_FILE.exists():
        return jsonify({"distribution": {}, "avg_risk": 0, "total": 0})
    try:
        data = json.loads(SUGGESTIONS_FILE.read_text())
        suggestions = data.get("suggestions", [])
        dist = {"low": 0, "medium": 0, "high": 0, "extreme": 0}
        for s in suggestions:
            rs = s.get("risk_score", 5)
            if rs <= 3:
                dist["low"] += 1
            elif rs <= 6:
                dist["medium"] += 1
            elif rs <= 8:
                dist["high"] += 1
            else:
                dist["extreme"] += 1
        scores = [s.get("risk_score", 5) for s in suggestions]
        avg = round(sum(scores) / len(scores), 1) if scores else 0
        return jsonify({"distribution": dist, "avg_risk": avg, "total": len(suggestions)})
    except Exception:
        return jsonify({"distribution": {}, "avg_risk": 0, "total": 0})


@hawk_bp.route("/api/hawk/reviews")
def api_hawk_reviews():
    """Post-trade analysis from hawk_reviews.json."""
    if not REVIEWS_FILE.exists():
        return jsonify({"total_reviewed": 0, "trade_reviews": []})
    try:
        data = json.loads(REVIEWS_FILE.read_text())
        return jsonify(data)
    except Exception:
        return jsonify({"total_reviewed": 0, "trade_reviews": []})


@hawk_bp.route("/api/hawk/performance")
def api_hawk_performance():
    """Win rate breakdowns by category, risk level, and edge range."""
    if not REVIEWS_FILE.exists():
        # Compute from trades directly
        trades = _load_trades()
        resolved = [t for t in trades if t.get("resolved") and t.get("outcome")]
        if not resolved:
            return jsonify({"total": 0, "by_category": {}, "by_risk": {}, "by_edge": {}})

        by_cat: dict[str, dict] = {}
        by_risk: dict[str, dict] = {}
        by_edge: dict[str, dict] = {}

        for t in resolved:
            cat = t.get("category", "other")
            if cat not in by_cat:
                by_cat[cat] = {"wins": 0, "losses": 0, "pnl": 0.0}
            if t.get("won"):
                by_cat[cat]["wins"] += 1
            else:
                by_cat[cat]["losses"] += 1
            by_cat[cat]["pnl"] += t.get("pnl", 0)

            rs = t.get("risk_score", 5)
            bucket = "low" if rs <= 3 else "medium" if rs <= 6 else "high"
            if bucket not in by_risk:
                by_risk[bucket] = {"wins": 0, "losses": 0, "pnl": 0.0}
            if t.get("won"):
                by_risk[bucket]["wins"] += 1
            else:
                by_risk[bucket]["losses"] += 1
            by_risk[bucket]["pnl"] += t.get("pnl", 0)

            edge = t.get("edge", 0)
            eb = "7-10%" if edge < 0.10 else "10-15%" if edge < 0.15 else "15-20%" if edge < 0.20 else "20%+"
            if eb not in by_edge:
                by_edge[eb] = {"wins": 0, "losses": 0, "pnl": 0.0}
            if t.get("won"):
                by_edge[eb]["wins"] += 1
            else:
                by_edge[eb]["losses"] += 1
            by_edge[eb]["pnl"] += t.get("pnl", 0)

        return jsonify({
            "total": len(resolved),
            "by_category": by_cat,
            "by_risk": by_risk,
            "by_edge": by_edge,
        })

    try:
        data = json.loads(REVIEWS_FILE.read_text())
        return jsonify({
            "total": data.get("total_reviewed", 0),
            "win_rate": data.get("win_rate", 0),
            "by_category": data.get("win_rate_by_category", {}),
            "by_risk": data.get("win_rate_by_risk_level", {}),
            "by_edge": data.get("win_rate_by_edge_range", {}),
            "calibration": data.get("calibration_score", 0),
            "recommendations": data.get("recommendations", []),
        })
    except Exception:
        return jsonify({"total": 0, "by_category": {}, "by_risk": {}, "by_edge": {}})
