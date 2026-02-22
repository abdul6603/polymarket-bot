"""Shared utilities for dashboard routes — SSH proxy for fresh data from Pro."""
from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Cache SSH results briefly to avoid hammering Pro with SSH calls
_ssh_cache: dict[str, tuple[float, dict | list | None]] = {}
_SSH_CACHE_TTL = 30  # seconds


def _read_pro_file(remote_path: str) -> dict | list | None:
    """Read a file from Pro via SSH. Returns parsed JSON or None."""
    # Check cache first
    cached = _ssh_cache.get(remote_path)
    if cached and (time.time() - cached[0]) < _SSH_CACHE_TTL:
        return cached[1]

    try:
        result = subprocess.run(
            ["ssh", "pro", "cat", remote_path],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            _ssh_cache[remote_path] = (time.time(), data)
            return data
    except Exception:
        pass
    return None


def read_fresh(local_path: Path, remote_path: str, stale_seconds: int = 120) -> dict:
    """Load local JSON; if stale (>stale_seconds), fetch from Pro via SSH.

    Args:
        local_path: Path to local JSON file
        remote_path: Remote path on Pro (e.g. ~/polymarket-bot/data/file.json)
        stale_seconds: Max age before fetching from Pro (default 2 min)

    Returns:
        Parsed JSON dict (or empty dict if both fail)
    """
    data = {}
    if local_path.exists():
        try:
            data = json.loads(local_path.read_text())
        except Exception:
            pass
        # Check freshness via mtime
        age_s = time.time() - local_path.stat().st_mtime
        if age_s < stale_seconds:
            return data

    # Stale or missing — try Pro
    pro_data = _read_pro_file(remote_path)
    if pro_data and isinstance(pro_data, dict):
        return pro_data
    return data


def read_fresh_list(local_path: Path, remote_path: str, stale_seconds: int = 120) -> list:
    """Same as read_fresh but for JSON arrays."""
    data = []
    if local_path.exists():
        try:
            data = json.loads(local_path.read_text())
        except Exception:
            pass
        age_s = time.time() - local_path.stat().st_mtime
        if age_s < stale_seconds:
            return data if isinstance(data, list) else []

    pro_data = _read_pro_file(remote_path)
    if pro_data and isinstance(pro_data, list):
        return pro_data
    return data if isinstance(data, list) else []


def read_fresh_jsonl(local_path: Path, remote_path: str, stale_seconds: int = 120) -> list[dict]:
    """Read a JSONL file; if stale, fetch from Pro."""
    lines = []
    if local_path.exists():
        age_s = time.time() - local_path.stat().st_mtime
        if age_s < stale_seconds:
            try:
                for line in local_path.read_text().strip().split("\n"):
                    if line.strip():
                        lines.append(json.loads(line))
                return lines
            except Exception:
                pass

    # Stale — fetch from Pro
    try:
        result = subprocess.run(
            ["ssh", "pro", "cat", remote_path],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    lines.append(json.loads(line))
            return lines
    except Exception:
        pass

    # Fallback to local
    if local_path.exists():
        try:
            for line in local_path.read_text().strip().split("\n"):
                if line.strip():
                    lines.append(json.loads(line))
        except Exception:
            pass
    return lines
