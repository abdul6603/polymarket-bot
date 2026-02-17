"""Soren Monetization — track growth, estimate CPM, find brand opportunities."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from viper.config import ViperConfig

log = logging.getLogger(__name__)


def track_soren_growth(cfg: ViperConfig) -> dict:
    """Read from Lisa's analytics file to get Soren's social metrics."""
    analytics_file = cfg.mercury_analytics_file
    if not analytics_file.exists():
        return {"followers": 0, "engagement_rate": 0, "posts": 0, "platforms": {}}

    try:
        data = json.loads(analytics_file.read_text())
        return {
            "followers": data.get("total_followers", 0),
            "engagement_rate": data.get("avg_engagement_rate", 0),
            "posts": data.get("total_posts", 0),
            "platforms": data.get("by_platform", {}),
            "growth_rate": data.get("follower_growth_rate", 0),
        }
    except Exception:
        log.exception("Failed to read Soren analytics")
        return {"followers": 0, "engagement_rate": 0, "posts": 0, "platforms": {}}


def estimate_cpm(platform: str, followers: int, engagement_rate: float) -> float:
    """Estimate CPM (cost per 1000 impressions) based on platform and metrics."""
    base_cpm = {
        "tiktok": 0.50,
        "instagram": 2.00,
        "x": 1.00,
        "twitter": 1.00,
        "youtube": 4.00,
    }
    cpm = base_cpm.get(platform.lower(), 1.0)

    # Engagement multiplier
    if engagement_rate > 0.05:
        cpm *= 2.0
    elif engagement_rate > 0.03:
        cpm *= 1.5
    elif engagement_rate > 0.01:
        cpm *= 1.2

    # Scale with followers (bigger audience = slightly higher CPM)
    if followers > 100_000:
        cpm *= 1.5
    elif followers > 10_000:
        cpm *= 1.2

    return round(cpm, 2)


def find_brand_opportunities(followers: int, niche: str = "dark_motivation") -> list[dict]:
    """Suggest brand deal opportunities based on milestones."""
    opps = []

    if followers >= 1000:
        opps.append({
            "type": "micro_sponsorship",
            "description": "Micro brand deals with fitness/mindset brands",
            "est_revenue": "$50-200/post",
            "requirement": "1K+ followers",
            "ready": True,
        })

    if followers >= 5000:
        opps.append({
            "type": "affiliate_marketing",
            "description": "Affiliate links for books, courses, supplements",
            "est_revenue": "$100-500/month",
            "requirement": "5K+ followers",
            "ready": True,
        })

    if followers >= 10_000:
        opps.append({
            "type": "brand_partnership",
            "description": "Dedicated brand partnerships with lifestyle companies",
            "est_revenue": "$500-2000/campaign",
            "requirement": "10K+ followers",
            "ready": True,
        })

    if followers >= 50_000:
        opps.append({
            "type": "merch_launch",
            "description": "Launch @soren.era merchandise line",
            "est_revenue": "$2000-10000/month",
            "requirement": "50K+ followers",
            "ready": True,
        })

    # Always suggest these regardless of followers
    if not opps:
        opps.append({
            "type": "growth_focus",
            "description": "Focus on growing to 1K followers first, then monetization unlocks",
            "est_revenue": "$0 (growth phase)",
            "requirement": "Consistent posting",
            "ready": False,
        })

    return opps


def get_soren_metrics(cfg: ViperConfig) -> dict:
    """Full Soren monetization summary for dashboard."""
    growth = track_soren_growth(cfg)
    followers = growth.get("followers", 0)
    engagement = growth.get("engagement_rate", 0)

    # Average CPM across platforms
    platforms = growth.get("platforms", {})
    cpms = []
    for plat, stats in platforms.items():
        cpm = estimate_cpm(plat, followers, engagement)
        cpms.append(cpm)
    avg_cpm = sum(cpms) / len(cpms) if cpms else estimate_cpm("tiktok", followers, engagement)

    brand_opps = find_brand_opportunities(followers)
    brand_ready = any(o.get("ready") for o in brand_opps)

    return {
        "followers": followers,
        "engagement_rate": engagement,
        "estimated_cpm": avg_cpm,
        "brand_ready": brand_ready,
        "brand_opportunities": brand_opps,
        "growth_rate": growth.get("growth_rate", 0),
    }
