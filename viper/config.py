"""ViperConfig — 24/7 market intelligence engine configuration."""
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
    # Intelligence APIs
    tavily_api_key: str = _env("TAVILY_API_KEY")
    openai_api_key: str = _env("OPENAI_API_KEY")

    # Polymarket CLOB for activity scanning
    clob_host: str = _env("CLOB_HOST", "https://clob.polymarket.com")

    # Cycle: 5 minutes for real-time intelligence
    cycle_minutes: int = int(_env("VIPER_CYCLE_MINUTES", "5"))
    dry_run: bool = _env("VIPER_DRY_RUN", "true").lower() in ("true", "1", "yes")

    # Shelby integration
    shelby_tasks_file: Path = Path("/Users/abdallaalhamdan/shelby/data/tasks.json")
    mercury_analytics_file: Path = Path("/Users/abdallaalhamdan/mercury/data/analytics.json")
