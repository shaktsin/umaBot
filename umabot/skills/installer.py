"""Skill installation and management."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger("umabot.skills.installer")


class SkillInstaller:
    """Manages skill installation from various sources."""

    def __init__(self, skills_dir: Path, config_path: Optional[str] = None):
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        # Path to config.yaml — used to auto-register skill entries after install.
        self._config_path = config_path

    def install(self, source: str, name: Optional[str] = None) -> tuple[bool, str]:
        """Install skill from source (Git URL or local path)."""
        logger.info("Installing skill from source=%s name=%s", source, name)

        if source.startswith(("http://", "https://", "git@")):
            ok, msg = self._install_from_git(source, name)
        else:
            ok, msg = self._install_from_path(Path(source), name)

        if ok:
            # Derive the installed skill name from the message if not explicit
            skill_name = name or _extract_skill_name(source)
            self._register_in_config(skill_name)

        return ok, msg

    def _install_from_git(self, url: str, name: Optional[str] = None) -> tuple[bool, str]:
        parsed = urlparse(url)
        repo_name = Path(parsed.path).stem
        skill_name = name or repo_name
        skill_path = self.skills_dir / skill_name

        if skill_path.exists():
            return False, f"Skill '{skill_name}' already exists at {skill_path}"

        logger.debug("Cloning from git url=%s to path=%s", url, skill_path)
        try:
            subprocess.run(
                ["git", "clone", url, str(skill_path)],
                capture_output=True, text=True, check=True,
            )
        except subprocess.CalledProcessError as exc:
            logger.error("Git clone failed: %s", exc.stderr)
            return False, f"Git clone failed: {exc.stderr}"
        except FileNotFoundError:
            return False, "Git not found. Please install git."

        if not (skill_path / "SKILL.md").exists():
            shutil.rmtree(skill_path, ignore_errors=True)
            return False, "No SKILL.md found in repository"

        return True, f"Skill '{skill_name}' installed to {skill_path}"

    def _install_from_path(self, path: Path, name: Optional[str] = None) -> tuple[bool, str]:
        source_path = path.resolve()
        if not source_path.exists():
            return False, f"Path does not exist: {source_path}"
        if not (source_path / "SKILL.md").exists():
            return False, f"No SKILL.md found in {source_path}"

        skill_name = name or source_path.name
        skill_path = self.skills_dir / skill_name

        if skill_path.exists():
            return False, f"Skill '{skill_name}' already exists at {skill_path}"

        logger.debug("Copying from path=%s to %s", source_path, skill_path)
        try:
            shutil.copytree(source_path, skill_path, symlinks=False)
        except Exception as exc:
            logger.exception("Copy failed")
            return False, f"Failed to copy skill: {exc}"

        return True, f"Skill '{skill_name}' installed to {skill_path}"

    def uninstall(self, name: str) -> tuple[bool, str]:
        skill_path = self.skills_dir / name
        if not skill_path.exists():
            return False, f"Skill '{name}' not found"

        logger.info("Uninstalling skill=%s path=%s", name, skill_path)
        try:
            shutil.rmtree(skill_path)
            return True, f"Skill '{name}' uninstalled"
        except Exception as exc:
            logger.exception("Uninstall failed")
            return False, f"Failed to uninstall: {exc}"

    def list_installed(self) -> list[tuple[str, Path]]:
        skills = []
        if not self.skills_dir.exists():
            return skills
        for child in self.skills_dir.iterdir():
            if child.is_dir() and (child / "SKILL.md").exists():
                skills.append((child.name, child))
        return sorted(skills, key=lambda x: x[0])

    # ------------------------------------------------------------------
    # Config registration
    # ------------------------------------------------------------------

    def _register_in_config(self, skill_name: str) -> None:
        """Add a skills.<name> block to config.yaml if not already present.

        The block is a commented scaffold the user can fill in:

            docx:
              node_bin: ''     # override global node_bin for this skill
              python_bin: ''   # override global python_bin
              extra_path: []
              env: {}          # skill-specific env vars (e.g. API keys)
        """
        if not self._config_path:
            return
        config_file = Path(self._config_path).expanduser()
        if not config_file.exists():
            return

        try:
            import yaml
            content = config_file.read_text()
            data = yaml.safe_load(content) or {}

            skills_section = data.get("skills") or {}
            if skill_name in skills_section:
                logger.debug("Skill '%s' already in config.yaml skills section", skill_name)
                return

            # Add an empty override block for this skill
            skills_section[skill_name] = {
                "node_bin": "",
                "python_bin": "",
                "extra_path": [],
                "env": {},
            }
            data["skills"] = skills_section

            config_file.write_text(yaml.safe_dump(data, sort_keys=False))
            logger.info(
                "Added skills.%s config block to %s — fill in any overrides needed",
                skill_name,
                config_file,
            )
        except Exception as exc:
            logger.warning("Could not register skill '%s' in config: %s", skill_name, exc)


def _extract_skill_name(source: str) -> str:
    """Best-effort skill name from a source URL or path."""
    return Path(source.rstrip("/")).stem
