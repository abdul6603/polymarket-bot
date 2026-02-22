"""Swarm-Collaborate — reads status from all trading agents for cross-agent input."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

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
    regime_name = regime.get("current", "unknown")
    confidence = regime.get("confidence", 0)
    btc_bias = status.get("opportunities", {}).get("btc_bias", "neutral")
    eth_bias = status.get("opportunities", {}).get("eth_bias", "neutral")

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
