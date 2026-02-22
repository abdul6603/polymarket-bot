"""Swarm-Collaborate — reads status from all trading agents for cross-agent input."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

# Cross-agent memory (optional — graceful if shared layer missing)
try:
    from shared.agent_memory import AgentMemory
except ImportError:
    AgentMemory = None

try:
    from shared.events import get_events
except ImportError:
    def get_events(*a, **kw): return []

log = logging.getLogger(__name__)

DATA_DIR = Path.home() / "polymarket-bot" / "data"
ODIN_DIR = Path.home() / "odin" / "data"


def gather_agent_signals() -> dict[str, Any]:
    """Read latest status from Garves, Hawk, Odin, and Atlas to build agent signals."""
    signals: dict[str, Any] = {}

    # Garves — crypto Up/Down trader
    signals["garves"] = _read_garves()

    # Hawk — non-crypto Polymarket scanner
    signals["hawk"] = _read_hawk()

    # Odin — BTC/ETH futures swing trader
    signals["odin"] = _read_odin()

    # Atlas — research engine
    signals["atlas"] = _read_atlas()

    # Cross-agent memory (enhanced swarm intelligence)
    signals["garves_memory"] = _read_garves_memory()
    signals["odin_memory"] = _read_odin_memory()
    signals["recent_events"] = _read_recent_bus_events()

    return signals


def _read_json(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return {}


def _read_garves() -> str:
    """Garves's current view: regime, recent win rate, indicator signals."""
    status = _read_json(DATA_DIR / "garves_status.json")
    if not status:
        return "offline"

    regime = status.get("regime", {})
    regime_name = regime.get("name", "unknown")
    fng = regime.get("fear_greed", "?")
    win_rate = status.get("session", {}).get("win_rate_pct", 0)
    trades = status.get("session", {}).get("total_trades", 0)

    return f"regime={regime_name} FnG={fng} WR={win_rate:.0f}% ({trades} trades)"


def _read_hawk() -> str:
    """Hawk's view: any correlated market insights."""
    status = _read_json(DATA_DIR / "hawk_status.json")
    if not status:
        return "offline"

    scan = status.get("last_scan", {})
    opportunities = scan.get("opportunities_found", 0)
    win_rate = status.get("win_rate", 0)

    return f"opportunities={opportunities} WR={win_rate:.1f}%"


def _read_odin() -> str:
    """Odin's view: BTC/ETH derivatives regime, funding, structure."""
    status = _read_json(ODIN_DIR / "odin_status.json")
    if not status:
        return "offline"

    regime = status.get("regime", {})
    if isinstance(regime, dict):
        regime_name = regime.get("current", regime.get("regime", "unknown"))
        confidence = regime.get("confidence", regime.get("global_score", 0))
    else:
        regime_name = str(regime)
        confidence = 0

    opps = status.get("opportunities", [])
    btc_bias = "neutral"
    eth_bias = "neutral"
    if isinstance(opps, list):
        for o in opps:
            sym = (o.get("symbol") or "").upper()
            if "BTC" in sym:
                btc_bias = o.get("direction", "neutral").lower()
            elif "ETH" in sym:
                eth_bias = o.get("direction", "neutral").lower()
    elif isinstance(opps, dict):
        btc_bias = opps.get("btc_bias", "neutral")
        eth_bias = opps.get("eth_bias", "neutral")

    return f"regime={regime_name} conf={confidence:.0f}% BTC={btc_bias} ETH={eth_bias}"


def _read_atlas() -> str:
    """Atlas's macro intelligence summary."""
    atlas_dir = Path.home() / "atlas" / "data"
    status = _read_json(atlas_dir / "atlas_status.json")
    if not status:
        return "offline"

    cycle = status.get("cycle_count", 0)
    kb_size = status.get("kb_size", 0)
    last_topic = status.get("last_research_topic", "")

    return f"cycles={cycle} kb={kb_size} last_topic={last_topic[:50]}"


def _read_garves_memory() -> str:
    """Query Garves's AgentMemory for active high-confidence patterns."""
    if AgentMemory is None:
        return "memory_unavailable"
    try:
        mem = AgentMemory("garves")
        patterns = mem.get_active_patterns(min_confidence=0.5, limit=5)
        mem.close()
        if not patterns:
            return "no_patterns"
        summaries = []
        for p in patterns:
            summaries.append(f"{p.get('pattern', '')} (conf={p.get('confidence', 0):.0%})")
        return "; ".join(summaries)
    except Exception:
        return "memory_error"


def _read_odin_memory() -> str:
    """Query Odin's AgentMemory for regime patterns and derivatives intel."""
    if AgentMemory is None:
        return "memory_unavailable"
    try:
        mem = AgentMemory("odin")
        patterns = mem.get_active_patterns(min_confidence=0.5, limit=5)
        mem.close()
        if not patterns:
            return "no_patterns"
        summaries = []
        for p in patterns:
            summaries.append(f"{p.get('pattern', '')} (conf={p.get('confidence', 0):.0%})")
        return "; ".join(summaries)
    except Exception:
        return "memory_error"


def _read_recent_bus_events() -> str:
    """Read recent trade events from the event bus for cross-agent awareness."""
    try:
        events = get_events(limit=10)
        if not events:
            return "no_events"
        trade_events = [e for e in events if "trade" in (e.get("type") or "").lower()
                        or "prediction" in (e.get("type") or "").lower()]
        if not trade_events:
            return "no_trade_events"
        summaries = []
        for e in trade_events[:5]:
            agent = e.get("agent", "?")
            etype = e.get("type", "?")
            data = e.get("data", {})
            summaries.append(f"{agent}:{etype}({json.dumps(data, default=str)[:80]})")
        return "; ".join(summaries)
    except Exception:
        return "bus_error"
