"""Shelby Integration — push high-value opportunities to Shelby's task queue."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from viper.config import ViperConfig
from viper.intel import IntelItem

log = logging.getLogger(__name__)

from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York")


def push_to_shelby(cfg: ViperConfig, item: IntelItem, score: int) -> bool:
    """Append task to Shelby's tasks.json with [VIPER] prefix."""
    tasks_file = cfg.shelby_tasks_file

    # Load existing tasks
    tasks = []
    if tasks_file.exists():
        try:
            tasks = json.loads(tasks_file.read_text())
        except Exception:
            log.warning("Could not read Shelby tasks file, starting fresh")
            tasks = []

    # Create task entry
    task = {
        "title": f"[VIPER] {item.headline[:100]}",
        "description": (
            f"Source: {item.source}\n"
            f"Category: {item.category}\n"
            f"Sentiment: {item.sentiment:+.2f}\n"
            f"Confidence: {item.confidence:.0%}\n"
            f"URL: {item.url}\n"
            f"Tags: {', '.join(item.relevance_tags)}\n\n"
            f"{item.summary[:300]}"
        ),
        "priority": "high" if score >= 80 else "normal",
        "status": "pending",
        "from": "viper",
        "created_at": datetime.now(ET).isoformat(),
        "score": score,
        "done": False,
    }

    tasks.append(task)

    try:
        tasks_file.parent.mkdir(parents=True, exist_ok=True)
        tasks_file.write_text(json.dumps(tasks, indent=2))
        log.info("Pushed to Shelby: [VIPER] %s (score=%d)", item.headline[:50], score)
        return True
    except Exception:
        log.exception("Failed to push to Shelby")
        return False
