"""Hawk (market predator) routes: /api/hawk/*"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)
hawk_bp = Blueprint("hawk", __name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data"
TRADES_FILE = DATA_DIR / "hawk_trades.jsonl"
OPPS_FILE = DATA_DIR / "hawk_opportunities.json"
STATUS_FILE = DATA_DIR / "hawk_status.json"
ET = timezone(timedelta(hours=-5))

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
            from hawk.edge import calculate_edge, rank_opportunities

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

            contested.sort(key=lambda m: m.volume, reverse=True)
            target_markets = contested[5:35] if len(contested) > 35 else contested

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
                })

            OPPS_FILE.parent.mkdir(exist_ok=True)
            OPPS_FILE.write_text(json.dumps({
                "opportunities": opp_data,
                "updated": time.time(),
                "total_scanned": len(markets),
                "contested": len(contested),
                "analyzed": len(target_markets),
            }, indent=2))

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
