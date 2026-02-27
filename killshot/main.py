"""Killshot — late-window maker snipe for 5m crypto markets.

Runs as a separate process alongside Garves. Discovers 5m markets via
Gamma slug lookups, monitors Binance spot prices in real-time, and
simulates paper trades when direction is determined in the final 60
seconds of each window.

Usage:
    cd ~/polymarket-bot && .venv/bin/python -m killshot.main
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from bot.config import Config as BotConfig
from bot.price_cache import PriceCache
from bot.binance_feed import BinanceFeed
from bot.snipe.window_tracker import WindowTracker

from killshot.config import KillshotConfig
from killshot.engine import KillshotEngine
from killshot.tracker import PaperTracker

log = logging.getLogger("killshot")

# ── Asset detection ─────────────────────────────────────────────
_ASSET_KEYWORDS = {
    "bitcoin": ("bitcoin up or down",),
    "ethereum": ("ethereum up or down",),
    "solana": ("solana up or down",),
    "xrp": ("xrp up or down",),
}
_ASSET_SHORT = {
    "bitcoin": "btc", "ethereum": "eth", "solana": "sol", "xrp": "xrp",
}


def _detect_asset(question: str) -> str | None:
    q = question.lower()
    for asset, keywords in _ASSET_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return asset
    return None


# ── Lightweight market data object for WindowTracker ────────────

@dataclass
class Market5m:
    """Minimal market object compatible with WindowTracker.update()."""
    market_id: str
    question: str
    asset: str
    raw: dict[str, Any]


# ── Market scanner ──────────────────────────────────────────────

def _scan_5m_markets(assets: list[str]) -> list[Market5m]:
    """Scan Gamma + CLOB for active 5m crypto up/down markets.

    Uses timestamp-based slug lookups: {coin}-updown-5m-{unix_ts}
    where ts aligns to 5-minute boundaries.
    """
    now = time.time()
    current_ts = int(now // 300) * 300
    intervals = [current_ts, current_ts + 300]

    results: list[Market5m] = []
    seen: set[str] = set()

    with httpx.Client(timeout=8) as client:
        for ts in intervals:
            for asset in assets:
                coin = _ASSET_SHORT.get(asset)
                if not coin:
                    continue
                slug = f"{coin}-updown-5m-{ts}"
                try:
                    resp = client.get(
                        "https://gamma-api.polymarket.com/markets",
                        params={"slug": slug},
                    )
                    if resp.status_code != 200:
                        continue
                    for m in resp.json():
                        cid = m.get("conditionId") or m.get("condition_id", "")
                        if not cid or cid in seen or m.get("closed"):
                            continue

                        # Fetch full CLOB data (includes tokens with IDs)
                        clob_resp = client.get(
                            f"https://clob.polymarket.com/markets/{cid}",
                        )
                        if clob_resp.status_code != 200:
                            continue
                        clob_market = clob_resp.json()
                        if not clob_market.get("accepting_orders"):
                            continue

                        question = clob_market.get("question", "")
                        detected = _detect_asset(question)
                        if not detected or detected not in assets:
                            continue

                        seen.add(cid)
                        results.append(Market5m(
                            market_id=clob_market.get("condition_id", cid),
                            question=question[:120],
                            asset=detected,
                            raw=clob_market,
                        ))
                except Exception as e:
                    log.debug("Scan error for %s/%d: %s", asset, ts, str(e)[:80])

    return results


# ── Signal handling ─────────────────────────────────────────────

_running = True


def _signal_handler(sig, _frame):
    global _running
    log.info("Shutdown signal received (sig=%s)", sig)
    _running = False


# ── Logging setup ───────────────────────────────────────────────

def _setup_logging() -> None:
    fmt = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


# ── Main ────────────────────────────────────────────────────────

def main() -> None:
    global _running

    _setup_logging()
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    cfg = KillshotConfig()
    bot_cfg = BotConfig()

    log.info("=" * 60)
    log.info("KILLSHOT — Late-Window Maker Snipe")
    log.info("=" * 60)
    log.info("Mode:       %s", "PAPER" if cfg.dry_run else "LIVE")
    log.info("Bankroll:   $%.0f", cfg.bankroll_usd)
    log.info("Max bet:    $%.0f", cfg.max_bet_usd)
    log.info("Assets:     %s", ", ".join(cfg.assets))
    log.info("Kill zone:  T-%ds to T-%ds", cfg.window_seconds, cfg.min_window_seconds)
    log.info("Threshold:  %.4f%%", cfg.direction_threshold * 100)
    log.info("Entry:      %.0f¢ - %.0f¢", cfg.entry_price_min * 100, cfg.entry_price_max * 100)
    log.info("Tick:       %.1fs | Scan: %.0fs", cfg.tick_interval_s, cfg.scan_interval_s)
    log.info("=" * 60)

    # Safety gate: live mode requires separate wallet
    if not cfg.dry_run and not cfg.private_key:
        log.critical("LIVE MODE requires KILLSHOT_PRIVATE_KEY. Exiting.")
        sys.exit(1)

    # Initialize shared components
    from bot.snipe import clob_book
    clob_book.init("https://clob.polymarket.com")

    price_cache = PriceCache()
    price_cache.preload_from_disk()

    binance_feed = BinanceFeed(bot_cfg, price_cache)
    window_tracker = WindowTracker(bot_cfg, price_cache)
    tracker = PaperTracker()
    engine = KillshotEngine(cfg, price_cache, tracker)

    # Start Binance WebSocket feed (runs in daemon threads)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(binance_feed.start())
    log.info("Binance feed started")

    # Wait for first price data
    log.info("Waiting for Binance price data...")
    for _ in range(30):
        if price_cache.get_price("bitcoin"):
            break
        time.sleep(1)
    else:
        log.warning("No BTC price after 30s — continuing anyway")

    btc = price_cache.get_price("bitcoin")
    if btc:
        log.info("BTC price: $%.2f — feed alive", btc)

    last_scan = 0.0
    last_status_write = 0.0
    tick_count = 0

    log.info("Entering main loop...")

    while _running:
        try:
            now = time.time()
            tick_count += 1

            # Keep Binance feed alive
            binance_feed.ensure_alive()

            # Periodic market scan
            if now - last_scan > cfg.scan_interval_s:
                markets = _scan_5m_markets(cfg.assets)
                if markets:
                    window_tracker.update(markets)
                active = len(window_tracker.all_active_windows())
                log.info(
                    "Scan: %d markets found, %d active windows",
                    len(markets), active,
                )
                last_scan = now

            # Engine tick — evaluate kill zones
            engine.tick(window_tracker.all_active_windows())

            # Resolve completed paper trades
            resolved = tracker.resolve_trades(price_cache)
            if resolved:
                engine.report_resolved(resolved)

            # Periodic cleanup (every hour)
            if tick_count % 3600 == 0:
                engine.cleanup_expired()

            # Write status for dashboard (every 10s)
            if now - last_status_write > 10:
                tracker.write_status()
                last_status_write = now

            time.sleep(cfg.tick_interval_s)

        except KeyboardInterrupt:
            break
        except Exception:
            log.exception("Tick error — continuing")
            time.sleep(5)

    # Graceful shutdown
    log.info("Shutting down Killshot...")
    tracker.write_status()
    price_cache.save_candles()
    binance_feed._running = False
    loop.close()
    log.info("Killshot stopped.")


if __name__ == "__main__":
    main()
