"""Agent Digest System — per-agent intelligence briefs compiled from all Viper data.

Every 4 cycles (~20 min), Viper compiles tailored intelligence digests for each agent
and writes them to data files. Agents read their digest at their own pace.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
OPPS_FILE = DATA_DIR / "viper_opportunities.json"
COSTS_FILE = DATA_DIR / "viper_costs.json"
SOREN_OPPS_FILE = DATA_DIR / "soren_opportunities.json"
TRADES_FILE = DATA_DIR / "trades.jsonl"
HAWK_TRADES_FILE = DATA_DIR / "hawk_trades.jsonl"
REGIME_FILE = DATA_DIR / "market_regime.json"

MAX_DIGEST_ITEMS = 10


def _read_json(path: Path) -> dict | list:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


def _read_jsonl_tail(path: Path, n: int = 20) -> list[dict]:
    """Read last N lines of a JSONL file."""
    if not path.exists():
        return []
    try:
        lines = path.read_text().strip().split("\n")
        result = []
        for line in lines[-n:]:
            line = line.strip()
            if line:
                try:
                    result.append(json.loads(line))
                except Exception:
                    pass
        return result
    except Exception:
        return []


def _save_digest(agent: str, digest: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"viper_{agent}_digest.json"
    try:
        path.write_text(json.dumps(digest, indent=2))
    except Exception:
        log.exception("Failed to save %s digest", agent)


def _make_digest_item(headline: str, detail: str, source: str, score: int = 0, **extra) -> dict:
    item = {
        "headline": headline[:200],
        "detail": detail[:400],
        "source": source,
        "score": score,
        "ts": time.time(),
    }
    item.update(extra)
    return item


def _generate_garves_digest() -> dict:
    """Garves digest: crypto news, market regime, cost data."""
    items = []

    # Crypto news from intel feed
    opps = _read_json(OPPS_FILE)
    for opp in (opps.get("opportunities", []) if isinstance(opps, dict) else []):
        tags = opp.get("relevance_tags", [])
        if any(t in tags for t in ["bitcoin", "ethereum", "sol", "crypto", "btc", "eth"]):
            items.append(_make_digest_item(
                opp.get("headline", ""),
                opp.get("summary", ""),
                opp.get("source", "viper"),
                opp.get("score", 0),
                category="crypto_intel",
            ))

    # Market regime context
    regime = _read_json(REGIME_FILE)
    if regime:
        fng = regime.get("fear_greed", regime.get("fng_value", ""))
        if fng:
            items.append(_make_digest_item(
                f"Market Fear & Greed: {fng}",
                json.dumps({k: v for k, v in regime.items() if k != "raw"}, default=str)[:400],
                "regime_data",
                50,
                category="market_regime",
            ))

    # Cost data for Garves
    costs = _read_json(COSTS_FILE)
    agent_totals = costs.get("agent_totals", {})
    garves_cost = agent_totals.get("garves", 0)
    if garves_cost > 0:
        items.append(_make_digest_item(
            f"Garves monthly cost: ${garves_cost:.2f}",
            f"Total system cost: ${costs.get('total_monthly', 0):.2f}/mo",
            "cost_audit",
            30,
            category="cost",
        ))

    items.sort(key=lambda x: x["score"], reverse=True)
    return {
        "agent": "garves",
        "items": items[:MAX_DIGEST_ITEMS],
        "generated_at": time.time(),
        "item_count": len(items[:MAX_DIGEST_ITEMS]),
        "fresh": True,
    }


def _generate_hawk_digest() -> dict:
    """Hawk digest: top scored intel matched to markets, volume spikes."""
    items = []

    # Top scored intel items
    opps = _read_json(OPPS_FILE)
    for opp in (opps.get("opportunities", []) if isinstance(opps, dict) else [])[:15]:
        matched = opp.get("matched_markets", [])
        items.append(_make_digest_item(
            opp.get("headline", ""),
            opp.get("summary", ""),
            opp.get("source", "viper"),
            opp.get("score", 0),
            category="intel",
            matched_markets=matched,
        ))

    # Volume spike alerts from Polymarket activity
    for opp in (opps.get("opportunities", []) if isinstance(opps, dict) else []):
        if opp.get("source") == "polymarket_activity":
            raw = opp.get("raw_data", {})
            vol = raw.get("volume_24h", 0)
            if vol > 50000:
                items.append(_make_digest_item(
                    f"Volume spike: {opp.get('headline', '')}",
                    f"24h Volume: ${vol:,.0f} | Price: {raw.get('yes_price', 0.5):.2f}",
                    "polymarket_activity",
                    85,
                    category="volume_spike",                    condition_id=raw.get("condition_id", ""),
                ))

    items.sort(key=lambda x: x["score"], reverse=True)
    return {
        "agent": "hawk",
        "items": items[:MAX_DIGEST_ITEMS],
        "generated_at": time.time(),
        "item_count": len(items[:MAX_DIGEST_ITEMS]),
        "fresh": True,
    }


def _generate_soren_digest() -> dict:
    """Soren digest: top Soren opportunities, trending signals."""
    items = []

    # Top Soren opportunities
    soren_opps = _read_json(SOREN_OPPS_FILE)
    for opp in (soren_opps.get("opportunities", []) if isinstance(soren_opps, dict) else [])[:10]:
        items.append(_make_digest_item(
            opp.get("title", ""),
            opp.get("description", ""),
            opp.get("source", "soren_scout"),
            opp.get("fit_score", 0),
            category=opp.get("type", "opportunity"),
            urgency=opp.get("urgency", "low"),
        ))

    # Trending content signals from intel
    opps = _read_json(OPPS_FILE)
    for opp in (opps.get("opportunities", []) if isinstance(opps, dict) else []):
        tags = opp.get("relevance_tags", [])
        if any(t in tags for t in ["viral", "tiktok", "youtube"]):
            items.append(_make_digest_item(
                opp.get("headline", ""),
                opp.get("summary", ""),
                "intel_trending",
                opp.get("score", 0),
                category="trending",
            ))

    items.sort(key=lambda x: x["score"], reverse=True)
    return {
        "agent": "soren",
        "items": items[:MAX_DIGEST_ITEMS],
        "generated_at": time.time(),
        "item_count": len(items[:MAX_DIGEST_ITEMS]),
        "fresh": True,
    }


def _generate_shelby_digest() -> dict:
    """Shelby digest: action items, cost waste, pipeline stats."""
    items = []

    # High-score intel action items
    opps = _read_json(OPPS_FILE)
    for opp in (opps.get("opportunities", []) if isinstance(opps, dict) else []):
        if opp.get("score", 0) >= 75:
            items.append(_make_digest_item(
                f"[ACTION] {opp.get('headline', '')}",
                opp.get("summary", ""),
                opp.get("source", "viper"),
                opp.get("score", 0),
                category="action_item",
            ))

    # Cost waste alerts
    costs = _read_json(COSTS_FILE)
    for w in costs.get("waste", []):
        items.append(_make_digest_item(
            f"[WASTE] {w.get('agent', '?')}: ${w.get('monthly', 0):.2f}/mo",
            w.get("reason", "High spend"),
            "cost_audit",
            60,
            category="cost_waste",
        ))

    # Push stats
    pushed_file = DATA_DIR / "viper_pushed.json"
    push_count = 0
    if pushed_file.exists():
        try:
            push_count = len(json.loads(pushed_file.read_text()))
        except Exception:
            pass

    items.append(_make_digest_item(
        f"Pipeline stats: {push_count} total pushes to Shelby",
        f"System cost: ${costs.get('total_monthly', 0):.2f}/mo",
        "pipeline",
        20,
        category="stats",
    ))

    items.sort(key=lambda x: x["score"], reverse=True)
    return {
        "agent": "shelby",
        "items": items[:MAX_DIGEST_ITEMS],
        "generated_at": time.time(),
        "item_count": len(items[:MAX_DIGEST_ITEMS]),
        "fresh": True,
    }


def _generate_atlas_digest() -> dict:
    """Atlas digest: cost trends, new research topics, anomalies."""
    items = []

    # Cross-agent cost trends
    costs = _read_json(COSTS_FILE)
    agent_totals = costs.get("agent_totals", {})
    if agent_totals:
        top_spender = max(agent_totals, key=agent_totals.get)
        items.append(_make_digest_item(
            f"Top spender: {top_spender} (${agent_totals[top_spender]:.2f}/mo)",
            f"Total: ${costs.get('total_monthly', 0):.2f}/mo across {len(agent_totals)} agents",
            "cost_audit",
            50,
            category="cost_trend",
        ))

    # New research topics from intel scanning
    opps = _read_json(OPPS_FILE)
    categories_seen = set()
    for opp in (opps.get("opportunities", []) if isinstance(opps, dict) else []):
        cat = opp.get("category", "other")
        if cat not in categories_seen and cat != "other":
            categories_seen.add(cat)
            items.append(_make_digest_item(
                f"Active intel category: {cat}",
                f"Latest: {opp.get('headline', '')[:200]}",
                "intel_categories",
                30,
                category="research_topic",
            ))

    # System-wide anomalies
    anomaly_file = DATA_DIR / "viper_baselines.json"
    if anomaly_file.exists():
        try:
            baselines = json.loads(anomaly_file.read_text())
            items.append(_make_digest_item(
                f"Monitoring {len(baselines.get('agents', {}))} agents",
                f"Baselines updated: {baselines.get('updated', 'unknown')}",
                "anomaly_detector",
                20,
                category="system_health",
            ))
        except Exception:
            pass

    items.sort(key=lambda x: x["score"], reverse=True)
    return {
        "agent": "atlas",
        "items": items[:MAX_DIGEST_ITEMS],
        "generated_at": time.time(),
        "item_count": len(items[:MAX_DIGEST_ITEMS]),
        "fresh": True,
    }


def generate_digests() -> dict:
    """Build per-agent intelligence digests from all Viper data sources.

    Called every 4th cycle (~20 min). Saves to data/viper_{agent}_digest.json.
    Publishes digest_generated event to shared event bus.
    """
    digests = {}

    for name, gen_fn in [
        ("garves", _generate_garves_digest),
        ("hawk", _generate_hawk_digest),
        ("soren", _generate_soren_digest),
        ("shelby", _generate_shelby_digest),
        ("atlas", _generate_atlas_digest),
    ]:
        try:
            digest = gen_fn()
            _save_digest(name, digest)
            digests[name] = digest
            log.info("Digest generated for %s: %d items", name, digest.get("item_count", 0))
        except Exception:
            log.exception("Failed to generate %s digest", name)

    # Publish event
    try:
        from shared.events import publish as bus_publish
        bus_publish(
            agent="viper",
            event_type="digest_generated",
            data={
                "agents": list(digests.keys()),
                "total_items": sum(d.get("item_count", 0) for d in digests.values()),
            },
            summary=f"Digests generated for {len(digests)} agents",
        )
    except Exception:
        pass

    return digests
