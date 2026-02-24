"""Authenticated Kalshi API client — shared by Hawk + Oracle.

Kalshi uses RSA-PSS signing for authenticated endpoints.
Read-only market data uses the existing unauthenticated hawk/kalshi.py cache.
This client adds account + trading capabilities.

Auth flow:
  1. Generate timestamp (ms since epoch)
  2. Build message = f"{timestamp}{method}{path}"
  3. Sign with RSA-PSS (SHA-256, max salt length)
  4. Send as headers: KALSHI-ACCESS-KEY, KALSHI-ACCESS-SIGNATURE, KALSHI-ACCESS-TIMESTAMP
"""
from __future__ import annotations

import base64
import logging
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, ec

from bot.http_session import get_session

log = logging.getLogger(__name__)

_BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"


class KalshiClient:
    """Authenticated Kalshi API client."""

    def __init__(
        self,
        api_key: str,
        private_key_path: str,
        base_url: str = _BASE_URL,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._session = get_session()
        self._private_key = self._load_key(private_key_path)

    @staticmethod
    def _load_key(path: str):
        """Load RSA or EC private key from PEM file."""
        pem_path = Path(path).expanduser()
        if not pem_path.exists():
            raise FileNotFoundError(f"Kalshi private key not found: {pem_path}")
        pem_data = pem_path.read_bytes()
        key = serialization.load_pem_private_key(pem_data, password=None)
        return key

    def _sign_request(self, method: str, path: str, timestamp: str) -> str:
        """Sign request with RSA-PSS or ECDSA depending on key type.

        Kalshi supports both RSA and EC keys.
        Message format: "{timestamp}{method}{path}"
        """
        message = f"{timestamp}{method}{path}".encode()

        if isinstance(self._private_key, ec.EllipticCurvePrivateKey):
            signature = self._private_key.sign(
                message,
                ec.ECDSA(hashes.SHA256()),
            )
        else:
            # RSA-PSS
            signature = self._private_key.sign(
                message,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
        return base64.b64encode(signature).decode()

    def _headers(self, method: str, path: str) -> dict:
        """Build authenticated headers for Kalshi API request."""
        timestamp = str(int(time.time() * 1000))
        sig = self._sign_request(method, path, timestamp)
        return {
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, params: dict | None = None,
                 json_body: dict | None = None) -> dict:
        """Make authenticated API request."""
        url = f"{self.base_url}{path}"
        headers = self._headers(method.upper(), path)

        resp = self._session.request(
            method=method.upper(),
            url=url,
            headers=headers,
            params=params,
            json=json_body,
            timeout=15,
        )

        if resp.status_code not in (200, 201):
            log.warning("[KALSHI] %s %s → HTTP %d: %s",
                        method.upper(), path, resp.status_code, resp.text[:200])
            resp.raise_for_status()

        return resp.json()

    # ── Read APIs ──

    def get_markets(self, status: str = "open", cursor: str | None = None,
                    limit: int = 200) -> list[dict]:
        """Fetch markets (authenticated — includes more detail than public endpoint)."""
        params: dict = {"status": status, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        data = self._request("GET", "/markets", params=params)
        return data.get("markets", [])

    def get_market(self, ticker: str) -> dict:
        """Get single market by ticker."""
        return self._request("GET", f"/markets/{ticker}")

    def get_events(self, status: str = "open", cursor: str | None = None) -> list[dict]:
        """Fetch events."""
        params: dict = {"status": status, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        data = self._request("GET", "/events", params=params)
        return data.get("events", [])

    def get_event(self, event_ticker: str) -> dict:
        """Get single event by ticker."""
        return self._request("GET", f"/events/{event_ticker}")

    def get_orderbook(self, ticker: str) -> dict:
        """Get orderbook for a market."""
        return self._request("GET", f"/orderbooks/{ticker}")

    # ── Account APIs ──

    def get_balance(self) -> float:
        """Get account balance in dollars (Kalshi returns cents internally)."""
        data = self._request("GET", "/portfolio/balance")
        # Kalshi returns balance in cents
        balance_cents = data.get("balance", 0)
        return balance_cents / 100.0

    def get_positions(self) -> list[dict]:
        """Get all open positions."""
        data = self._request("GET", "/portfolio/positions")
        positions = data.get("market_positions", [])
        return positions

    # ── Trading APIs ──

    def place_order(
        self,
        ticker: str,
        side: str,
        action: str = "buy",
        count: int = 1,
        type: str = "market",
        yes_price: int | None = None,
        no_price: int | None = None,
    ) -> dict:
        """Place an order on Kalshi.

        Args:
            ticker: Market ticker (e.g. "KXBTC-25FEB28-B95000")
            side: "yes" or "no"
            action: "buy" or "sell"
            count: Number of contracts
            type: "market" or "limit"
            yes_price: Limit price in cents (1-99) for YES side
            no_price: Limit price in cents (1-99) for NO side
        """
        body: dict = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "type": type,
        }
        if type == "limit":
            if yes_price is not None:
                body["yes_price"] = yes_price
            if no_price is not None:
                body["no_price"] = no_price

        log.info("[KALSHI] Placing order: %s %s %s x%d @ %s on %s",
                 action, side, type, count, yes_price or no_price or "market", ticker)

        data = self._request("POST", "/portfolio/orders", json_body=body)
        order = data.get("order", data)
        log.info("[KALSHI] Order response: id=%s status=%s",
                 order.get("order_id", "?"), order.get("status", "?"))
        return order

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        try:
            self._request("DELETE", f"/portfolio/orders/{order_id}")
            log.info("[KALSHI] Cancelled order %s", order_id)
            return True
        except Exception:
            log.warning("[KALSHI] Failed to cancel order %s", order_id)
            return False

    def get_order(self, order_id: str) -> dict:
        """Get order status."""
        return self._request("GET", f"/portfolio/orders/{order_id}")

    def get_fills(self, ticker: str | None = None) -> list[dict]:
        """Get order fills, optionally filtered by ticker."""
        params: dict = {}
        if ticker:
            params["ticker"] = ticker
        data = self._request("GET", "/portfolio/fills", params=params)
        return data.get("fills", [])
