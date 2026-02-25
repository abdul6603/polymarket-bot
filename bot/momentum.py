"""Momentum Capture Mode — detects large moves in fearful/greedy markets.

Activates ONLY during big moves (BTC/ETH ≥2.8% in 4h or ≥4% in 8h), overrides
fear-paralysis filter gates in the move's direction, and auto-deactivates when
the move fades. Quiet days stay conservative.

Coordination: writes data/momentum_mode.json (Garves writes, all agents read).
Publishes activate/deactivate events to the shared event bus.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.price_cache import PriceCache
    from bot.regime import RegimeAdjustment

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
MOMENTUM_FILE = DATA_DIR / "momentum_mode.json"

# Trigger thresholds (ANY fires)
TRIGGER_4H_PCT = 2.8     # ≥2.8% move in 4h (240 candles)
TRIGGER_8H_PCT = 4.0     # ≥4.0% move in 8h (480 candles)
VOLUME_SPIKE_MULT = 2.5  # 30-min vol > 2.5× average hourly vol

# FnG gate — only in extreme regimes
FNG_FEAR_CEILING = 25
FNG_GREED_FLOOR = 75

# Anti-wick: require move to persist across N consecutive 1-min candles
PERSISTENCE_CANDLES = 5

# Auto-deactivation
DEACTIVATION_EMA_PERIOD = 20
DEACTIVATION_VOLUME_RATIO = 0.50  # volume < 50% of momentum-period avg


@dataclass
class MomentumState:
    active: bool = False
    direction: str = ""           # "up" or "down"
    trigger_asset: str = ""       # "bitcoin" or "ethereum"
    trigger_pct: float = 0.0
    trigger_type: str = ""        # "4h_move", "8h_move", "volume_spike"
    strength: int = 0             # 0-100
    activated_at: float = 0.0
    expires_at: float = 0.0
    manual_override: bool = False


def detect_momentum(
    price_cache: PriceCache,
    regime: RegimeAdjustment | None,
) -> MomentumState | None:
    """Check for momentum trigger conditions. Called every tick (~5-15s).

    Returns MomentumState if momentum is active (new or continuing), None otherwise.
    """
    # If already active, check deactivation first
    current = _read_state()
    if current and current.active:
        if _should_deactivate(current, price_cache):
            _deactivate(current)
            return None
        return current

    # Gate: only fire in extreme FnG regimes
    if regime is None:
        return None
    fng = regime.fng_value
    if FNG_FEAR_CEILING < fng < FNG_GREED_FLOOR:
        return None

    # Check each trigger for BTC and ETH
    for asset in ("bitcoin", "ethereum"):
        state = _check_triggers(price_cache, asset)
        if state is not None:
            _activate(state)
            return state

    return None


def force_momentum(direction: str, duration_h: float = 6.0) -> MomentumState:
    """Manual activation from dashboard."""
    now = time.time()
    state = MomentumState(
        active=True,
        direction=direction.lower(),
        trigger_asset="manual",
        trigger_pct=0.0,
        trigger_type="manual_force",
        strength=80,
        activated_at=now,
        expires_at=now + duration_h * 3600,
        manual_override=True,
    )
    _write_state(state)
    _publish_event("momentum_activated", state)
    log.info("[MOMENTUM] FORCE activated: %s for %.1fh", direction.upper(), duration_h)
    return state


def end_momentum() -> None:
    """Manual deactivation from dashboard."""
    current = _read_state()
    if current and current.active:
        _deactivate(current)
    else:
        # Clear file anyway
        _write_state(MomentumState())
    log.info("[MOMENTUM] Manually ended")


def get_state() -> MomentumState | None:
    """Read current momentum state (for cross-agent use)."""
    state = _read_state()
    if state and state.active and time.time() < state.expires_at:
        return state
    return None


# ── Internal helpers ──

def _check_triggers(price_cache: PriceCache, asset: str) -> MomentumState | None:
    """Check if asset has a qualifying move."""
    # 4h trigger (240 candles)
    candles_4h = price_cache.get_candles(asset, 240)
    if len(candles_4h) >= 240:
        first_close = candles_4h[0].close
        last_close = candles_4h[-1].close
        if first_close > 0:
            pct_4h = (last_close - first_close) / first_close * 100
            if abs(pct_4h) >= TRIGGER_4H_PCT:
                if _persistence_check(candles_4h, pct_4h > 0):
                    return _build_state(asset, pct_4h, "4h_move")

    # 8h trigger (480 candles)
    candles_8h = price_cache.get_candles(asset, 480)
    if len(candles_8h) >= 240:  # fire even with partial 8h data if move is big enough
        first_close = candles_8h[0].close
        last_close = candles_8h[-1].close
        if first_close > 0:
            pct_8h = (last_close - first_close) / first_close * 100
            if abs(pct_8h) >= TRIGGER_8H_PCT:
                if _persistence_check(candles_8h, pct_8h > 0):
                    return _build_state(asset, pct_8h, "8h_move")

    # Volume spike trigger
    candles_60 = price_cache.get_candles(asset, 60)
    if len(candles_60) >= 60:
        avg_hourly_vol = sum(c.volume for c in candles_60) / len(candles_60) * 60
        recent_30m_vol = sum(c.volume for c in candles_60[-30:])
        if avg_hourly_vol > 0 and recent_30m_vol > VOLUME_SPIKE_MULT * avg_hourly_vol:
            # Determine direction from the 30-min price move
            first_30 = candles_60[-30].close
            last_30 = candles_60[-1].close
            if first_30 > 0:
                pct_30m = (last_30 - first_30) / first_30 * 100
                if abs(pct_30m) >= 1.0:  # at least 1% move with the volume
                    if _persistence_check(candles_60[-30:], pct_30m > 0):
                        return _build_state(asset, pct_30m, "volume_spike")

    return None


def _persistence_check(candles, is_up: bool) -> bool:
    """Verify move persists across ≥5 consecutive 1-min candles (anti-wick)."""
    if len(candles) < PERSISTENCE_CANDLES:
        return False
    recent = candles[-PERSISTENCE_CANDLES:]
    if is_up:
        return all(c.close >= c.open for c in recent)
    else:
        return all(c.close <= c.open for c in recent)


def _build_state(asset: str, pct: float, trigger_type: str) -> MomentumState:
    """Build a new MomentumState from trigger data."""
    now = time.time()
    strength = min(100, int(abs(pct) * 15))
    duration_h = 4.0 + (strength / 100.0) * 8.0  # 4h min, 12h max
    return MomentumState(
        active=True,
        direction="up" if pct > 0 else "down",
        trigger_asset=asset,
        trigger_pct=round(abs(pct), 2),
        trigger_type=trigger_type,
        strength=strength,
        activated_at=now,
        expires_at=now + duration_h * 3600,
        manual_override=False,
    )


def _should_deactivate(state: MomentumState, price_cache: PriceCache) -> bool:
    """Check auto-deactivation conditions."""
    now = time.time()

    # Expiry
    if now >= state.expires_at:
        log.info("[MOMENTUM] Expired after %.1fh", (now - state.activated_at) / 3600)
        return True

    # Check EMA breach + volume drop
    asset = state.trigger_asset if state.trigger_asset != "manual" else "bitcoin"
    candles = price_cache.get_candles(asset, 60)
    if len(candles) < DEACTIVATION_EMA_PERIOD:
        return False

    # 20-period EMA of closes
    closes = [c.close for c in candles[-DEACTIVATION_EMA_PERIOD:]]
    ema = _ema(closes, DEACTIVATION_EMA_PERIOD)
    current_close = candles[-1].close

    ema_breached = False
    if state.direction == "up" and current_close < ema:
        ema_breached = True
    elif state.direction == "down" and current_close > ema:
        ema_breached = True

    if not ema_breached:
        return False

    # Also need volume drop in last 30 min vs momentum-period average
    if len(candles) >= 30:
        recent_vol = sum(c.volume for c in candles[-30:])
        period_avg_vol = sum(c.volume for c in candles) / len(candles) * 30
        if period_avg_vol > 0 and recent_vol < DEACTIVATION_VOLUME_RATIO * period_avg_vol:
            log.info("[MOMENTUM] Auto-fade: EMA breach + volume drop (%.0f%% of avg)",
                     recent_vol / period_avg_vol * 100 if period_avg_vol > 0 else 0)
            return True

    return False


def _ema(values: list[float], period: int) -> float:
    """Simple EMA calculation."""
    if not values:
        return 0.0
    k = 2 / (period + 1)
    ema_val = values[0]
    for v in values[1:]:
        ema_val = v * k + ema_val * (1 - k)
    return ema_val


def _activate(state: MomentumState) -> None:
    """Write state and publish activation event."""
    _write_state(state)
    _publish_event("momentum_activated", state)
    log.info(
        "[MOMENTUM] ACTIVATED: %s %s %+.1f%% (%s) strength=%d expires_in=%.1fh",
        state.direction.upper(), state.trigger_asset.upper(),
        state.trigger_pct, state.trigger_type, state.strength,
        (state.expires_at - time.time()) / 3600,
    )


def _deactivate(state: MomentumState) -> None:
    """Clear state and publish deactivation event."""
    _publish_event("momentum_deactivated", state)
    _write_state(MomentumState())  # write inactive state


def _write_state(state: MomentumState) -> None:
    """Atomic write to momentum_mode.json."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MOMENTUM_FILE.with_suffix(".json.tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(asdict(state), f, indent=2)
        os.replace(str(tmp), str(MOMENTUM_FILE))
    except Exception as e:
        log.error("[MOMENTUM] Failed to write state: %s", e)


def _read_state() -> MomentumState | None:
    """Read momentum state from file."""
    try:
        if not MOMENTUM_FILE.exists():
            return None
        data = json.loads(MOMENTUM_FILE.read_text())
        state = MomentumState(**data)
        # Check expiry
        if state.active and time.time() >= state.expires_at:
            state.active = False
        return state
    except Exception:
        return None


def _publish_event(event_type: str, state: MomentumState) -> None:
    """Publish to shared event bus."""
    try:
        import sys
        sys.path.insert(0, str(Path.home() / "shared"))
        from shared.events import publish
        publish(
            agent="garves",
            event_type=event_type,
            data=asdict(state),
            summary=f"Momentum {event_type.split('_')[1]}: {state.direction} {state.trigger_asset} {state.trigger_pct}%",
        )
    except Exception as e:
        log.debug("[MOMENTUM] Event publish failed: %s", str(e)[:100])
