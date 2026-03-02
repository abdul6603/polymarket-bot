"""Killshot — late-window maker snipe for 5m crypto markets.

Runs as a separate process alongside Garves. Discovers 5m markets via
Gamma slug lookups, monitors Binance spot prices in real-time, and
trades when direction is determined in the final seconds of each window.

Enhanced with Binance @aggTrade leading indicator and 1-minute market support.

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
from killshot.clob_ws import ClobWS
from killshot.engine import KillshotEngine
from killshot.eval_tracker import EvalTracker
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

def _scan_markets(assets: list[str], market_types: str = "5m") -> list[Market5m]:
    """Scan Gamma + CLOB for active crypto up/down markets.

    Supports multiple market types: 5m, 1m (comma-separated).
    Uses timestamp-based slug lookups: {coin}-updown-{type}-{unix_ts}
    """
    now = time.time()
    types = [t.strip() for t in market_types.split(",")]
    results: list[Market5m] = []
    seen: set[str] = set()

    with httpx.Client(timeout=8) as client:
        for mtype in types:
            if mtype == "5m":
                interval = 300
            elif mtype == "1m":
                interval = 60
            else:
                continue

            current_ts = int(now // interval) * interval
            intervals = [current_ts, current_ts + interval]
            if mtype == "1m":
                intervals.append(current_ts + interval * 2)

            for ts in intervals:
                for asset in assets:
                    coin = _ASSET_SHORT.get(asset)
                    if not coin:
                        continue
                    slug = f"{coin}-updown-{mtype}-{ts}"
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
                        log.debug("Scan error for %s/%s/%d: %s", mtype, asset, ts, str(e)[:80])

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
    log.info("KILLSHOT — Late-Window Maker Snipe v2.0")
    log.info("=" * 60)
    log.info("Mode:       %s", "PAPER" if cfg.dry_run else "LIVE")
    log.info("Bankroll:   $%.0f", cfg.bankroll_usd)
    log.info("Max bet:    $%.0f", cfg.max_bet_usd)
    log.info("Assets:     %s", ", ".join(cfg.assets))
    log.info("Kill zone:  T-%ds to T-%ds", cfg.window_seconds, cfg.min_window_seconds)
    log.info("Threshold:  %.4f%% (adaptive=%s)", cfg.direction_threshold * 100, cfg.adaptive_threshold)
    log.info("Entry:      %.0f¢ - %.0f¢", cfg.entry_price_min * 100, cfg.entry_price_max * 100)
    log.info("Kelly:      %s (fraction=%.2f)", "ON" if cfg.kelly_enabled else "OFF", cfg.kelly_fraction)
    log.info("Arb:        %s (threshold=%.2f)", "ON" if cfg.arb_enabled else "OFF", cfg.arb_threshold)
    log.info("Exposure:   $%.0f max", cfg.max_exposure_usd)
    log.info("Rust:       %s (%s)", "ON" if cfg.rust_executor_enabled else "OFF", cfg.rust_executor_url)
    log.info("Binance:    %s", "ON" if cfg.binance_agg_enabled else "OFF")
    log.info("Cascade:    %s (delay=%.1fs)", "ON" if cfg.cascade_enabled else "OFF", cfg.cascade_delay_s)
    log.info("Markets:    %s", cfg.market_types)
    log.info("Tick:       %.1fs | Scan: %.0fs", cfg.tick_interval_s, cfg.scan_interval_s)
    log.info("=" * 60)

    # Safety gate: live mode requires separate wallet
    if not cfg.dry_run and not cfg.private_key:
        log.critical("LIVE MODE requires KILLSHOT_PRIVATE_KEY. Exiting.")
        sys.exit(1)

    # Initialize shared components
    from bot.snipe import clob_book
    clob_book.init("https://clob.polymarket.com")

    # Build CLOB client for live trading
    clob_client = None
    if not cfg.dry_run and cfg.private_key:
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
            clob_client = ClobClient(
                "https://clob.polymarket.com",
                key=cfg.private_key,
                chain_id=137,
                funder=cfg.funder_address or None,
                signature_type=2,
            )
            if cfg.clob_api_key:
                clob_client.set_api_creds(ApiCreds(
                    api_key=cfg.clob_api_key,
                    api_secret=cfg.clob_api_secret,
                    api_passphrase=cfg.clob_api_passphrase,
                ))
            clob_client.get_ok()
            log.info("CLOB client connected — LIVE trading enabled")
        except Exception:
            log.exception("CLOB client init FAILED — falling back to paper mode")
            clob_client = None

    price_cache = PriceCache()
    price_cache.preload_from_disk()

    # Start Chainlink real-time WebSocket (exact oracle Polymarket resolves against)
    from killshot.chainlink_ws import ChainlinkWS
    chainlink_ws = ChainlinkWS()
    chainlink_ws.start()
    log.info("Chainlink RTDS feed starting...")

    # Start CLOB orderbook WebSocket (replaces REST polling)
    clob_ws = ClobWS()
    clob_ws.start()
    log.info("CLOB orderbook WebSocket starting...")

    # Phase 2d: Start Binance @aggTrade leading indicator
    binance_agg = None
    if cfg.binance_agg_enabled:
        try:
            from killshot.binance_agg import BinanceAggWS
            binance_agg = BinanceAggWS(cfg.assets)
            binance_agg.start()
            log.info("Binance @aggTrade feed starting...")
        except Exception:
            log.exception("Binance aggTrade init failed — continuing without")

    binance_feed = BinanceFeed(bot_cfg, price_cache)
    window_tracker = WindowTracker(bot_cfg, price_cache)
    tracker = PaperTracker()
    engine = KillshotEngine(
        cfg, price_cache, tracker, clob_client=clob_client,
        chainlink_ws=chainlink_ws, clob_ws=clob_ws,
        binance_agg=binance_agg,
    )

    # Start 50-trade evaluation tracker
    eval_tracker = EvalTracker()
    eval_tracker.start()

    # Start Binance WebSocket feed (runs in daemon threads)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(binance_feed.start())
    log.info("Binance feed started")

    # Wait for first price data (Chainlink or Binance)
    log.info("Waiting for price data...")
    for _ in range(30):
        if chainlink_ws.get_price("bitcoin") or price_cache.get_price("bitcoin"):
            break
        time.sleep(1)
    else:
        log.warning("No BTC price after 30s — continuing anyway")

    btc = price_cache.get_price("bitcoin")
    if btc:
        log.info("BTC price: $%.2f — feed alive", btc)

    # Check Rust executor health
    if cfg.rust_executor_enabled:
        try:
            import httpx as hx
            resp = hx.get(f"{cfg.rust_executor_url}/health", timeout=3.0)
            if resp.status_code == 200:
                health = resp.json()
                log.info(
                    "Rust executor: UP (uptime=%.0fs, orders=%d, avg_latency=%.1fms)",
                    health.get("uptime_s", 0),
                    health.get("orders_sent", 0),
                    health.get("avg_latency_ms", 0),
                )
            else:
                log.warning("Rust executor: returned %d — will fall back to Python", resp.status_code)
        except Exception:
            log.warning("Rust executor: NOT RUNNING at %s — will fall back to Python", cfg.rust_executor_url)

    last_scan = 0.0
    last_status_write = 0.0
    last_cleanup = 0.0

    log.info("Entering main loop...")

    while _running:
        try:
            now = time.time()

            # Keep Binance feed alive
            binance_feed.ensure_alive()

            # Periodic market scan
            if now - last_scan > cfg.scan_interval_s:
                markets = _scan_markets(cfg.assets, cfg.market_types)
                if markets:
                    window_tracker.update(markets)
                active = len(window_tracker.all_active_windows())
                log.info(
                    "Scan: %d markets found, %d active windows (types=%s)",
                    len(markets), active, cfg.market_types,
                )

                # Update CLOB WS subscriptions for active windows
                token_ids = set()
                for w in window_tracker.all_active_windows():
                    if w.up_token_id:
                        token_ids.add(w.up_token_id)
                    if w.down_token_id:
                        token_ids.add(w.down_token_id)
                if token_ids:
                    clob_ws.update_subscriptions(token_ids)

                last_scan = now

            # Engine tick — evaluate kill zones
            engine.tick(window_tracker.all_active_windows())

            # Resolve completed paper trades
            resolved = tracker.resolve_trades(price_cache)
            if resolved:
                engine.report_resolved(resolved)
                # Feed to 50-trade evaluator
                for t in resolved:
                    if t.outcome in ("win", "loss"):
                        remaining = max(0, int(t.window_end_ts - t.timestamp))
                        eval_tracker.record_trade(
                            direction=t.direction,
                            asset=t.asset,
                            entry_c=int(t.entry_price * 100),
                            size=t.size_usd,
                            t_left=remaining,
                            result=t.outcome.upper(),
                            pnl=t.pnl,
                        )

            # Periodic cleanup (every hour)
            if now - last_cleanup > 3600:
                engine.cleanup_expired()
                last_cleanup = now

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
    eval_tracker.stop()
    clob_ws.stop()
    chainlink_ws.stop()
    if binance_agg:
        binance_agg.stop()
    tracker.write_status()
    price_cache.save_candles()
    binance_feed._running = False
    loop.close()
    log.info("Killshot stopped.")


if __name__ == "__main__":
    main()
