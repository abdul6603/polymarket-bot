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
    """Execute selected trades on Polymarket CLOB.

    Uses py_clob_client for authenticated order placement.
    Respects dry_run mode.
    """
    results: list[OrderResult] = []

    if not trades:
        log.info("No trades to execute")
        return results

    if cfg.dry_run:
        log.info("[DRY RUN] Would execute %d trades:", len(trades))
        for t in trades:
            log.info(
                "  [DRY RUN] %s %s on %s | edge=%.1f%% | size=$%.2f | oracle=%.1f%% vs market=%.1f%%",
                t.side, t.market.asset.upper(), t.market.question[:50],
                t.edge_abs * 100, t.size, t.oracle_prob * 100, t.market_prob * 100,
            )
            results.append(OrderResult(
                signal=t,
                success=True,
                order_id=f"dry_run_{int(time.time())}",
                fill_price=t.market_prob,
            ))
        return results

    # Live execution
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
            # Determine token ID (YES or NO)
            tokens = trade.market.tokens
            if not tokens or len(tokens) < 2:
                log.warning("No tokens for %s, skipping", trade.market.condition_id[:12])
                results.append(OrderResult(signal=trade, success=False, error="no tokens"))
                continue

            # tokens[0] = YES, tokens[1] = NO typically
            if trade.side == "YES":
                token_id = tokens[0].get("token_id", "")
                price = trade.market_prob  # buy at current market price
            else:
                token_id = tokens[1].get("token_id", "")
                price = trade.market.no_price

            # Calculate shares from dollar amount
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
                "Placing order: %s %s | %s @ $%.2f | size=$%.2f (%.1f shares)",
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
            log.info("Order placed: %s | id=%s", trade.market.asset.upper(), order_id[:16])

            time.sleep(1)  # rate limit between orders

        except Exception as e:
            log.error("Order failed for %s: %s", trade.market.condition_id[:12], e)
            results.append(OrderResult(signal=trade, success=False, error=str(e)))

    placed = sum(1 for r in results if r.success)
    log.info("Execution complete: %d/%d orders placed", placed, len(trades))
    return results
