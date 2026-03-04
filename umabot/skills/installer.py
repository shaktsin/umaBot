"""Skill installation and management."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger("umabot.skills.installer")


class SkillInstaller:
    """Manages skill installation from various sources."""

    def __init__(self, skills_dir: Path):
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def install(self, source: str, name: Optional[str] = None) -> tuple[bool, str]:
        """
        Install skill from source.

        Args:
            source: PyPI package name, GitHub URL, or local path
            name: Optional skill name override

        Returns:
            (success, message)
        """
        logger.info("Installing skill from source=%s name=%s", source, name)

        # Determine source type
        if source.startswith(("http://", "https://", "git@")):
            return self._install_from_git(source, name)
        elif "/" in source or source.startswith("."):
            # Local path
            return self._install_from_path(Path(source), name)
        else:
            # Assume PyPI package
            return self._install_from_pypi(source, name)

    def _install_from_pypi(self, package: str, name: Optional[str] = None) -> tuple[bool, str]:
        """Install skill from PyPI package."""
        skill_name = name or package.replace("umabot-skill-", "").replace("umabot_skill_", "")
        skill_path = self.skills_dir / skill_name

        if skill_path.exists():
            return False, f"Skill '{skill_name}' already exists at {skill_path}"

        logger.debug("Installing from PyPI package=%s to path=%s", package, skill_path)

        # Create temporary directory to download package
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / skill_name

            try:
                # Download package using pip
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "download",
                        "--no-deps",
                        "--dest",
                        tmpdir,
                        package,
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                logger.debug("PyPI download output: %s", result.stdout)

                # Find downloaded file
                downloaded = list(Path(tmpdir).glob("*.whl")) or list(Path(tmpdir).glob("*.tar.gz"))
                if not downloaded:
                    return False, f"No package files found for '{package}'"

                # Extract to temp location
                pkg_file = downloaded[0]
                if pkg_file.suffix == ".whl":
                    # Unzip wheel
                    import zipfile

                    with zipfile.ZipFile(pkg_file) as zf:
                        zf.extractall(tmp_path)
                else:
                    # Extract tarball
                    import tarfile

                    with tarfile.open(pkg_file) as tf:
                        tf.extractall(tmp_path)

                # Find SKILL.md in extracted contents
                skill_md_candidates = list(tmp_path.rglob("SKILL.md"))
                if not skill_md_candidates:
                    return False, f"No SKILL.md found in package '{package}'"

                # Use the directory containing SKILL.md as the skill root
                skill_root = skill_md_candidates[0].parent

                # Move to skills directory
                shutil.move(str(skill_root), str(skill_path))

            except subprocess.CalledProcessError as exc:
                logger.error("PyPI install failed: %s", exc.stderr)
                return False, f"Failed to download package: {exc.stderr}"
            except Exception as exc:
                logger.exception("PyPI install error")
                return False, f"Installation error: {exc}"

        # Setup virtualenv and install dependencies
        setup_success, setup_msg = self._setup_skill_env(skill_path)
        if not setup_success:
            shutil.rmtree(skill_path, ignore_errors=True)
            return False, setup_msg

        return True, f"Skill '{skill_name}' installed to {skill_path}"

    def _install_from_git(self, url: str, name: Optional[str] = None) -> tuple[bool, str]:
        """Install skill from Git repository."""
        # Extract repo name from URL
        parsed = urlparse(url)
        repo_name = Path(parsed.path).stem
        skill_name = name or repo_name.replace("umabot-skill-", "").replace("umabot_skill_", "")
        skill_path = self.skills_dir / skill_name

        if skill_path.exists():
            return False, f"Skill '{skill_name}' already exists at {skill_path}"

        logger.debug("Cloning from git url=%s to path=%s", url, skill_path)

        try:
            result = subprocess.run(
                ["git", "clone", url, str(skill_path)],
                capture_output=True,
                text=True,
                check=True,
            )
            logger.debug("Git clone output: %s", result.stdout)
        except subprocess.CalledProcessError as exc:
            logger.error("Git clone failed: %s", exc.stderr)
            return False, f"Git clone failed: {exc.stderr}"
        except FileNotFoundError:
            return False, "Git not found. Please install git."

        # Verify SKILL.md exists
        if not (skill_path / "SKILL.md").exists():
            shutil.rmtree(skill_path, ignore_errors=True)
            return False, f"No SKILL.md found in repository"

        # Setup virtualenv and install dependencies
        setup_success, setup_msg = self._setup_skill_env(skill_path)
        if not setup_success:
            shutil.rmtree(skill_path, ignore_errors=True)
            return False, setup_msg

        return True, f"Skill '{skill_name}' installed to {skill_path}"

    def _install_from_path(self, path: Path, name: Optional[str] = None) -> tuple[bool, str]:
        """Install skill from local path."""
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

        # Setup virtualenv and install dependencies
        setup_success, setup_msg = self._setup_skill_env(skill_path)
        if not setup_success:
            shutil.rmtree(skill_path, ignore_errors=True)
            return False, setup_msg

        return True, f"Skill '{skill_name}' installed to {skill_path}"

    def _setup_skill_env(self, skill_path: Path) -> tuple[bool, str]:
        """Create virtualenv and install dependencies for skill."""
        logger.debug("Setting up environment for skill=%s", skill_path)

        venv_path = skill_path / ".venv"

        # Create virtualenv
        try:
            subprocess.run(
                [sys.executable, "-m", "venv", str(venv_path)],
                capture_output=True,
                text=True,
                check=True,
            )
            logger.debug("Created virtualenv at %s", venv_path)
        except subprocess.CalledProcessError as exc:
            logger.error("Virtualenv creation failed: %s", exc.stderr)
            return False, f"Failed to create virtualenv: {exc.stderr}"

        # Determine pip path
        if sys.platform == "win32":
            pip_exe = venv_path / "Scripts" / "pip.exe"
        else:
            pip_exe = venv_path / "bin" / "pip"

        # Install dependencies if requirements.txt exists
        requirements_file = skill_path / "requirements.txt"
        if requirements_file.exists():
            logger.debug("Installing dependencies from %s", requirements_file)
            try:
                subprocess.run(
                    [str(pip_exe), "install", "-r", str(requirements_file)],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                logger.debug("Dependencies installed successfully")
            except subprocess.CalledProcessError as exc:
                logger.error("Dependency installation failed: %s", exc.stderr)
                return False, f"Failed to install dependencies: {exc.stderr}"

        return True, "Environment setup complete"

    def uninstall(self, name: str) -> tuple[bool, str]:
        """Uninstall a skill by name."""
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
        """List all installed skills."""
        skills = []
        if not self.skills_dir.exists():
            return skills

        for child in self.skills_dir.iterdir():
            if child.is_dir() and (child / "SKILL.md").exists():
                skills.append((child.name, child))

        return sorted(skills, key=lambda x: x[0])
