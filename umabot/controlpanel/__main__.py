"""Run the control panel as a module: python -m umabot.controlpanel"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(prog="umabot.controlpanel")
    parser.add_argument("--config", default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-open", dest="no_open", action="store_true")
    parser.add_argument("--log-level", dest="log_level", default=None)
    args = parser.parse_args()

    # Resolve host/port from config so web_port in config.yaml is honoured.
    host = args.host
    port = args.port
    if host is None or port is None:
        try:
            from umabot.config.loader import load_config
            cfg, _ = load_config(args.config)
            cp = cfg.control_panel
            if host is None:
                host = getattr(cp, "web_host", None) or "127.0.0.1"
            if port is None:
                port = int(getattr(cp, "web_port", None) or 8080)
        except Exception:
            host = host or "127.0.0.1"
            port = port or 8080

    from umabot.controlpanel.server import run_panel

    run_panel(
        config_path=args.config,
        host=host,
        port=port,
        open_browser=not args.no_open,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
