"""Overview routes: /api/overview, /api/intelligence, /api/broadcasts, /api/agent/<agent>/kpis"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from flask import Blueprint, jsonify

from bot.shared import (
    _load_trades,
    get_atlas,
    ET,
    DATA_DIR,
    INDICATOR_ACCURACY_FILE,
    SOREN_QUEUE_FILE,
    ATLAS_ROOT,
    MERCURY_ROOT,
    MERCURY_POSTING_LOG,
    SHELBY_ROOT_DIR,
    SHELBY_TASKS_FILE,
    SHELBY_ASSESSMENTS_FILE,
)

overview_bp = Blueprint("overview", __name__)

THOR_DATA = Path.home() / "thor" / "data"


def _get_thor_overview() -> dict:
    """Quick Thor status for overview grid."""
    try:
        status_file = THOR_DATA / "status.json"
        state = "offline"
        if status_file.exists():
            data = json.loads(status_file.read_text())
            state = data.get("state", "offline")

        pending = completed = 0
        tasks_dir = THOR_DATA / "tasks"
        if tasks_dir.exists():
            for f in tasks_dir.glob("task_*.json"):
                try:
                    td = json.loads(f.read_text())
                    if td.get("status") == "pending":
                        pending += 1
                    elif td.get("status") == "completed":
                        completed += 1
                except Exception:
                    pass

        return {"state": state, "pending": pending, "completed": completed}
    except Exception:
        return {"state": "offline", "pending": 0, "completed": 0}


def _get_quant_overview() -> dict:
    """Quick Quant status for overview grid."""
    try:
        status_file = DATA_DIR / "quant_status.json"
        if status_file.exists():
            data = json.loads(status_file.read_text())
            results_file = DATA_DIR / "quant_results.json"
            best_wr = 0
            if results_file.exists():
                rdata = json.loads(results_file.read_text())
                top = rdata.get("top_results", [])
                if top:
                    best_wr = top[0].get("win_rate", 0)
            return {
                "running": data.get("running", False),
                "total_combos_tested": data.get("total_combos_tested", 0),
                "best_win_rate": best_wr,
            }
    except Exception:
        pass
    return {"running": False, "total_combos_tested": 0, "best_win_rate": 0}


@overview_bp.route("/api/overview")
def api_overview():
    """High-level status of all agents."""
    # Garves
    trades = _load_trades()
    resolved = [t for t in trades if t.get("resolved") and t.get("outcome") not in ("unknown", None)]
    wins = sum(1 for t in resolved if t.get("won"))
    garves_wr = (wins / len(resolved) * 100) if resolved else 0
    garves_running = False
    try:
        result = subprocess.run(["pgrep", "-f", "bot.main"], capture_output=True, text=True)
        garves_running = bool(result.stdout.strip())
    except Exception:
        pass

    # Indicator accuracy
    accuracy_data = {}
    if INDICATOR_ACCURACY_FILE.exists():
        try:
            with open(INDICATOR_ACCURACY_FILE) as f:
                accuracy_data = json.load(f)
        except Exception:
            pass

    # Soren
    soren_queue = 0
    soren_posted = 0
    if SOREN_QUEUE_FILE.exists():
        try:
            with open(SOREN_QUEUE_FILE) as f:
                q = json.load(f)
                soren_queue = sum(1 for x in q if x.get("status") == "pending")
                soren_posted = sum(1 for x in q if x.get("status") == "posted")
        except Exception:
            pass

    # Shelby
    shelby_running = False
    try:
        result = subprocess.run(["pgrep", "-f", "app.py"], capture_output=True, text=True)
        shelby_running = bool(result.stdout.strip())
    except Exception:
        pass

    # Mercury review stats
    mercury_review_avg = None
    mercury_total_posts = 0
    if MERCURY_POSTING_LOG.exists():
        try:
            with open(MERCURY_POSTING_LOG) as f:
                mlog = json.load(f)
            mercury_total_posts = len(mlog)
            reviewed = [p for p in mlog if p.get("review_score") is not None and p.get("review_score", -1) != -1]
            if reviewed:
                mercury_review_avg = round(sum(p["review_score"] for p in reviewed) / len(reviewed), 1)
        except Exception:
            pass

    # Hawk
    hawk_status = {}
    hawk_status_file = DATA_DIR / "hawk_status.json"
    if hawk_status_file.exists():
        try:
            hawk_status = json.loads(hawk_status_file.read_text())
        except Exception:
            pass

    # Viper
    viper_status = {}
    viper_status_file = DATA_DIR / "viper_status.json"
    if viper_status_file.exists():
        try:
            viper_status = json.loads(viper_status_file.read_text())
        except Exception:
            pass

    return jsonify({
        "garves": {
            "running": garves_running,
            "win_rate": round(garves_wr, 1),
            "total_trades": len(trades),
            "resolved": len(resolved),
            "wins": wins,
            "losses": len(resolved) - wins,
            "pending": len(trades) - len([t for t in trades if t.get("resolved")]),
            "indicator_accuracy": accuracy_data,
        },
        "soren": {
            "queue_pending": soren_queue,
            "total_posted": soren_posted,
        },
        "shelby": {
            "running": shelby_running,
        },
        "lisa": {
            "total_posts": mercury_total_posts,
            "review_avg": mercury_review_avg,
        },
        "thor": _get_thor_overview(),
        "hawk": {
            "running": hawk_status.get("running", False),
            "win_rate": hawk_status.get("win_rate", 0),
            "open_bets": hawk_status.get("open_positions", 0),
        },
        "viper": {
            "running": viper_status.get("running", False),
            "opportunities": viper_status.get("total_found", 0),
            "pushed": viper_status.get("pushed_to_shelby", 0),
        },
        "quant": _get_quant_overview(),
    })


@overview_bp.route("/api/agent/<agent>/kpis")
def api_agent_kpis(agent):
    """Per-agent KPIs."""
    agent = agent.lower()
    kpis = {}

    if agent == "garves":
        trades = _load_trades()
        resolved = [t for t in trades if t.get("resolved") and t.get("outcome") not in ("unknown", None)]
        wins = sum(1 for t in resolved if t.get("won"))
        wr = (wins / len(resolved) * 100) if resolved else 0
        stake = float(os.getenv("ORDER_SIZE_USD", "5.0"))
        pnl = 0.0
        for t in resolved:
            implied = t.get("implied_up_price", 0.5)
            d = t.get("direction", "up")
            ep = implied if d == "up" else (1 - implied)
            if t.get("won"):
                pnl += stake * (1 - ep) - stake * 0.02
            else:
                pnl += -stake * ep
        # Trades per day
        if trades:
            first_ts = min(t.get("timestamp", time.time()) for t in trades)
            days = max(1, (time.time() - first_ts) / 86400)
            trades_per_day = round(len(trades) / days, 1)
        else:
            trades_per_day = 0
        # Best timeframe
        by_tf = {}
        for t in resolved:
            tf = t.get("timeframe", "?")
            if tf not in by_tf:
                by_tf[tf] = {"w": 0, "l": 0}
            if t.get("won"):
                by_tf[tf]["w"] += 1
            else:
                by_tf[tf]["l"] += 1
        best_tf = "N/A"
        best_wr = 0
        for tf, d in by_tf.items():
            total = d["w"] + d["l"]
            if total >= 3:
                tfwr = d["w"] / total * 100
                if tfwr > best_wr:
                    best_wr = tfwr
                    best_tf = tf
        avg_edge = 0
        if trades:
            avg_edge = sum(t.get("edge", 0) for t in trades) / len(trades)
        kpis = {
            "win_rate": round(wr, 1),
            "pnl": round(pnl, 2),
            "trades_per_day": trades_per_day,
            "avg_edge": round(avg_edge * 100, 2),
            "best_timeframe": best_tf,
            "total_trades": len(trades),
            "resolved": len(resolved),
        }
    elif agent == "soren":
        if SOREN_QUEUE_FILE.exists():
            try:
                with open(SOREN_QUEUE_FILE) as f:
                    queue = json.load(f)
                total = len(queue)
                posted = sum(1 for q in queue if q.get("status") == "posted")
                approved = sum(1 for q in queue if q.get("status") == "approved")
                pending = sum(1 for q in queue if q.get("status") == "pending")
                by_pillar = {}
                for q in queue:
                    p = q.get("pillar", "unknown")
                    by_pillar[p] = by_pillar.get(p, 0) + 1
                kpis = {
                    "content_produced": total,
                    "approval_rate": round(((posted + approved) / total * 100) if total else 0, 1),
                    "pending": pending,
                    "posted": posted,
                    "pillar_distribution": by_pillar,
                }
            except Exception:
                kpis = {"content_produced": 0}
        else:
            kpis = {"content_produced": 0}
    elif agent == "atlas":
        atlas_status_file = ATLAS_ROOT / "data" / "background_status.json"
        kb_file = ATLAS_ROOT / "data" / "knowledge_base.json"
        cycles = 0
        obs = 0
        improvements = 0
        if atlas_status_file.exists():
            try:
                with open(atlas_status_file) as f:
                    bg = json.load(f)
                cycles = bg.get("cycles", 0)
            except Exception:
                pass
        if kb_file.exists():
            try:
                with open(kb_file) as f:
                    kb = json.load(f)
                obs = len(kb.get("observations", []))
                improvements = len(kb.get("improvements", []))
            except Exception:
                pass
        kpis = {
            "cycles_total": cycles,
            "observations": obs,
            "improvements": improvements,
            "obs_per_cycle": round(obs / max(1, cycles), 1),
        }
    elif agent == "lisa":
        total_posts = 0
        review_kpis = {}
        if MERCURY_POSTING_LOG.exists():
            try:
                with open(MERCURY_POSTING_LOG) as f:
                    posts = json.load(f)
                total_posts = len(posts)
                reviewed = [p for p in posts if p.get("review_score") is not None and p.get("review_score", -1) != -1]
                if reviewed:
                    scores = [p["review_score"] for p in reviewed]
                    review_kpis = {
                        "total_reviewed": len(reviewed),
                        "avg_score": str(round(sum(scores) / len(scores), 1)) + "/10",
                        "passed": sum(1 for s in scores if s >= 7),
                        "warned": sum(1 for s in scores if 4 <= s < 7),
                        "failed": sum(1 for s in scores if s < 4),
                        "pass_rate": str(round(sum(1 for s in scores if s >= 7) / len(scores) * 100, 1)) + "%",
                    }
            except Exception:
                pass
        # Platform breakdown
        platform_dist = {}
        if MERCURY_POSTING_LOG.exists():
            try:
                with open(MERCURY_POSTING_LOG) as f:
                    posts = json.load(f)
                for p in posts:
                    plat = p.get("platform", "unknown")
                    platform_dist[plat] = platform_dist.get(plat, 0) + 1
            except Exception:
                pass
        kpis = {
            "total_posts": total_posts,
            "mode": "semi-auto",
        }
        if review_kpis:
            kpis["brand_review"] = review_kpis
        if platform_dist:
            kpis["platform_distribution"] = platform_dist
    elif agent == "sentinel":
        try:
            from bot.routes.sentinel import _get_sentinel
            s = _get_sentinel()
            status = s.get_status()
            kpis = {
                "agents_online": status.get("agents_online", 0),
                "total_scans": status.get("total_scans", 0),
                "issues_detected": status.get("active_issues", 0),
                "auto_fixes": status.get("total_fixes", 0),
            }
        except Exception:
            kpis = {"agents_online": 0, "total_scans": 0}

    return jsonify(kpis)


@overview_bp.route("/api/intelligence")
def api_intelligence():
    """Intelligence meter for all agents -- 5 dimensions each, 0-100 scale."""
    ET_tz = ZoneInfo("America/New_York")

    result = {}

    # -- GARVES -- The Trader --
    try:
        garves = {"dimensions": {}, "overall": 0, "title": "The Trader"}
        trades_file = DATA_DIR / "trades.jsonl"
        trades = []
        if trades_file.exists():
            with open(trades_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        trades.append(json.loads(line))

        resolved = [t for t in trades if t.get("resolved")]
        wins = sum(1 for t in resolved if t.get("won"))
        wr = (wins / len(resolved) * 100) if resolved else 0
        total = len(trades)

        # Merge in historical data from daily reports for cumulative intelligence
        daily_reports_file = DATA_DIR / "daily_reports.json"
        hist_total = 0
        hist_resolved = 0
        hist_wins = 0
        hist_days = 0
        hist_assets = set()
        hist_timeframes = set()
        hist_avg_edge = 0
        if daily_reports_file.exists():
            try:
                with open(daily_reports_file) as f:
                    daily_reports = json.load(f)
                for dr in daily_reports:
                    s = dr.get("summary", {})
                    hist_total += s.get("total_trades", 0)
                    hist_resolved += s.get("resolved", 0)
                    hist_wins += s.get("wins", 0)
                    hist_days += 1
                    hist_avg_edge += s.get("avg_edge", 0)
                    for a in dr.get("by_asset", {}):
                        hist_assets.add(a)
                    for tf in dr.get("by_timeframe", {}):
                        hist_timeframes.add(tf)
                if hist_days > 0:
                    hist_avg_edge /= hist_days
            except Exception:
                pass

        # Combined stats (today + history)
        combined_total = total + hist_total
        combined_resolved = len(resolved) + hist_resolved
        combined_wins = wins + hist_wins
        combined_wr = (combined_wins / combined_resolved * 100) if combined_resolved > 0 else 0
        all_assets = set(t.get("asset", "") for t in trades) | hist_assets
        all_timeframes = set(t.get("timeframe", "") for t in trades) | hist_timeframes

        # 1. Market Knowledge (indicators, regime, assets — capabilities don't reset)
        indicators_file = DATA_DIR / "indicator_accuracy.json"
        indicator_count = 0
        if indicators_file.exists():
            with open(indicators_file) as f:
                ind = json.load(f)
            indicator_count = len(ind)
        regime_file = DATA_DIR / "regime_state.json"
        has_regime = regime_file.exists()
        knowledge = min(100, indicator_count * 6 + (20 if has_regime else 0) + len(all_assets) * 10)
        garves["dimensions"]["Market Knowledge"] = knowledge

        # 2. Accuracy (from combined win rate — knowledge carries over)
        accuracy = min(100, int(combined_wr * 1.3)) if combined_resolved else 15
        garves["dimensions"]["Accuracy"] = accuracy

        # 3. Experience (cumulative trades across all days)
        experience = min(100, int(combined_total * 0.5)) if combined_total else 5
        garves["dimensions"]["Experience"] = experience

        # 4. Risk Management
        straddle_file = DATA_DIR / "straddle_trades.json"
        has_straddle = straddle_file.exists()
        avg_edge = 0
        if resolved:
            edges = [t.get("edge", 0) for t in resolved if t.get("edge")]
            avg_edge = sum(edges) / len(edges) if edges else 0
        elif hist_avg_edge > 0:
            avg_edge = hist_avg_edge / 100  # stored as percentage
        # ConvictionEngine and daily cycle add to risk management
        has_conviction = (DATA_DIR.parent / "bot" / "conviction.py").exists()
        has_daily_cycle = (DATA_DIR.parent / "bot" / "daily_cycle.py").exists()
        risk = 40 + (20 if has_straddle else 0) + min(20, int(avg_edge * 200))
        risk += (10 if has_conviction else 0) + (10 if has_daily_cycle else 0)
        garves["dimensions"]["Risk Management"] = min(100, risk)

        # 5. Adaptability (assets, timeframes, regime — capabilities persist)
        adaptability = min(100, len(all_assets) * 15 + len(all_timeframes) * 15 + (25 if has_regime else 0))
        garves["dimensions"]["Adaptability"] = adaptability

        scores = list(garves["dimensions"].values())
        garves["overall"] = int(sum(scores) / len(scores)) if scores else 0
        result["garves"] = garves
    except Exception:
        result["garves"] = {"dimensions": {}, "overall": 0, "title": "The Trader"}

    # -- SOREN -- The Thinker --
    try:
        soren = {"dimensions": {}, "overall": 0, "title": "The Thinker"}
        queue_file = Path.home() / "soren-content" / "data" / "content_queue.json"
        queue = []
        if queue_file.exists():
            with open(queue_file) as f:
                queue = json.load(f)

        posted = [i for i in queue if i.get("status") == "posted"]
        pending = [i for i in queue if i.get("status") == "pending"]
        approved = [i for i in queue if i.get("status") == "approved"]
        pillars = set(i.get("pillar", "") for i in queue if i.get("pillar"))
        platforms = set(i.get("platform", "") for i in queue if i.get("platform"))

        # 1. Creativity
        creativity = min(100, len(pillars) * 15 + len(queue) * 2)
        soren["dimensions"]["Creativity"] = creativity

        # 2. Productivity
        productivity = min(100, len(posted) * 10 + len(approved) * 5 + len(pending) * 2)
        soren["dimensions"]["Productivity"] = productivity

        # 3. Brand Consistency
        ab_file = Path.home() / "soren-content" / "data" / "ab_results.json"
        has_ab = ab_file.exists()
        consistency = 55 + (15 if has_ab else 0) + min(30, len(pillars) * 6)
        soren["dimensions"]["Brand Voice"] = min(100, consistency)

        # 4. Platform Awareness
        platform_score = min(100, len(platforms) * 25 + 20)
        soren["dimensions"]["Platform Reach"] = platform_score

        # 5. Trend Awareness
        trend_file = Path.home() / "soren-content" / "data" / "trend_topics.json"
        trends_count = 0
        if trend_file.exists():
            with open(trend_file) as f:
                trends_count = len(json.load(f))
        trend_score = min(100, 30 + trends_count * 5)
        soren["dimensions"]["Trend Awareness"] = trend_score

        scores = list(soren["dimensions"].values())
        soren["overall"] = int(sum(scores) / len(scores)) if scores else 0
        result["soren"] = soren
    except Exception:
        result["soren"] = {"dimensions": {}, "overall": 0, "title": "The Thinker"}

    # -- ATLAS -- The Scientist --
    try:
        atlas_intel = {"dimensions": {}, "overall": 0, "title": "The Scientist"}
        atlas = get_atlas()
        kb_stats = atlas.kb.stats() if atlas else {}
        bg = atlas.background.get_status() if atlas else {}
        research = atlas.researcher.get_research_stats() if atlas else {}

        obs = kb_stats.get("total_observations", 0)
        learnings = kb_stats.get("total_learnings", 0)
        cycles = bg.get("cycles", 0)
        quality = research.get("avg_quality_score", 0)
        seen = research.get("seen_urls", 0)

        # 1. Knowledge Depth
        depth = min(100, int(obs * 0.2 + learnings * 1.5))
        atlas_intel["dimensions"]["Knowledge Depth"] = depth

        # 2. Research Quality
        rq = min(100, int(quality * 10)) if quality else 20
        atlas_intel["dimensions"]["Research Quality"] = rq

        # 3. Learning Rate
        lr = min(100, int((learnings / max(cycles, 1)) * 25))
        atlas_intel["dimensions"]["Learning Rate"] = lr

        # 4. Coverage
        agents_covered = len(kb_stats.get("agents_observed", []))
        coverage = min(100, agents_covered * 15 + min(40, seen))
        atlas_intel["dimensions"]["Agent Coverage"] = coverage

        # 5. Synthesis
        exp_stats = atlas.hypothesis.stats() if atlas else {}
        improvements_file = Path.home() / "atlas" / "data" / "improvements.json"
        imp_count = 0
        if improvements_file.exists():
            with open(improvements_file) as f:
                imp_data = json.load(f)
            imp_count = sum(len(v) for v in imp_data.values() if isinstance(v, list))
        synthesis = min(100, exp_stats.get("completed", 0) * 10 + imp_count * 3 + 20)
        atlas_intel["dimensions"]["Synthesis"] = synthesis

        scores = list(atlas_intel["dimensions"].values())
        atlas_intel["overall"] = int(sum(scores) / len(scores)) if scores else 0
        result["atlas"] = atlas_intel
    except Exception:
        result["atlas"] = {"dimensions": {}, "overall": 0, "title": "The Scientist"}

    # -- SHELBY -- The Commander --
    try:
        shelby = {"dimensions": {}, "overall": 0, "title": "The Commander"}

        # 1. Team Awareness
        assess_file = SHELBY_ROOT_DIR / "data" / "agent_assessments.json"
        assessments = {}
        if assess_file.exists():
            with open(assess_file) as f:
                assessments = json.load(f)
        awareness = min(100, len(assessments) * 15 + 25)
        shelby["dimensions"]["Team Awareness"] = awareness

        # 2. Task Management
        tasks_file = SHELBY_ROOT_DIR / "data" / "tasks.json"
        task_completion = 50
        if tasks_file.exists():
            with open(tasks_file) as f:
                tasks = json.load(f)
            done = sum(1 for t in tasks if t.get("done") or t.get("status") == "done")
            total = len(tasks)
            if total > 0:
                task_completion = min(100, int(done / total * 100) + 10)
        shelby["dimensions"]["Task Management"] = task_completion

        # 3. Communication
        bc_file = SHELBY_ROOT_DIR / "data" / "broadcasts.json"
        bc_count = 0
        if bc_file.exists():
            with open(bc_file) as f:
                bc_count = len(json.load(f))
        comm = min(100, 40 + bc_count * 5)
        shelby["dimensions"]["Communication"] = comm

        # 4. Scheduling
        sched_file = SHELBY_ROOT_DIR / "data" / "scheduler_log.json"
        sched_count = 0
        if sched_file.exists():
            with open(sched_file) as f:
                sched_count = len(json.load(f))
        scheduling = min(100, 35 + sched_count * 3)
        shelby["dimensions"]["Scheduling"] = scheduling

        # 5. Decision Quality
        avg_score = 0
        if assessments:
            agent_scores = [a.get("score", 0) for a in assessments.values()]
            avg_score = sum(agent_scores) / len(agent_scores) if agent_scores else 0
        decision = min(100, int(avg_score * 1.2) + 10)
        shelby["dimensions"]["Decision Quality"] = decision

        scores = list(shelby["dimensions"].values())
        shelby["overall"] = int(sum(scores) / len(scores)) if scores else 0
        result["shelby"] = shelby
    except Exception:
        result["shelby"] = {"dimensions": {}, "overall": 0, "title": "The Commander"}

    # -- LISA -- The Operator --
    try:
        lisa = {"dimensions": {}, "overall": 0, "title": "The Operator"}
        mercury_root = Path.home() / "mercury"

        # 1. Platform Knowledge
        brain_file = mercury_root / "data" / "brain.json"
        insights_count = 0
        if brain_file.exists():
            with open(brain_file) as f:
                brain = json.load(f)
            insights_count = len(brain.get("insights", []))
        platform_knowledge = min(100, 25 + insights_count * 3)
        lisa["dimensions"]["Platform Knowledge"] = platform_knowledge

        # 2. Posting Discipline
        posting_log = mercury_root / "data" / "posting_log.json"
        post_count = 0
        if posting_log.exists():
            with open(posting_log) as f:
                post_count = len(json.load(f))
        discipline = min(100, 20 + post_count * 5)
        lisa["dimensions"]["Posting Discipline"] = discipline

        # 3. Brand Alignment
        review_file = mercury_root / "data" / "brand_reviews.json"
        avg_review = 0
        if review_file.exists():
            with open(review_file) as f:
                reviews = json.load(f)
            if reviews:
                scores_list = [r.get("score", 0) for r in reviews if r.get("score")]
                avg_review = sum(scores_list) / len(scores_list) if scores_list else 0
        brand = min(100, int(avg_review * 10) + 20) if avg_review else 40
        lisa["dimensions"]["Brand Alignment"] = brand

        # 4. Strategy Depth
        plan_file = mercury_root / "data" / "strategy_plan.json"
        plan_depth = 30
        if plan_file.exists():
            with open(plan_file) as f:
                plan = json.load(f)
            plan_depth = min(100, 30 + len(str(plan)) // 100)
        lisa["dimensions"]["Strategy Depth"] = plan_depth

        # 5. Engagement IQ
        reply_file = mercury_root / "data" / "reply_templates.json"
        reply_count = 0
        if reply_file.exists():
            with open(reply_file) as f:
                reply_count = len(json.load(f))
        engagement = min(100, 25 + reply_count * 5 + insights_count * 2)
        lisa["dimensions"]["Engagement IQ"] = engagement

        scores = list(lisa["dimensions"].values())
        lisa["overall"] = int(sum(scores) / len(scores)) if scores else 0
        result["lisa"] = lisa
    except Exception:
        result["lisa"] = {"dimensions": {}, "overall": 0, "title": "The Operator"}

    # -- ROBOTOX -- The Watchman --
    try:
        robotox = {"dimensions": {}, "overall": 0, "title": "The Watchman"}
        sentinel_root = Path.home() / "sentinel"

        # 1. Detection
        scan_file = sentinel_root / "data" / "scan_results.json"
        total_issues = 0
        scan_count = 0
        if scan_file.exists():
            with open(scan_file) as f:
                scans = json.load(f)
            if isinstance(scans, list):
                scan_count = len(scans)
                total_issues = sum(s.get("issues_found", s.get("issues", 0)) for s in scans)
        detection = min(100, 40 + scan_count * 3 + total_issues * 2)
        robotox["dimensions"]["Detection"] = detection

        # 2. Auto-Fix
        fix_file = sentinel_root / "data" / "fix_log.json"
        fix_count = 0
        if fix_file.exists():
            with open(fix_file) as f:
                fix_count = len(json.load(f))
        autofix = min(100, 30 + fix_count * 8)
        robotox["dimensions"]["Auto-Fix"] = autofix

        # 3. Vigilance
        vigilance = min(100, 35 + scan_count * 5)
        robotox["dimensions"]["Vigilance"] = vigilance

        # 4. Coverage
        config_file = sentinel_root / "config.json"
        agents_monitored = 5  # default
        if config_file.exists():
            with open(config_file) as f:
                cfg = json.load(f)
            agents_monitored = len(cfg.get("agents", {}))
        coverage = min(100, agents_monitored * 15 + 20)
        robotox["dimensions"]["Coverage"] = coverage

        # 5. Response Speed
        response = 50 + min(50, fix_count * 5)
        robotox["dimensions"]["Response Speed"] = min(100, response)

        scores = list(robotox["dimensions"].values())
        robotox["overall"] = int(sum(scores) / len(scores)) if scores else 0
        result["robotox"] = robotox
    except Exception:
        result["robotox"] = {"dimensions": {}, "overall": 0, "title": "The Watchman"}

    # -- THOR -- The Engineer --
    try:
        thor_intel = {"dimensions": {}, "overall": 0, "title": "The Engineer"}
        thor_data_dir = Path.home() / "thor" / "data"

        # Gather task stats
        tasks_dir = thor_data_dir / "tasks"
        results_dir = thor_data_dir / "results"
        pending = completed = failed = in_progress = total_tasks = 0
        agents_worked_on = set()
        total_retries = 0
        if tasks_dir.exists():
            for f in tasks_dir.glob("task_*.json"):
                try:
                    td = json.loads(f.read_text())
                    total_tasks += 1
                    s = td.get("status", "")
                    if s == "pending":
                        pending += 1
                    elif s == "completed":
                        completed += 1
                    elif s == "failed":
                        failed += 1
                    elif s == "in_progress":
                        in_progress += 1
                    ag = td.get("agent", "")
                    if ag:
                        agents_worked_on.add(ag)
                    total_retries += td.get("retries", 0)
                except Exception:
                    pass

        # Knowledge entries
        kb_index = thor_data_dir / "knowledge" / "index.json"
        kb_entries = 0
        if kb_index.exists():
            try:
                kb_entries = len(json.loads(kb_index.read_text()))
            except Exception:
                pass

        # Activity log for token/model stats
        activity_file = thor_data_dir / "activity.json"
        sonnet_uses = opus_uses = 0
        total_tokens = 0
        test_passes = test_total = 0
        if activity_file.exists():
            try:
                activities = json.loads(activity_file.read_text())
                for a in activities:
                    total_tokens += a.get("tokens", 0)
                    model = a.get("model", "")
                    if "sonnet" in model:
                        sonnet_uses += 1
                    elif "opus" in model:
                        opus_uses += 1
                    if a.get("test_passed") is True:
                        test_passes += 1
                        test_total += 1
                    elif a.get("test_passed") is False:
                        test_total += 1
            except Exception:
                pass

        # Results for test pass rate
        if results_dir.exists():
            for f in results_dir.glob("result_*.json"):
                try:
                    rd = json.loads(f.read_text())
                    if rd.get("test_passed") is True:
                        test_passes += 1
                        test_total += 1
                    elif rd.get("test_passed") is False:
                        test_total += 1
                except Exception:
                    pass

        # 1. Code Quality — task completion rate + test pass rate
        completion_rate = (completed / max(1, completed + failed)) * 100 if (completed + failed) > 0 else 0
        test_rate = (test_passes / max(1, test_total)) * 100 if test_total > 0 else 0
        code_quality = min(100, int(completion_rate * 0.5 + test_rate * 0.3 + 20))
        thor_intel["dimensions"]["Code Quality"] = code_quality

        # 2. Knowledge Depth — based on KB entries
        knowledge_depth = min(100, 15 + kb_entries * 4)
        thor_intel["dimensions"]["Knowledge Depth"] = knowledge_depth

        # 3. Task Execution — completed tasks, retries, volume
        task_exec = min(100, completed * 5 + max(0, 30 - total_retries * 3) + 10)
        thor_intel["dimensions"]["Task Execution"] = task_exec

        # 4. System Coverage — how many agents Thor has worked on (7 total possible)
        system_coverage = min(100, len(agents_worked_on) * 14 + 15)
        thor_intel["dimensions"]["System Coverage"] = system_coverage

        # 5. Efficiency — Sonnet vs Opus ratio (more Sonnet = more efficient)
        total_model_uses = sonnet_uses + opus_uses
        if total_model_uses > 0:
            sonnet_ratio = sonnet_uses / total_model_uses
            efficiency = min(100, int(sonnet_ratio * 60 + 30))
        else:
            efficiency = 40
        thor_intel["dimensions"]["Efficiency"] = efficiency

        scores = list(thor_intel["dimensions"].values())
        thor_intel["overall"] = int(sum(scores) / len(scores)) if scores else 0
        result["thor"] = thor_intel
    except Exception:
        result["thor"] = {"dimensions": {}, "overall": 0, "title": "The Engineer"}

    # -- HAWK -- The Market Predator --
    try:
        hawk_intel = {"dimensions": {}, "overall": 0, "title": "The Market Predator"}
        hawk_trades_file = DATA_DIR / "hawk_trades.jsonl"
        hawk_opps_file = DATA_DIR / "hawk_opportunities.json"
        hawk_status_file = DATA_DIR / "hawk_status.json"

        hawk_trades = []
        if hawk_trades_file.exists():
            with open(hawk_trades_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        hawk_trades.append(json.loads(line))

        hawk_resolved = [t for t in hawk_trades if t.get("resolved")]
        hawk_wins = sum(1 for t in hawk_resolved if t.get("won"))
        hawk_wr = (hawk_wins / len(hawk_resolved) * 100) if hawk_resolved else 0
        hawk_cats = set(t.get("category", "") for t in hawk_trades if t.get("category"))

        hawk_opps = []
        if hawk_opps_file.exists():
            try:
                with open(hawk_opps_file) as f:
                    hawk_opps = json.loads(f.read()).get("opportunities", [])
            except Exception:
                pass

        # 1. Market Scanning — categories covered + opportunities found
        scanning = min(100, len(hawk_cats) * 12 + len(hawk_opps) * 3 + 15)
        hawk_intel["dimensions"]["Market Scanning"] = scanning

        # 2. Edge Detection — average edge quality
        hawk_edges = [t.get("edge", 0) for t in hawk_trades if t.get("edge")]
        avg_edge = sum(hawk_edges) / len(hawk_edges) if hawk_edges else 0
        edge_detect = min(100, int(avg_edge * 300) + 20)
        hawk_intel["dimensions"]["Edge Detection"] = edge_detect

        # 3. Win Rate — trading accuracy
        accuracy = min(100, int(hawk_wr * 1.2)) if hawk_resolved else 15
        hawk_intel["dimensions"]["Accuracy"] = accuracy

        # 4. Experience — total trades + resolved
        experience = min(100, len(hawk_trades) * 2 + len(hawk_resolved) * 3 + 5)
        hawk_intel["dimensions"]["Experience"] = experience

        # 5. Category Breadth — how many market categories covered
        breadth = min(100, len(hawk_cats) * 15 + 20)
        hawk_intel["dimensions"]["Category Breadth"] = breadth

        scores = list(hawk_intel["dimensions"].values())
        hawk_intel["overall"] = int(sum(scores) / len(scores)) if scores else 0
        result["hawk"] = hawk_intel
    except Exception:
        result["hawk"] = {"dimensions": {}, "overall": 0, "title": "The Market Predator"}

    # -- VIPER -- The Opportunity Hunter --
    try:
        viper_intel = {"dimensions": {}, "overall": 0, "title": "The Opportunity Hunter"}
        viper_opps_file = DATA_DIR / "viper_opportunities.json"
        viper_costs_file = DATA_DIR / "viper_costs.json"
        viper_status_file = DATA_DIR / "viper_status.json"

        viper_status = {}
        if viper_status_file.exists():
            try:
                viper_status = json.loads(viper_status_file.read_text())
            except Exception:
                pass

        viper_opps = []
        if viper_opps_file.exists():
            try:
                viper_opps = json.loads(viper_opps_file.read_text()).get("opportunities", [])
            except Exception:
                pass

        viper_costs = []
        if viper_costs_file.exists():
            try:
                viper_costs = json.loads(viper_costs_file.read_text()).get("costs", [])
            except Exception:
                pass

        # 1. Opportunity Discovery — opportunities found
        discovery = min(100, len(viper_opps) * 5 + 15)
        viper_intel["dimensions"]["Discovery"] = discovery

        # 2. Cost Intelligence — API cost tracking
        cost_intel = min(100, len(viper_costs) * 10 + 20)
        viper_intel["dimensions"]["Cost Intelligence"] = cost_intel

        # 3. Revenue Potential — score-weighted intel value
        total_value = sum(o.get("score", o.get("value", 0)) for o in viper_opps)
        revenue = min(100, int(total_value / 10) + 15) if total_value else 15
        viper_intel["dimensions"]["Revenue Potential"] = revenue

        # 4. Push Rate — opportunities pushed to Shelby
        pushed = viper_status.get("pushed_to_shelby", viper_status.get("pushes", 0))
        # Also count from dedup file if status hasn't been updated yet
        pushed_file = DATA_DIR / "viper_pushed.json"
        if pushed == 0 and pushed_file.exists():
            try:
                pushed = len(json.loads(pushed_file.read_text()))
            except Exception:
                pass
        push_rate = min(100, pushed * 8 + 10)
        viper_intel["dimensions"]["Push Rate"] = push_rate

        # 5. Monetization IQ — Soren metrics/opportunities awareness
        has_soren_metrics = (DATA_DIR / "viper_soren_metrics.json").exists() or viper_status.get("soren_metrics_ready", False)
        # Soren opportunities file also counts as monetization awareness
        if not has_soren_metrics and (DATA_DIR / "soren_opportunities.json").exists():
            try:
                so = json.loads((DATA_DIR / "soren_opportunities.json").read_text())
                if so.get("count", 0) > 0 or len(so.get("opportunities", [])) > 0:
                    has_soren_metrics = True
            except Exception:
                pass
        monetization = 30 + (30 if has_soren_metrics else 0) + min(40, len(viper_opps) * 3)
        viper_intel["dimensions"]["Monetization IQ"] = min(100, monetization)

        scores = list(viper_intel["dimensions"].values())
        viper_intel["overall"] = int(sum(scores) / len(scores)) if scores else 0
        result["viper"] = viper_intel
    except Exception:
        result["viper"] = {"dimensions": {}, "overall": 0, "title": "The Opportunity Hunter"}

    # -- QUANT -- The Strategy Alchemist --
    try:
        quant_intel = {"dimensions": {}, "overall": 0, "title": "The Strategy Alchemist"}
        quant_status_file = DATA_DIR / "quant_status.json"
        quant_results_file = DATA_DIR / "quant_results.json"
        quant_wf_file = DATA_DIR / "quant_walk_forward.json"
        quant_analytics_file = DATA_DIR / "quant_analytics.json"

        q_status = {}
        if quant_status_file.exists():
            q_status = json.loads(quant_status_file.read_text())
        q_results = {}
        if quant_results_file.exists():
            q_results = json.loads(quant_results_file.read_text())
        q_wf = {}
        if quant_wf_file.exists():
            q_wf = json.loads(quant_wf_file.read_text())
        q_analytics = {}
        if quant_analytics_file.exists():
            q_analytics = json.loads(quant_analytics_file.read_text())

        combos_tested = q_status.get("total_combos_tested", 0)
        top_results = q_results.get("top_results", [])
        best_wr = top_results[0].get("win_rate", 0) if top_results else 0

        # 1. Backtesting Volume — how many combos tested
        bt_volume = min(100, int(combos_tested * 0.02) + 10) if combos_tested else 10
        quant_intel["dimensions"]["Backtesting Volume"] = bt_volume

        # 2. Statistical Rigor — walk-forward validation + confidence intervals
        wf_data = q_wf.get("walk_forward", {})
        ci_data = q_wf.get("confidence_interval", {})
        wf_folds = len(wf_data.get("folds", []))
        has_ci = bool(ci_data.get("lower"))
        rigor = min(100, 15 + wf_folds * 12 + (25 if has_ci else 0) + min(20, len(top_results) * 2))
        quant_intel["dimensions"]["Statistical Rigor"] = rigor

        # 3. Walk-Forward Accuracy — OOS win rate quality
        wf_oos = wf_data.get("avg_oos_wr", 0)
        wf_accuracy = min(100, int(wf_oos * 1.4)) if wf_oos else 15
        quant_intel["dimensions"]["Walk-Forward Accuracy"] = wf_accuracy

        # 4. Indicator Diversity — diversity score from analytics
        diversity = q_analytics.get("diversity", {})
        div_score = diversity.get("diversity_score", 0)
        indicator_div = min(100, int(div_score * 100) + 15) if div_score else 20
        quant_intel["dimensions"]["Indicator Diversity"] = indicator_div

        # 5. Strategy Optimization — improvement found + best WR
        improvement = q_results.get("improvement", 0) if q_results else 0
        opt_score = min(100, 15 + int(best_wr * 0.8) + int(improvement * 5))
        quant_intel["dimensions"]["Strategy Optimization"] = opt_score

        scores = list(quant_intel["dimensions"].values())
        quant_intel["overall"] = int(sum(scores) / len(scores)) if scores else 0
        result["quant"] = quant_intel
    except Exception:
        result["quant"] = {"dimensions": {}, "overall": 0, "title": "The Strategy Alchemist"}

    # -- TEAM -- Collective Intelligence --
    try:
        team = {"dimensions": {}, "overall": 0, "title": "Brotherhood"}
        agents = ["garves", "soren", "atlas", "shelby", "lisa", "robotox", "thor", "hawk", "viper", "quant"]
        agent_scores = {a: result.get(a, {}).get("overall", 0) for a in agents}

        # 1. Collective Knowledge
        atlas_depth = result.get("atlas", {}).get("dimensions", {}).get("Knowledge Depth", 0)
        garves_know = result.get("garves", {}).get("dimensions", {}).get("Market Knowledge", 0)
        soren_brand = result.get("soren", {}).get("dimensions", {}).get("Brand Voice", 0)
        shelby_aware = result.get("shelby", {}).get("dimensions", {}).get("Team Awareness", 0)
        lisa_plat = result.get("lisa", {}).get("dimensions", {}).get("Platform Knowledge", 0)
        robotox_cov = result.get("robotox", {}).get("dimensions", {}).get("Coverage", 0)
        thor_know = result.get("thor", {}).get("dimensions", {}).get("Knowledge Depth", 0)
        hawk_scan = result.get("hawk", {}).get("dimensions", {}).get("Market Scanning", 0)
        viper_disc = result.get("viper", {}).get("dimensions", {}).get("Discovery", 0)
        quant_rigor = result.get("quant", {}).get("dimensions", {}).get("Statistical Rigor", 0)
        collective_knowledge = int((atlas_depth * 0.18 + garves_know * 0.13 + soren_brand * 0.09 +
                                    shelby_aware * 0.09 + lisa_plat * 0.07 + robotox_cov * 0.07 +
                                    thor_know * 0.09 + hawk_scan * 0.09 + viper_disc * 0.08 +
                                    quant_rigor * 0.11))
        team["dimensions"]["Collective Knowledge"] = min(100, collective_knowledge)

        # 2. Coordination
        shelby_comm = result.get("shelby", {}).get("dimensions", {}).get("Communication", 0)
        shelby_sched = result.get("shelby", {}).get("dimensions", {}).get("Scheduling", 0)
        atlas_coverage = result.get("atlas", {}).get("dimensions", {}).get("Agent Coverage", 0)
        coordination = int((shelby_comm * 0.35 + shelby_sched * 0.25 + atlas_coverage * 0.4))
        team["dimensions"]["Coordination"] = min(100, coordination)

        # 3. Performance
        garves_acc = result.get("garves", {}).get("dimensions", {}).get("Accuracy", 0)
        soren_prod = result.get("soren", {}).get("dimensions", {}).get("Productivity", 0)
        atlas_quality = result.get("atlas", {}).get("dimensions", {}).get("Research Quality", 0)
        shelby_task = result.get("shelby", {}).get("dimensions", {}).get("Task Management", 0)
        lisa_disc = result.get("lisa", {}).get("dimensions", {}).get("Posting Discipline", 0)
        robotox_det = result.get("robotox", {}).get("dimensions", {}).get("Detection", 0)
        thor_exec = result.get("thor", {}).get("dimensions", {}).get("Task Execution", 0)
        hawk_acc = result.get("hawk", {}).get("dimensions", {}).get("Accuracy", 0)
        viper_rev = result.get("viper", {}).get("dimensions", {}).get("Revenue Potential", 0)
        quant_wf_acc = result.get("quant", {}).get("dimensions", {}).get("Walk-Forward Accuracy", 0)
        performance = int((garves_acc + soren_prod + atlas_quality + shelby_task + lisa_disc + robotox_det + thor_exec + hawk_acc + viper_rev + quant_wf_acc) / 10)
        team["dimensions"]["Performance"] = min(100, performance)

        # 4. Autonomy
        robotox_fix = result.get("robotox", {}).get("dimensions", {}).get("Auto-Fix", 0)
        atlas_synth = result.get("atlas", {}).get("dimensions", {}).get("Synthesis", 0)
        atlas_lr = result.get("atlas", {}).get("dimensions", {}).get("Learning Rate", 0)
        garves_risk = result.get("garves", {}).get("dimensions", {}).get("Risk Management", 0)
        thor_eff = result.get("thor", {}).get("dimensions", {}).get("Efficiency", 0)
        autonomy = int((robotox_fix * 0.2 + atlas_synth * 0.2 + atlas_lr * 0.2 + garves_risk * 0.2 + thor_eff * 0.2))
        team["dimensions"]["Autonomy"] = min(100, autonomy)

        # 5. Adaptability
        garves_adapt = result.get("garves", {}).get("dimensions", {}).get("Adaptability", 0)
        soren_trend = result.get("soren", {}).get("dimensions", {}).get("Trend Awareness", 0)
        lisa_strat = result.get("lisa", {}).get("dimensions", {}).get("Strategy Depth", 0)
        shelby_dec = result.get("shelby", {}).get("dimensions", {}).get("Decision Quality", 0)
        thor_coverage = result.get("thor", {}).get("dimensions", {}).get("System Coverage", 0)
        hawk_breadth = result.get("hawk", {}).get("dimensions", {}).get("Category Breadth", 0)
        viper_monetize = result.get("viper", {}).get("dimensions", {}).get("Monetization IQ", 0)
        quant_opt = result.get("quant", {}).get("dimensions", {}).get("Strategy Optimization", 0)
        adaptability = int((garves_adapt * 0.16 + soren_trend * 0.10 + lisa_strat * 0.10 +
                            shelby_dec * 0.16 + thor_coverage * 0.13 + hawk_breadth * 0.11 +
                            viper_monetize * 0.10 + quant_opt * 0.14))
        team["dimensions"]["Adaptability"] = min(100, adaptability)

        team_scores = list(team["dimensions"].values())
        team["overall"] = int(sum(team_scores) / len(team_scores)) if team_scores else 0
        team["agent_scores"] = agent_scores
        result["team"] = team
    except Exception:
        result["team"] = {"dimensions": {}, "overall": 0, "title": "Brotherhood", "agent_scores": {}}

    return jsonify(result)


@overview_bp.route("/api/broadcasts")
def api_broadcasts():
    """Recent broadcasts with acknowledgment status."""
    try:
        # Path already added via bot.shared.ensure_path
        from core.broadcast import get_recent_broadcasts, check_acknowledgments

        broadcasts = get_recent_broadcasts(limit=15)
        result = []
        for bc in reversed(broadcasts):
            bc_id = bc.get("id", "")
            acks = check_acknowledgments(bc_id) if bc_id else {}
            result.append({
                "id": bc_id,
                "message": bc.get("message", ""),
                "priority": bc.get("priority", "normal"),
                "from": bc.get("from", "shelby"),
                "timestamp": bc.get("timestamp", ""),
                "delivered_to": bc.get("delivered_to", []),
                "acked": acks.get("acked", []),
                "pending": acks.get("pending", []),
            })
        return jsonify({"broadcasts": result})
    except Exception as e:
        return jsonify({"error": str(e)[:200], "broadcasts": []}), 500
