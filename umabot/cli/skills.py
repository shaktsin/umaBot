"""Skills management commands."""

from __future__ import annotations

import getpass
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from umabot.config import load_config
from umabot.config.loader import save_config as save_cfg

console = Console()


def handle_skills(args) -> None:
    """Handle skills subcommands."""
    if not args.skills_command or args.skills_command == "list":
        list_skills()
    elif args.skills_command == "install":
        install_skill(args.path, config_path=args.config)
    elif args.skills_command == "remove":
        remove_skill(args.name, config_path=args.config)
    elif args.skills_command == "uninstall":
        remove_skill(args.name, config_path=args.config)
    elif args.skills_command == "reinstall":
        reinstall_skill(args.name, config_path=args.config)
    elif args.skills_command == "lint":
        lint_skills()
    elif args.skills_command == "configure":
        configure_skill(args.name, config_path=args.config)
    else:
        console.print("[yellow]Unknown skills command[/yellow]")


def list_skills() -> None:
    """List installed skills."""
    # Import here to avoid circular dependencies
    from pathlib import Path
    from umabot.skills import SkillRegistry

    registry = SkillRegistry()
    registry.load_from_dirs([
        Path.cwd() / "skills",
        Path.home() / ".umabot" / "skills",
    ])

    skills = registry.list()
    if not skills:
        console.print("[yellow]No skills installed[/yellow]")
        return

    console.print("\n[bold]Installed Skills:[/bold]\n")
    for skill in skills:
        console.print(f"  • {skill.metadata.name} (v{skill.metadata.version})")
        if skill.metadata.description:
            console.print(f"    {skill.metadata.description}")
    console.print()


def install_skill(path: str, *, config_path: str | None) -> None:
    """Install a skill from path."""
    from umabot.skills.loader import load_skill_metadata
    from umabot.skills.registry import lint_skill_dir

    source = Path(path).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        console.print(f"[red]Skill directory not found: {source}[/red]")
        return

    errors = lint_skill_dir(source)
    if errors:
        console.print("[red]Skill validation failed:[/red]")
        for err in errors:
            console.print(f"  • {err}")
        return

    metadata = load_skill_metadata(source)
    if metadata is None:
        console.print("[red]Invalid SKILL.md metadata[/red]")
        return

    cfg, resolved_config_path = load_config(config_path=config_path)
    skill_cfg = _collect_install_config(metadata.install_config)

    install_root = Path.home() / ".umabot" / "skills"
    dest = install_root / metadata.name
    try:
        install_root.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest)
        _prepare_skill_runtime(dest)
        if not hasattr(cfg, "skill_configs") or not isinstance(cfg.skill_configs, dict):
            cfg.skill_configs = {}
        cfg.skill_configs[metadata.name] = skill_cfg
        save_cfg(cfg, resolved_config_path)
    except PermissionError:
        console.print(f"[red]Permission denied while writing to {install_root}[/red]")
        return
    except Exception as exc:
        console.print(f"[red]Skill installation failed: {exc}[/red]")
        return
    console.print(f"[green]Installed skill '{metadata.name}' at {dest}[/green]")
    console.print(f"[green]Saved skill config in {resolved_config_path}[/green]")


def remove_skill(name: str, *, config_path: str | None = None) -> None:
    """Remove an installed skill."""
    target = (Path.home() / ".umabot" / "skills" / name).resolve()
    root = (Path.home() / ".umabot" / "skills").resolve()
    if root not in target.parents:
        console.print("[red]Refusing to remove path outside skills directory[/red]")
        return
    if not target.exists():
        console.print(f"[yellow]Skill not installed: {name}[/yellow]")
        return
    try:
        shutil.rmtree(target)
    except PermissionError:
        console.print(f"[red]Permission denied while removing {target}[/red]")
        return
    console.print(f"[green]Removed skill '{name}'[/green]")
    cfg, resolved_config_path = load_config(config_path=config_path)
    if isinstance(getattr(cfg, "skill_configs", None), dict) and name in cfg.skill_configs:
        cfg.skill_configs.pop(name, None)
        save_cfg(cfg, resolved_config_path)
        console.print(f"[green]Removed stored config for skill '{name}'[/green]")


def configure_skill(name: str, *, config_path: str | None) -> None:
    from umabot.skills.loader import load_skill_metadata

    cfg, resolved_config_path = load_config(config_path=config_path)
    skill_path = Path.home() / ".umabot" / "skills" / name
    metadata = load_skill_metadata(skill_path)
    if not metadata:
        console.print(f"[red]Skill not installed or invalid: {name}[/red]")
        return
    skill_cfg = _collect_install_config(metadata.install_config, previous=cfg.skill_configs.get(name, {}))
    if not hasattr(cfg, "skill_configs") or not isinstance(cfg.skill_configs, dict):
        cfg.skill_configs = {}
    cfg.skill_configs[name] = skill_cfg
    save_cfg(cfg, resolved_config_path)
    console.print(f"[green]Updated config for skill '{name}' in {resolved_config_path}[/green]")


