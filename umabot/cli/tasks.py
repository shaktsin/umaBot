"""Task management commands."""

from __future__ import annotations

from typing import Optional

from rich.console import Console

from umabot.config import load_config
from umabot.storage import Database
from umabot.tasks.schedule import compute_initial_next_run_at

console = Console()


def handle_tasks(args) -> None:
    if args.tasks_command == "create":
        create_task(
            config_path=args.config,
            name=args.name,
            prompt=args.prompt,
            task_type=args.type,
            run_at=args.run_at,
            frequency=args.frequency,
            time_of_day=args.time,
            day_of_week=args.day_of_week,
            timezone=args.timezone,
        )
        return
    if args.tasks_command == "list":
        list_tasks(config_path=args.config, status=args.status)
        return
    if args.tasks_command == "cancel":
        cancel_task(config_path=args.config, task_id=args.id)
        return
    console.print("[yellow]Unknown tasks command[/yellow]")


def create_task(
    *,
    config_path: Optional[str],
    name: str,
    prompt: str,
    task_type: str,
    run_at: Optional[str],
    frequency: Optional[str],
    time_of_day: Optional[str],
    day_of_week: Optional[str],
    timezone: str,
) -> None:
    cfg, _ = load_config(config_path=config_path)
    db = Database(cfg.storage.db_path)
    try:
        if task_type == "one_time":
            if not run_at:
                console.print("[red]--run-at is required for one_time tasks[/red]")
                return
            schedule = {"run_at": run_at}
        else:
            if not frequency:
                console.print("[red]--frequency is required for periodic tasks[/red]")
                return
            schedule = {"frequency": frequency}
            if time_of_day:
                schedule["time"] = time_of_day
            if day_of_week:
                schedule["day_of_week"] = day_of_week
        next_run_at = compute_initial_next_run_at(task_type, schedule, timezone)
        if not next_run_at:
            console.print("[red]Could not compute next run from provided schedule[/red]")
            return
        task_id = db.create_task(
            name=name,
            prompt=prompt,
            task_type=task_type,
            schedule=schedule,
            timezone=timezone,
            next_run_at=next_run_at,
            created_by="cli",
        )
        console.print(f"[green]Task created: #{task_id} next_run={next_run_at}[/green]")
    finally:
        db.close()


def list_tasks(*, config_path: Optional[str], status: Optional[str]) -> None:
    cfg, _ = load_config(config_path=config_path)
    db = Database(cfg.storage.db_path)
    try:
        rows = db.list_tasks(status=status)
        if not rows:
            console.print("[yellow]No tasks found[/yellow]")
            return
        for task in rows:
            console.print(
                f"#{task['id']} ({task['status']}) {task['name']} "
                f"type={task['task_type']} next={task.get('next_run_at') or '-'}"
            )
    finally:
        db.close()


def cancel_task(*, config_path: Optional[str], task_id: int) -> None:
    cfg, _ = load_config(config_path=config_path)
    db = Database(cfg.storage.db_path)
    try:
        if db.cancel_task(task_id):
            console.print(f"[green]Cancelled task #{task_id}[/green]")
        else:
            console.print(f"[yellow]Task #{task_id} not found[/yellow]")
    finally:
        db.close()
