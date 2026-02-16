"""Shared HTTP session with automatic retry and backoff.

Handles transient DNS resolution failures, connection resets, and
server-side 5xx errors that would otherwise crash the bot.
"""
from __future__ import annotations

import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

# Retry strategy: 3 retries with exponential backoff (0.5s, 1s, 2s)
# Retries on connection errors (DNS failures, resets) AND server errors (502/503/504)
_RETRY_STRATEGY = Retry(
    total=3,
    backoff_factor=0.5,           # 0.5s -> 1s -> 2s between retries
    status_forcelist=[429, 502, 503, 504],
    allowed_methods=["GET", "POST", "PUT", "DELETE"],
    raise_on_status=False,        # let caller inspect status codes
)

_session: requests.Session | None = None


def get_session() -> requests.Session:
    """Return a module-level session with retry adapter mounted.

    The session is created once and reused across the entire bot lifetime.
    This also gives us connection pooling for free.
    """
    global _session
    if _session is None:
        _session = requests.Session()
        adapter = HTTPAdapter(max_retries=_RETRY_STRATEGY)
        _session.mount("https://", adapter)
        _session.mount("http://", adapter)
        log.info("HTTP session initialized with retry strategy (3 retries, exponential backoff)")
    return _session
