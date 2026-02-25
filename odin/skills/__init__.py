"""Odin Skills Registry — all 13 skills loaded and accessible."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("odin.skills")


@dataclass
class SkillStatus:
    """Status of a single skill."""
    name: str
    enabled: bool = True
    ready: bool = False
    last_run: float = 0.0
    run_count: int = 0
    error_count: int = 0
    last_error: str = ""


class SkillRegistry:
    """Central registry for all Odin skills."""

    def __init__(self):
        self._skills: dict[str, object] = {}
        self._status: dict[str, SkillStatus] = {}

    def register(self, name: str, skill: object) -> None:
        self._skills[name] = skill
        self._status[name] = SkillStatus(name=name, ready=True)
        log.info("[SKILLS] Registered: %s", name)

    def get(self, name: str) -> Optional[object]:
        return self._skills.get(name)

    def mark_run(self, name: str) -> None:
        import time
        if name in self._status:
            self._status[name].last_run = time.time()
            self._status[name].run_count += 1

    def mark_error(self, name: str, error: str) -> None:
        if name in self._status:
            self._status[name].error_count += 1
            self._status[name].last_error = error[:200]

    def get_all_status(self) -> dict:
        return {
            name: {
                "enabled": s.enabled,
                "ready": s.ready,
                "run_count": s.run_count,
                "error_count": s.error_count,
                "last_error": s.last_error,
            }
            for name, s in self._status.items()
        }

    @property
    def skill_count(self) -> int:
        return len(self._skills)

    @property
    def skill_names(self) -> list[str]:
        return list(self._skills.keys())
