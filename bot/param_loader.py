"""Runtime parameter loader — bridges Quant's backtest findings to Garves's live trading.

Quant backtests → validates with walk-forward → writes optimal params to
data/quant_live_params.json → this module reads them with a TTL cache →
SignalEngine uses them instead of hardcoded defaults.

Same pattern as weight_learner.py (disk-backed, cached, fallback to defaults).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
LIVE_PARAMS_FILE = DATA_DIR / "quant_live_params.json"

# Cache to avoid disk reads every signal evaluation
_params_cache: dict = {"params": None, "timestamp": 0.0}
_PARAMS_CACHE_TTL = 60  # seconds — check for new params every minute


def get_live_params(defaults: dict) -> dict:
    """Return live trading parameters, overriding defaults with Quant's validated findings.

    Args:
        defaults: Dict of param_name -> default_value (from signals.py constants).

    Returns:
        Dict with same keys, values overridden where Quant has validated improvements.
    """
    now = time.time()
    if _params_cache["params"] is not None and now - _params_cache["timestamp"] < _PARAMS_CACHE_TTL:
        result = dict(defaults)
        result.update(_params_cache["params"])
        return result

    overrides = _load_live_params()
    _params_cache["params"] = overrides
    _params_cache["timestamp"] = now

    if overrides:
        result = dict(defaults)
        result.update(overrides)
        return result

    return dict(defaults)


def _load_live_params() -> dict:
    """Load validated params from disk. Returns empty dict if file missing or invalid."""
    if not LIVE_PARAMS_FILE.exists():
        return {}
    try:
        with open(LIVE_PARAMS_FILE) as f:
            data = json.load(f)

        # Validate structure
        params = data.get("params", {})
        validation = data.get("validation", {})

        if not params:
            return {}

        # Safety check: only use if validation passed
        if not validation.get("walk_forward_passed", False):
            log.warning("Quant live params exist but walk-forward not passed — ignoring")
            return {}

        overfit = validation.get("overfit_drop", 99)
        if overfit > 8.0:
            log.warning("Quant live params overfit too high (%.1fpp) — ignoring", overfit)
            return {}

        log.info(
            "Loaded Quant live params: %s (validated: WR %.1f%% → %.1f%%, overfit %.1fpp)",
            list(params.keys()),
            validation.get("baseline_wr", 0),
            validation.get("best_wr", 0),
            overfit,
        )
        return params

    except Exception:
        log.exception("Failed to load quant live params")
        return {}


def invalidate_cache():
    """Force reload on next call (used after Quant writes new params)."""
    _params_cache["params"] = None
    _params_cache["timestamp"] = 0.0
