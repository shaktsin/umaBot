"""Skill registry — discovers and matches Agent Skills."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from .loader import SkillMetadata, load_skill_metadata

if TYPE_CHECKING:
    from .runtime import ResolvedRuntime, SkillRuntimeProvisioner


# Common English stopwords to skip during trigger matching
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "it", "in", "on", "to", "of", "or", "and",
    "for", "by", "as", "at", "be", "do", "if", "so", "no", "not", "but",
    "are", "was", "has", "had", "any", "all", "its", "can", "may", "use",
    "this", "that", "with", "from", "when", "what", "how", "you", "your",
    "will", "such", "also", "into", "than", "them", "then", "they", "been",
    "have", "each", "make", "like", "does", "used", "using", "should",
})

# Minimum word length to be considered a keyword
_MIN_KEYWORD_LEN = 3


@dataclass
class Skill:
    metadata: SkillMetadata
    path: Path
    # Populated at load time by SkillRuntimeProvisioner when configured.
    # Contains the resolved env dict for subprocess calls and the python binary path.
    resolved_runtime: Optional["ResolvedRuntime"] = field(default=None, compare=False)


class SkillRegistry:
    def __init__(self, provisioner: Optional["SkillRuntimeProvisioner"] = None) -> None:
        self._skills: Dict[str, Skill] = {}
        self._dirs: List[Path] = []
        self._provisioner = provisioner

    def set_provisioner(self, provisioner: "SkillRuntimeProvisioner") -> None:
        """Attach a runtime provisioner. Applied on next load/refresh."""
        self._provisioner = provisioner

    def load_from_dirs(self, dirs: List[Path]) -> None:
        self._dirs = list(dirs)
        self._skills = {}
        for skill_dir in dirs:
            if not skill_dir.exists():
                continue
            for child in skill_dir.iterdir():
                if not child.is_dir():
                    continue
                try:
                    metadata = load_skill_metadata(child)
                except ValueError:
                    continue
                if metadata:
                    skill = Skill(metadata=metadata, path=child)
                    if self._provisioner:
                        try:
                            skill.resolved_runtime = self._provisioner.provision(skill)
                        except Exception as exc:
                            import logging
                            logging.getLogger("umabot.skills.registry").warning(
                                "Runtime provisioning failed for skill '%s': %s",
                                metadata.name,
                                exc,
                            )
                    self._skills[metadata.name] = skill

    def refresh(self) -> None:
        if self._dirs:
            self.load_from_dirs(self._dirs)

    def list(self) -> List[Skill]:
        return list(self._skills.values())

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def match_trigger(self, text: str) -> Optional[Skill]:
        """Match user text against skill descriptions.

        Extracts keywords from each skill's description and checks if
        the user text contains any of them.  Returns the best match
        (highest keyword overlap).
        """
        if not text:
            return None
        text_words = set(_tokenize(text.lower()))

        best_skill: Optional[Skill] = None
        best_score = 0

        for skill in self._skills.values():
            keywords = _extract_keywords(skill.metadata.description)
            overlap = text_words & keywords
            if len(overlap) > best_score:
                best_score = len(overlap)
                best_skill = skill

        # Require at least 1 keyword match
        return best_skill if best_score >= 1 else None


def _tokenize(text: str) -> List[str]:
    """Split text into lowercase alphanumeric tokens."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _extract_keywords(description: str) -> set[str]:
    """Extract significant keywords from a skill description."""
    tokens = _tokenize(description)
    return {
        word
        for word in tokens
        if len(word) >= _MIN_KEYWORD_LEN and word not in _STOPWORDS
    }


def lint_skill_dir(path: Path) -> List[str]:
    """Validate a skill directory. Returns list of error messages."""
    errors: List[str] = []
    try:
        metadata = load_skill_metadata(path)
    except ValueError as exc:
        errors.append(str(exc))
        return errors
    if not metadata:
        errors.append("Missing or invalid SKILL.md")
    return errors
