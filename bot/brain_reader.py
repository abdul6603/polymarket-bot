"""Brain Notes Reader — shared utility for all agents to read their brain notes.

Brain notes are stored by the dashboard at:
    ~/polymarket-bot/bot/data/brains/{agent}.json

Each file has: {"agent": "name", "notes": [{"id", "topic", "content", "type", "tags", "created_at", "source"}, ...]}

Note types:
    - "note": General info/instructions for the agent
    - "command": Direct order to follow or execute
    - "memory": Persistent context the agent should always remember

Safe to call every tick — just reads a tiny JSON file, returns [] if missing.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

BRAIN_DIR = Path(__file__).parent / "data" / "brains"


def read_brain_notes(agent: str, note_type: str | None = None) -> list[dict]:
    """Read brain notes for an agent. Optionally filter by type.

    Args:
        agent: Agent name (garves, shelby, atlas, etc.)
        note_type: Optional filter — "note", "command", or "memory". None returns all.

    Returns list of note dicts, [] if none.
    """
    brain_file = BRAIN_DIR / f"{agent}.json"
    if not brain_file.exists():
        return []
    try:
        data = json.loads(brain_file.read_text())
        notes = data.get("notes", [])
        if note_type:
            notes = [n for n in notes if n.get("type", "note") == note_type]
        return notes
    except Exception as e:
        log.warning("Failed to read brain notes for %s: %s", agent, e)
        return []


def format_brain_context(agent: str) -> str:
    """Format brain notes as a text block for AI system prompts.

    Returns empty string if no notes exist.
    Used by Shelby to inject brain notes into the GPT system prompt.
    Groups by type so commands are clearly separated from memories and notes.
    """
    notes = read_brain_notes(agent)
    if not notes:
        return ""

    type_labels = {"command": "COMMANDS", "memory": "MEMORIES", "note": "NOTES"}
    grouped: dict[str, list[dict]] = {}
    for note in notes:
        ntype = note.get("type", "note")
        grouped.setdefault(ntype, []).append(note)

    lines = [f"BRAIN NOTES FROM JORDAN ({len(notes)} total):"]

    # Commands first (highest priority), then memories, then notes
    for ntype in ("command", "memory", "note"):
        group = grouped.get(ntype, [])
        if not group:
            continue
        lines.append(f"\n{type_labels.get(ntype, 'OTHER')} ({len(group)}):")
        for note in group:
            topic = note.get("topic", "untitled")
            content = note.get("content", "")
            lines.append(f"  - [{topic}]: {content}")

    return "\n".join(lines)
