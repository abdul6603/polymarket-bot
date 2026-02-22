"""Discord Alpha Scraper — Configuration."""
from __future__ import annotations

import os

# Auth — token MUST be set via environment variable or .env file
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
SERVER_ID = 1337061918908878918

# Channel map: id → config
CHANNELS = {
    1338523694867157074: {
        "name": "sn06-trades",
        "priority": "CRITICAL",
        "consumers": ["odin", "garves", "oracle"],
        "vision": False,
    },
    1370024157663723673: {
        "name": "kiku-trades",
        "priority": "CRITICAL",
        "consumers": ["odin", "garves", "oracle"],
        "vision": False,
    },
    1337062678295875604: {
        "name": "charts-ideas",
        "priority": "CONTEXT",
        "consumers": ["atlas", "quant", "garves", "odin"],
        "vision": False,
    },
    1339901629947842604: {
        "name": "ut-education",
        "priority": "KNOWLEDGE",
        "consumers": ["atlas"],
        "vision": True,
    },
    1446572692839989338: {
        "name": "abns92-trial",
        "priority": "MEDIUM",
        "consumers": ["atlas", "garves", "odin", "oracle"],
        "vision": True,
    },
    1429975783706853446: {
        "name": "miku-trades",
        "priority": "MEDIUM",
        "consumers": ["atlas", "garves", "odin", "oracle"],
        "vision": True,
    },
}

CHANNEL_IDS = list(CHANNELS.keys())

# Rate limits — stay under Discord radar
POLL_INTERVAL_SECONDS = 60  # check each channel every 60s
MESSAGE_FETCH_LIMIT = 10    # last N messages per poll
VISION_DAILY_CAP = 10       # max vision LLM calls per day

# Paths
DATA_DIR_NAME = "discord_scraper"
DB_NAME = "discord_intel.db"
