"""Run the control panel as a module: python -m umabot.controlpanel"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(prog="umabot.controlpanel")
    parser.add_argument("--config", default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-open", dest="no_open", action="store_true")
    parser.add_argument("--log-level", dest="log_level", default=None)
    args = parser.parse_args()

    from umabot.controlpanel.server import run_panel

    run_panel(
        config_path=args.config,
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
