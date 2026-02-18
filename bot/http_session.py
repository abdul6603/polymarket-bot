"""Shared HTTP session with automatic retry and backoff.

Handles transient DNS resolution failures, connection resets, and
server-side 5xx errors that would otherwise crash the bot.
"""
from __future__ import annotations

import logging
import threading

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
_session_lock = threading.Lock()

# Default timeout for all requests (connect, read) in seconds
DEFAULT_TIMEOUT = 30


def get_session() -> requests.Session:
    """Return a module-level session with retry adapter mounted.

    The session is created once and reused across the entire bot lifetime.
    This also gives us connection pooling for free.
    Thread-safe: uses a lock to prevent duplicate creation.
    """
    global _session
    if _session is not None:
        return _session
    with _session_lock:
        if _session is not None:
            return _session
        s = requests.Session()
        adapter = HTTPAdapter(max_retries=_RETRY_STRATEGY)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        log.info("HTTP session initialized with retry strategy (3 retries, exponential backoff)")
        _session = s
    return _session
