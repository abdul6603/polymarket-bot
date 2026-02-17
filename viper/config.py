"""ViperConfig — frozen dataclass for revenue hunting configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


@dataclass(frozen=True)
class ViperConfig:
    openai_api_key: str = _env("OPENAI_API_KEY")
    cycle_minutes: int = int(_env("VIPER_CYCLE_MINUTES", "60"))
    dry_run: bool = _env("VIPER_DRY_RUN", "true").lower() in ("true", "1", "yes")
    min_opportunity_score: int = int(_env("VIPER_MIN_OPPORTUNITY_SCORE", "60"))
    shelby_tasks_file: Path = Path("/Users/abdallaalhamdan/shelby/data/tasks.json")
    mercury_analytics_file: Path = Path("/Users/abdallaalhamdan/mercury/data/analytics.json")
