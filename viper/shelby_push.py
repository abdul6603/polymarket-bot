"""Shelby Integration — push high-value opportunities to Shelby's task queue."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from viper.config import ViperConfig
from viper.scanner import Opportunity

log = logging.getLogger(__name__)

ET = timezone(timedelta(hours=-5))


def push_to_shelby(cfg: ViperConfig, opp: Opportunity, score: int) -> bool:
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
        "title": f"[VIPER] {opp.title[:100]}",
        "description": (
            f"Source: {opp.source}\n"
            f"Value: ${opp.estimated_value_usd:.0f}\n"
            f"Effort: {opp.effort_hours:.0f}h\n"
            f"Score: {score}/100\n"
            f"URL: {opp.url}\n"
            f"Category: {opp.category}\n"
            f"Tags: {', '.join(opp.tags)}\n\n"
            f"{opp.description[:300]}"
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
        log.info("Pushed to Shelby: [VIPER] %s (score=%d)", opp.title[:50], score)
        return True
    except Exception:
        log.exception("Failed to push to Shelby")
        return False
