"""Brain Notes Reader — shared utility for all agents to read their brain notes.

Brain notes are stored by the dashboard at:
    ~/polymarket-bot/bot/data/brains/{agent}.json

Each file has: {"agent": "name", "notes": [{"id", "topic", "content", "tags", "created_at", "source"}, ...]}

Safe to call every tick — just reads a tiny JSON file, returns [] if missing.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

BRAIN_DIR = Path(__file__).parent / "data" / "brains"


def read_brain_notes(agent: str) -> list[dict]:
    """Read all brain notes for an agent. Returns list of note dicts, [] if none."""
    brain_file = BRAIN_DIR / f"{agent}.json"
    if not brain_file.exists():
        return []
    try:
        data = json.loads(brain_file.read_text())
        return data.get("notes", [])
    except Exception:
        return []


def format_brain_context(agent: str) -> str:
    """Format brain notes as a text block for AI system prompts.

    Returns empty string if no notes exist.
    Used by Shelby to inject brain notes into the GPT system prompt.
    """
    notes = read_brain_notes(agent)
    if not notes:
        return ""

    lines = [f"BRAIN NOTES FROM JORDAN ({len(notes)} notes):"]
    for note in notes:
        topic = note.get("topic", "untitled")
        content = note.get("content", "")
        tags = note.get("tags", [])
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        lines.append(f"- [{topic}]{tag_str}: {content}")

    return "\n".join(lines)
