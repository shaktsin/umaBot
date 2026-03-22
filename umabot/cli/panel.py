"""CLI handler for the 'umabot panel' command."""

from __future__ import annotations

from typing import Optional


def run_panel(
    config_path: Optional[str] = None,
    host: str = "127.0.0.1",
    port: int = 8080,
    no_open: bool = False,
    log_level: Optional[str] = None,
) -> None:
    """Start the local web control panel."""
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        from rich.console import Console
        Console().print(
            "[red]fastapi and uvicorn are required for the web panel.[/red]\n"
            "Install with: [bold]pip install 'umabot[panel]'[/bold]"
        )
        return

    from umabot.controlpanel.server import run_panel as _run

    _run(
        config_path=config_path,
        host=host,
        port=port,
        open_browser=not no_open,
        log_level=log_level,
    )
