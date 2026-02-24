"""Oracle executor — places orders on Polymarket CLOB."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oracle.config import OracleConfig
from oracle.edge_calculator import TradeSignal

log = logging.getLogger(__name__)


@dataclass
class OrderResult:
    """Result of an order placement."""
    signal: TradeSignal
    success: bool
    order_id: str = ""
    fill_price: float = 0.0
    error: str = ""


def execute_trades(
    cfg: OracleConfig,
    trades: list[TradeSignal],
) -> list[OrderResult]:
    """Execute selected trades on Polymarket CLOB and/or Kalshi.

    Routes trades based on condition_id prefix:
    - 'kalshi_*' → Kalshi authenticated API
    - everything else → Polymarket CLOB

    Respects dry_run mode for both exchanges.
    """
    results: list[OrderResult] = []

    if not trades:
        log.info("No trades to execute")
        return results

    # Split trades by exchange
    poly_trades = [t for t in trades if not t.market.condition_id.startswith("kalshi_")]
    kalshi_trades = [t for t in trades if t.market.condition_id.startswith("kalshi_")]

    if cfg.dry_run:
        log.info("[DRY RUN] Would execute %d trades (%d Poly + %d Kalshi):",
                 len(trades), len(poly_trades), len(kalshi_trades))
        for t in trades:
            exchange = "KALSHI" if t.market.condition_id.startswith("kalshi_") else "POLY"
            log.info(
                "  [DRY RUN][%s] %s %s on %s | edge=%.1f%% | size=$%.2f | oracle=%.1f%% vs market=%.1f%%",
                exchange, t.side, t.market.asset.upper(), t.market.question[:50],
                t.edge_abs * 100, t.size, t.oracle_prob * 100, t.market_prob * 100,
            )
            results.append(OrderResult(
                signal=t,
                success=True,
                order_id=f"dry_run_{exchange.lower()}_{int(time.time())}",
                fill_price=t.market_prob,
            ))
        return results

    # Live execution: Polymarket trades
    if poly_trades:
        results += _execute_polymarket(cfg, poly_trades)

    # Live execution: Kalshi trades
    if kalshi_trades and cfg.kalshi_enabled:
        results += _execute_kalshi(cfg, kalshi_trades)

    placed = sum(1 for r in results if r.success)
    log.info("Execution complete: %d/%d orders placed", placed, len(trades))
    return results


def _execute_polymarket(cfg: OracleConfig, trades: list[TradeSignal]) -> list[OrderResult]:
    """Execute trades on Polymarket CLOB."""
    results: list[OrderResult] = []

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType
        from bot.config import Config as GarvesConfig

        garves_cfg = GarvesConfig()
        client = ClobClient(
            garves_cfg.clob_host,
            key=garves_cfg.private_key,
            chain_id=garves_cfg.chain_id,
            signature_type=2,
            funder=garves_cfg.funder_address,
        )
        client.set_api_creds(client.create_or_derive_api_creds())
    except Exception as e:
        log.error("Failed to initialize CLOB client: %s", e)
        return [OrderResult(signal=t, success=False, error=str(e)) for t in trades]

    for trade in trades:
        try:
            tokens = trade.market.tokens
            if not tokens or len(tokens) < 2:
                log.warning("No tokens for %s, skipping", trade.market.condition_id[:12])
                results.append(OrderResult(signal=trade, success=False, error="no tokens"))
                continue

            if trade.side == "YES":
                token_id = tokens[0].get("token_id", "")
                price = trade.market_prob
            else:
                token_id = tokens[1].get("token_id", "")
                price = trade.market.no_price

            if price <= 0 or price >= 1:
                results.append(OrderResult(signal=trade, success=False, error="invalid price"))
                continue
            shares = trade.size / price

            order_args = OrderArgs(
                price=round(price, 2),
                size=round(shares, 2),
                side="BUY",
                token_id=token_id,
            )

            log.info(
                "[POLY] Placing order: %s %s | %s @ $%.2f | size=$%.2f (%.1f shares)",
                trade.side, trade.market.asset.upper(),
                trade.market.question[:40], price, trade.size, shares,
            )

            resp = client.create_and_post_order(order_args)
            order_id = ""
            if isinstance(resp, dict):
                order_id = resp.get("orderID", resp.get("id", ""))
            elif hasattr(resp, "orderID"):
                order_id = resp.orderID

            results.append(OrderResult(
                signal=trade,
                success=True,
                order_id=str(order_id),
                fill_price=price,
            ))
            log.info("[POLY] Order placed: %s | id=%s", trade.market.asset.upper(), order_id[:16])
            time.sleep(1)

        except Exception as e:
            log.error("[POLY] Order failed for %s: %s", trade.market.condition_id[:12], e)
            results.append(OrderResult(signal=trade, success=False, error=str(e)))

    return results


def _execute_kalshi(cfg: OracleConfig, trades: list[TradeSignal]) -> list[OrderResult]:
    """Execute trades on Kalshi."""
    results: list[OrderResult] = []

    if not cfg.kalshi_api_key or not cfg.kalshi_private_key_path:
        log.warning("[KALSHI] No API credentials configured — skipping %d trades", len(trades))
        return [OrderResult(signal=t, success=False, error="no kalshi credentials") for t in trades]

    try:
        from bot.kalshi_client import KalshiClient
        client = KalshiClient(
            api_key=cfg.kalshi_api_key,
            private_key_path=cfg.kalshi_private_key_path,
        )
    except Exception as e:
        log.error("[KALSHI] Failed to initialize client: %s", e)
        return [OrderResult(signal=t, success=False, error=str(e)) for t in trades]

    for trade in trades:
        try:
            # Extract Kalshi ticker from condition_id
            ticker = trade.market.condition_id.replace("kalshi_", "", 1)
            side = trade.side.lower()  # "yes" or "no"

            # Price in cents for Kalshi
            price_dollars = trade.market_prob if trade.side == "YES" else trade.market.no_price
            price_cents = max(1, min(99, int(round(price_dollars * 100))))
            count = max(1, int(trade.size / price_dollars)) if price_dollars > 0 else 1

            log.info(
                "[KALSHI] Placing order: %s %s | %s x%d @ %d¢ | size=$%.2f",
                side.upper(), trade.market.asset.upper(),
                trade.market.question[:40], count, price_cents, trade.size,
            )

            order_data = client.place_order(
                ticker=ticker,
                side=side,
                action="buy",
                count=count,
                type="limit",
                yes_price=price_cents if side == "yes" else None,
                no_price=price_cents if side == "no" else None,
            )
            order_id = order_data.get("order_id", "unknown")

            results.append(OrderResult(
                signal=trade,
                success=True,
                order_id=str(order_id),
                fill_price=price_dollars,
            ))
            log.info("[KALSHI] Order placed: %s | id=%s", trade.market.asset.upper(), order_id[:16])
            time.sleep(1)

        except Exception as e:
            log.error("[KALSHI] Order failed for %s: %s", trade.market.condition_id[:12], e)
            results.append(OrderResult(signal=trade, success=False, error=str(e)))

    return results
