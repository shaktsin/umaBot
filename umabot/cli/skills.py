"""Skills management commands."""

from __future__ import annotations

import shutil
from pathlib import Path

from rich.console import Console

from umabot.config import load_config

console = Console()


def handle_skills(args) -> None:
    """Handle skills subcommands."""
    if not args.skills_command or args.skills_command == "list":
        list_skills(config_path=getattr(args, "config", None))
    elif args.skills_command == "install":
        install_skill(args.path, config_path=args.config)
    elif args.skills_command == "remove":
        remove_skill(args.name, config_path=args.config)
    elif args.skills_command == "uninstall":
        remove_skill(args.name, config_path=args.config)
    elif args.skills_command == "lint":
        lint_skills(config_path=getattr(args, "config", None))
    else:
        console.print("[yellow]Unknown skills command[/yellow]")


def list_skills(*, config_path: str | None = None) -> None:
    """List installed skills."""
    from umabot.skills import SkillRegistry

    registry = SkillRegistry()
    registry.load_from_dirs(_skill_dirs(config_path))

    skills = registry.list()
    if not skills:
        console.print("[yellow]No skills installed[/yellow]")
        return

    console.print("\n[bold]Installed Skills:[/bold]\n")
    for skill in skills:
        console.print(f"  • {skill.metadata.name}")
        if skill.metadata.description:
            desc = skill.metadata.description
            if len(desc) > 100:
                desc = desc[:100] + "..."
            console.print(f"    {desc}")
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

    install_root = Path.home() / ".umabot" / "skills"
    dest = install_root / metadata.name
    try:
        install_root.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest)
    except PermissionError:
        console.print(f"[red]Permission denied while writing to {install_root}[/red]")
        return
    except Exception as exc:
        console.print(f"[red]Skill installation failed: {exc}[/red]")
        return
    console.print(f"[green]Installed skill '{metadata.name}' at {dest}[/green]")


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


def lint_skills(*, config_path: str | None = None) -> None:
    """Lint all installed skills."""
    from umabot.skills.registry import lint_skill_dir

    roots = _skill_dirs(config_path)
    checked = 0
    failures = 0

    for root in roots:
        if not root.exists():
            continue
        for child in root.iterdir():
            if not child.is_dir():
                continue
            if not (child / "SKILL.md").exists():
                continue
            checked += 1
            errors = lint_skill_dir(child)
            if errors:
                failures += 1
                console.print(f"[red]{child}[/red]")
                for err in errors:
                    console.print(f"  • {err}")
            else:
                console.print(f"[green]{child.name}[/green]")

    if checked == 0:
        console.print("[yellow]No skills found to lint[/yellow]")
        return
    if failures:
        console.print(f"[yellow]Lint complete: {checked} checked, {failures} failed[/yellow]")
    else:
        console.print(f"[green]Lint complete: {checked} checked, 0 failed[/green]")


def _skill_dirs(config_path: str | None = None) -> list[Path]:
    """Get all skill directories from defaults + config."""
    dirs = [
        Path.cwd() / "skills",
        Path.home() / ".umabot" / "skills",
    ]
    try:
        cfg, _ = load_config(config_path=config_path)
        for extra in getattr(cfg, "skill_dirs", []) or []:
            dirs.append(Path(extra).expanduser())
    except Exception:
        pass
    return dirs
