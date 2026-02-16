from __future__ import annotations

import logging

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

from bot.config import Config

log = logging.getLogger(__name__)


def build_client(cfg: Config) -> ClobClient:
    """Create and verify a ClobClient with L1/L2 authentication."""
    client = ClobClient(
        cfg.clob_host,
        key=cfg.private_key,
        chain_id=137,
        funder=cfg.funder_address if cfg.funder_address else None,
    )

    if cfg.clob_api_key:
        client.set_api_creds(ApiCreds(
            api_key=cfg.clob_api_key,
            api_secret=cfg.clob_api_secret,
            api_passphrase=cfg.clob_api_passphrase,
        ))
        log.info("L2 API credentials set")
    else:
        log.warning("No CLOB API key configured — L1 auth only")

    try:
        resp = client.get_ok()
        log.info("CLOB connection OK: %s", resp)
    except Exception:
        log.exception("CLOB connection check failed")

    return client
