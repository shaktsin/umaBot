from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .loader import SkillMetadata, load_skill_metadata


@dataclass
class Skill:
    metadata: SkillMetadata
    path: Path


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: Dict[str, Skill] = {}
        self._dirs: List[Path] = []

    def load_from_dirs(self, dirs: List[Path]) -> None:
        self._dirs = list(dirs)
        self._skills = {}
        for skill_dir in dirs:
            if not skill_dir.exists():
                continue
            for child in skill_dir.iterdir():
                if not child.is_dir():
                    continue
                metadata = load_skill_metadata(child)
                if metadata:
                    self._skills[metadata.name] = Skill(metadata=metadata, path=child)

    def refresh(self) -> None:
        if self._dirs:
            self.load_from_dirs(self._dirs)

    def list(self) -> List[Skill]:
        return list(self._skills.values())

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def match_trigger(self, text: str) -> Optional[Skill]:
        text_lower = text.lower()
        for skill in self._skills.values():
            for trigger in skill.metadata.triggers:
                if trigger.lower() in text_lower:
                    return skill
        return None


def lint_skill_dir(path: Path) -> List[str]:
    errors: List[str] = []
    metadata = None
    try:
        metadata = load_skill_metadata(path)
    except Exception as exc:
        errors.append(str(exc))
    if not metadata:
        errors.append("Missing or invalid SKILL.md")
        return errors
    for script_name, script_def in metadata.scripts.items():
        script_rel = str(script_def.get("path", "")).strip()
        script_path = (path / script_rel).resolve()
        if path.resolve() not in script_path.parents:
            errors.append(f"scripts.{script_name} points outside skill directory")
            continue
        if not script_path.exists():
            errors.append(f"scripts.{script_name} missing file: {script_rel}")
    return errors