def reinstall_skill(name: str, *, config_path: str | None) -> None:
    from umabot.skills.loader import load_skill_metadata
    from umabot.skills.registry import lint_skill_dir

    cfg, _ = load_config(config_path=config_path)
    skill_path = Path.home() / ".umabot" / "skills" / name
    if not skill_path.exists() or not skill_path.is_dir():
        console.print(f"[red]Skill not installed: {name}[/red]")
        return
    errors = lint_skill_dir(skill_path)
    if errors:
        console.print("[red]Skill validation failed:[/red]")
        for err in errors:
            console.print(f"  • {err}")
        return
    metadata = load_skill_metadata(skill_path)
    if not metadata:
        console.print(f"[red]Invalid SKILL.md for installed skill: {name}[/red]")
        return
    if not isinstance(getattr(cfg, "skill_configs", None), dict):
        cfg.skill_configs = {}
    if name not in cfg.skill_configs:
        cfg.skill_configs[name] = {"args": {}, "env": {}}
    try:
        venv_dir = skill_path / ".venv"
        if venv_dir.exists():
            shutil.rmtree(venv_dir)
        _prepare_skill_runtime(skill_path)
    except PermissionError:
        console.print(f"[red]Permission denied while rebuilding runtime for {name}[/red]")
        return
    except Exception as exc:
        console.print(f"[red]Failed to reinstall skill runtime: {exc}[/red]")
        return
    console.print(f"[green]Reinstalled skill runtime for '{name}'[/green]")


def lint_skills() -> None:
    """Lint all installed skills."""
    from umabot.skills.registry import lint_skill_dir

    roots = [Path.cwd() / "skills", Path.home() / ".umabot" / "skills"]
    checked = 0
    failures = 0

    for root in roots:
        if not root.exists():
            continue
        for child in root.iterdir():
            if not child.is_dir():
                continue
            checked += 1
            errors = lint_skill_dir(child)
            if errors:
                failures += 1
                console.print(f"[red]{child}[/red]")
                for err in errors:
                    console.print(f"  • {err}")
            else:
                console.print(f"[green]{child}[/green]")

    if checked == 0:
        console.print("[yellow]No skills found to lint[/yellow]")
        return
    if failures:
        console.print(f"[yellow]Lint complete: {checked} checked, {failures} failed[/yellow]")
    else:
        console.print(f"[green]Lint complete: {checked} checked, 0 failed[/green]")


def _collect_install_config(spec: dict, previous: dict | None = None) -> dict:
    previous = previous or {}
    args_prev = previous.get("args", {}) if isinstance(previous, dict) else {}
    env_prev = previous.get("env", {}) if isinstance(previous, dict) else {}
    args_spec = spec.get("args", {}) if isinstance(spec, dict) else {}
    env_spec = spec.get("env", {}) if isinstance(spec, dict) else {}
    args_values = _prompt_config_section(args_spec, args_prev, secret=False)
    env_values = _prompt_config_section(env_spec, env_prev, secret=True)
    return {"args": args_values, "env": env_values}


def _prompt_config_section(spec: dict, prev_values: dict, *, secret: bool) -> dict:
    values: dict = {}
    for key, raw_def in spec.items():
        item_def = raw_def if isinstance(raw_def, dict) else {"default": raw_def}
        required = bool(item_def.get("required", False))
        default = item_def.get("default", "")
        value_type = str(item_def.get("type", "string"))
        is_secret = bool(item_def.get("secret", False)) or secret
        prompt = f"{key}"
        current = prev_values.get(key, default)
        rendered_default = "***" if is_secret and current else str(current) if current is not None else ""
        while True:
            if is_secret:
                entered = getpass.getpass(f"{prompt} [{rendered_default}]: ")
            else:
                entered = input(f"{prompt} [{rendered_default}]: ")
            value = entered if entered != "" else current
            if required and (value is None or str(value).strip() == ""):
                console.print(f"[yellow]{key} is required[/yellow]")
                continue
            if value_type == "int" and str(value).strip() != "":
                try:
                    value = int(value)
                except ValueError:
                    console.print(f"[yellow]{key} must be int[/yellow]")
                    continue
            values[str(key)] = value
            break
    return values


def _prepare_skill_runtime(skill_dir: Path) -> None:
    venv_dir = skill_dir / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    python_bin = venv_dir / "Scripts" / "python.exe" if sys.platform == "win32" else venv_dir / "bin" / "python"
    subprocess.run([str(python_bin), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    req_file = skill_dir / "requirements.txt"
    if req_file.exists():
        subprocess.run([str(python_bin), "-m", "pip", "install", "-r", str(req_file)], check=True)
