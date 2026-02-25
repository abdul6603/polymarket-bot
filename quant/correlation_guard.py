"""Cross-Trader Correlation Guard — detect when Garves V2 + Odin have correlated positions.

Both traders can bet on the same asset (e.g., BTC up) simultaneously,
creating 2x risk exposure. This module detects and alerts on correlated positions.

Checks:
  1. Same-asset same-direction bets (direct overlap)
  2. Correlated assets (BTC + ETH move together → both long = amplified risk)
  3. Combined exposure vs bankroll limits

Publishes alerts to event bus when correlation exceeds thresholds.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ODIN_DATA_DIR = Path.home() / "odin" / "data"

# Known crypto asset correlations (approximate, BTC-relative)
ASSET_CORRELATIONS = {
    ("bitcoin", "ethereum"): 0.85,
    ("bitcoin", "solana"): 0.75,
    ("bitcoin", "xrp"): 0.70,
    ("ethereum", "solana"): 0.80,
    ("ethereum", "xrp"): 0.65,
    ("solana", "xrp"): 0.60,
}

# Map Odin symbols to Garves V2 asset names
ODIN_TO_GARVES = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "SOLUSDT": "solana",
    "XRPUSDT": "xrp",
    "LTCUSDT": "litecoin",
    "BCHUSDT": "bitcoin_cash",
    "DOGEUSDT": "dogecoin",
}


@dataclass
class PositionOverlap:
    """A detected position overlap between traders."""
    garves_asset: str = ""
    garves_direction: str = ""
    garves_size_usd: float = 0.0
    odin_symbol: str = ""
    odin_direction: str = ""
    odin_notional_usd: float = 0.0
    overlap_type: str = ""          # "direct" or "correlated"
    correlation: float = 0.0
    combined_exposure_usd: float = 0.0
    risk_level: str = "low"         # "low", "medium", "high"


@dataclass
class CorrelationReport:
    """Full correlation analysis between Garves V2 and Odin."""
    timestamp: float = 0.0
    # Positions
    garves_positions: list[dict] = field(default_factory=list)
    odin_positions: list[dict] = field(default_factory=list)
    # Overlaps found
    overlaps: list[PositionOverlap] = field(default_factory=list)
    direct_overlaps: int = 0
    correlated_overlaps: int = 0
    # Exposure
    garves_total_exposure: float = 0.0
    odin_total_exposure: float = 0.0
    combined_exposure: float = 0.0
    max_single_asset_exposure: float = 0.0
    # Risk assessment
    overall_risk: str = "low"       # "low", "medium", "high", "critical"
    alert_message: str = ""
    recommendations: list[str] = field(default_factory=list)
    # Historical correlation from trades
    trade_correlation: float = 0.0
    trade_correlation_window: int = 0


def _load_garves_positions() -> list[dict]:
    """Load current Garves V2 open positions from polymarket_positions.json."""
    pos_file = DATA_DIR / "polymarket_positions.json"
    if not pos_file.exists():
        return []
    try:
        data = json.loads(pos_file.read_text())
        positions = data if isinstance(data, list) else data.get("positions", [])
        return [
            {
                "asset": p.get("asset", "unknown"),
                "direction": p.get("direction", "unknown"),
                "size_usd": p.get("size_usd", p.get("cost", 0)),
                "market_id": p.get("market_id", ""),
                "timeframe": p.get("timeframe", ""),
            }
            for p in positions
            if p.get("asset") and p.get("direction")
        ]
    except Exception:
        log.exception("Failed to load Garves V2 positions")
        return []


def _load_odin_positions() -> list[dict]:
    """Load current Odin open positions from odin status/paper positions."""
    # Try paper positions first
    paper_file = ODIN_DATA_DIR / "odin_paper_positions.json"
    status_file = ODIN_DATA_DIR / "odin_status.json"

    positions = []

    # From status file (has paper_positions array)
    if status_file.exists():
        try:
            data = json.loads(status_file.read_text())
            for p in data.get("paper_positions", []):
                symbol = p.get("symbol", "")
                asset = ODIN_TO_GARVES.get(symbol, symbol.replace("USDT", "").lower())
                direction = p.get("direction", "").lower()
                # Normalize: Odin uses LONG/SHORT, Garves uses up/down
                if direction == "long":
                    direction = "up"
                elif direction == "short":
                    direction = "down"

                positions.append({
                    "symbol": symbol,
                    "asset": asset,
                    "direction": direction,
                    "notional_usd": abs(p.get("notional", 0)),
                    "leverage": p.get("leverage", 1),
                    "entry_price": p.get("entry_price", 0),
                    "unrealized_pnl": p.get("unrealized_pnl", 0),
                })
        except Exception:
            pass

    return positions


def _get_correlation(asset_a: str, asset_b: str) -> float:
    """Get correlation between two assets. Returns 1.0 for same asset."""
    if asset_a == asset_b:
        return 1.0
    key = tuple(sorted([asset_a, asset_b]))
    return ASSET_CORRELATIONS.get(key, 0.0)


def _compute_trade_correlation(
    garves_trades: list[dict],
    odin_trades: list[dict],
    window_hours: float = 24.0,
) -> float:
    """Compute directional correlation between Garves V2 and Odin recent trades.

    Looks at overlapping time windows where both traders had positions.
    Returns correlation coefficient (-1 to 1).
    """
    if not garves_trades or not odin_trades:
        return 0.0

    cutoff = time.time() - window_hours * 3600

    # Build direction sequences for overlapping crypto assets
    garves_signals = []
    odin_signals = []

    for gt in garves_trades:
        if gt.get("timestamp", 0) < cutoff:
            continue
        asset = gt.get("asset", "")
        direction = gt.get("direction", "")
        if not asset or not direction:
            continue

        # Find Odin trades on correlated assets in similar timeframe
        for ot in odin_trades:
            if ot.get("entry_time", 0) < cutoff:
                continue
            odin_asset = ODIN_TO_GARVES.get(ot.get("symbol", ""), "")
            corr = _get_correlation(asset, odin_asset)
            if corr < 0.5:
                continue

            # Both had positions on correlated assets
            g_dir = 1 if direction == "up" else -1
            o_dir = 1 if ot.get("side", "").lower() == "long" else -1
            garves_signals.append(g_dir)
            odin_signals.append(o_dir * corr)  # weight by correlation

    if len(garves_signals) < 3:
        return 0.0

    # Simple correlation: fraction of same-direction bets
    import numpy as np
    g = np.array(garves_signals)
    o = np.array(odin_signals)
    if g.std() == 0 or o.std() == 0:
        return 0.0
    return float(np.corrcoef(g, o)[0, 1])


def check_correlation(
    correlation_threshold: float = 0.7,
    max_combined_exposure_usd: float = 50.0,
    garves_trades: list[dict] | None = None,
    odin_trades: list[dict] | None = None,
) -> CorrelationReport:
    """Run full correlation check between Garves V2 and Odin positions.

    Returns a CorrelationReport with overlaps, risk level, and recommendations.
    """
    report = CorrelationReport(timestamp=time.time())

    # Load current positions
    garves_pos = _load_garves_positions()
    odin_pos = _load_odin_positions()
    report.garves_positions = garves_pos
    report.odin_positions = odin_pos

    # Calculate total exposure
    report.garves_total_exposure = sum(p.get("size_usd", 0) for p in garves_pos)
    report.odin_total_exposure = sum(p.get("notional_usd", 0) for p in odin_pos)
    report.combined_exposure = report.garves_total_exposure + report.odin_total_exposure

    # Check for overlaps
    overlaps = []
    asset_exposure: dict[str, float] = {}

    for gp in garves_pos:
        g_asset = gp.get("asset", "")
        g_dir = gp.get("direction", "")
        g_size = gp.get("size_usd", 0)

        # Track per-asset exposure
        asset_exposure[g_asset] = asset_exposure.get(g_asset, 0) + g_size

        for op in odin_pos:
            o_asset = op.get("asset", "")
            o_dir = op.get("direction", "")
            o_notional = op.get("notional_usd", 0)

            corr = _get_correlation(g_asset, o_asset)
            if corr < 0.5:
                continue

            # Same direction on correlated assets = amplified risk
            same_direction = (g_dir == o_dir)
            if not same_direction:
                continue  # Opposing positions actually reduce risk

            overlap_type = "direct" if corr >= 1.0 else "correlated"
            combined = g_size + o_notional * corr
            risk = "low"
            if combined > max_combined_exposure_usd * 0.75:
                risk = "high"
            elif combined > max_combined_exposure_usd * 0.5:
                risk = "medium"

            overlap = PositionOverlap(
                garves_asset=g_asset,
                garves_direction=g_dir,
                garves_size_usd=g_size,
                odin_symbol=op.get("symbol", ""),
                odin_direction=o_dir,
                odin_notional_usd=o_notional,
                overlap_type=overlap_type,
                correlation=corr,
                combined_exposure_usd=round(combined, 2),
                risk_level=risk,
            )
            overlaps.append(overlap)

    # Also track Odin per-asset exposure
    for op in odin_pos:
        o_asset = op.get("asset", "")
        o_notional = op.get("notional_usd", 0)
        asset_exposure[o_asset] = asset_exposure.get(o_asset, 0) + o_notional

    report.overlaps = overlaps
    report.direct_overlaps = sum(1 for o in overlaps if o.overlap_type == "direct")
    report.correlated_overlaps = sum(1 for o in overlaps if o.overlap_type == "correlated")
    report.max_single_asset_exposure = max(asset_exposure.values()) if asset_exposure else 0

    # Historical trade correlation
    if garves_trades and odin_trades:
        report.trade_correlation = round(
            _compute_trade_correlation(garves_trades, odin_trades), 3
        )
        report.trade_correlation_window = 24  # hours

    # Risk assessment
    high_risk_overlaps = [o for o in overlaps if o.risk_level == "high"]
    if high_risk_overlaps:
        report.overall_risk = "critical" if len(high_risk_overlaps) >= 2 else "high"
    elif overlaps:
        report.overall_risk = "medium" if report.direct_overlaps > 0 else "low"
    else:
        report.overall_risk = "low"

    # Build alert and recommendations
    if report.overall_risk in ("high", "critical"):
        assets = set(o.garves_asset for o in high_risk_overlaps)
        report.alert_message = (
            f"HIGH CORRELATION: Garves V2 + Odin both betting {', '.join(assets)} "
            f"same direction. Combined exposure: ${report.combined_exposure:.0f}"
        )
        for o in high_risk_overlaps:
            report.recommendations.append(
                f"Reduce {o.garves_asset}: Garves V2 ${o.garves_size_usd:.0f} + "
                f"Odin ${o.odin_notional_usd:.0f} = ${o.combined_exposure_usd:.0f} "
                f"(corr={o.correlation:.0%})"
            )
    elif report.overall_risk == "medium":
        report.alert_message = (
            f"Moderate correlation: {len(overlaps)} position overlap(s) detected. "
            f"Monitor closely."
        )
    else:
        report.alert_message = "No significant position correlation between Garves V2 and Odin."

    # Publish to event bus if high risk
    if report.overall_risk in ("high", "critical"):
        _publish_correlation_alert(report)

    log.info("Correlation check: %s (%d overlaps, combined=$%.0f, trade_corr=%.2f)",
             report.overall_risk, len(overlaps), report.combined_exposure,
             report.trade_correlation)

    return report


def _publish_correlation_alert(report: CorrelationReport):
    """Publish correlation alert to shared event bus."""
    try:
        import sys
        _shared = str(Path.home() / "shared")
        if _shared not in sys.path:
            sys.path.insert(0, _shared)
        from events import publish
        publish(
            agent="quant",
            event_type="correlation_alert",
            severity="warning" if report.overall_risk == "high" else "error",
            summary=report.alert_message,
            data={
                "overall_risk": report.overall_risk,
                "direct_overlaps": report.direct_overlaps,
                "correlated_overlaps": report.correlated_overlaps,
                "combined_exposure": report.combined_exposure,
                "trade_correlation": report.trade_correlation,
                "recommendations": report.recommendations,
            },
        )
    except Exception:
        pass


def write_correlation_report(report: CorrelationReport):
    """Write Garves V2/Odin correlation report to disk for dashboard display."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")

    output = {
        "overall_risk": report.overall_risk,
        "alert_message": report.alert_message,
        "direct_overlaps": report.direct_overlaps,
        "correlated_overlaps": report.correlated_overlaps,
        "garves_exposure": round(report.garves_total_exposure, 2),
        "odin_exposure": round(report.odin_total_exposure, 2),
        "combined_exposure": round(report.combined_exposure, 2),
        "max_single_asset": round(report.max_single_asset_exposure, 2),
        "trade_correlation": report.trade_correlation,
        "overlaps": [
            {
                "garves_asset": o.garves_asset,
                "garves_direction": o.garves_direction,
                "garves_size": o.garves_size_usd,
                "odin_symbol": o.odin_symbol,
                "odin_direction": o.odin_direction,
                "odin_notional": o.odin_notional_usd,
                "type": o.overlap_type,
                "correlation": o.correlation,
                "combined": o.combined_exposure_usd,
                "risk": o.risk_level,
            }
            for o in report.overlaps
        ],
        "recommendations": report.recommendations,
        "updated": datetime.now(ET).strftime("%Y-%m-%d %I:%M %p ET"),
    }

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "quant_correlation.json").write_text(json.dumps(output, indent=2))
