"""FastAPI application factory for the umaBot control panel."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from umabot.controlpanel.connector import GatewayConnector
from umabot.controlpanel.events import EventBroadcaster
from umabot.controlpanel.routers import chat, config_router, connectors, dashboard, logs, policy, skills, tasks
from umabot.controlpanel.store import PanelStore

logger = logging.getLogger("umabot.controlpanel")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    connector: GatewayConnector = app.state.connector
    broadcaster: EventBroadcaster = app.state.broadcaster
    await connector.start(broadcaster)
    logger.info("Control panel started")
    yield
    await connector.stop()
    logger.info("Control panel stopped")


def create_app(
    *,
    config,
    config_path: str,
    db,
    skill_registry,
) -> FastAPI:
    """Create and configure the FastAPI control panel application."""
    store = PanelStore()
    ws_url = f"ws://{config.runtime.ws_host}:{config.runtime.ws_port}/ws"
    ws_token = config.runtime.ws_token or ""

    connector = GatewayConnector(ws_url=ws_url, ws_token=ws_token, store=store)
    broadcaster = EventBroadcaster()

    app = FastAPI(
        title="umaBot Control Panel",
        version="0.1.0",
        lifespan=_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Inject shared state
    app.state.config = config
    app.state.config_path = config_path
    app.state.db = db
    app.state.store = store
    app.state.connector = connector
    app.state.broadcaster = broadcaster
    app.state.skill_registry = skill_registry

    # Mount routers
    app.include_router(dashboard.router, prefix="/api")
    app.include_router(connectors.router, prefix="/api")
    app.include_router(skills.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    app.include_router(policy.router, prefix="/api")
    app.include_router(config_router.router, prefix="/api")
    app.include_router(logs.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")

    # Serve built frontend SPA (if it exists)
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app


def run_panel(
    config_path: Optional[str] = None,
    host: str = "127.0.0.1",
    port: int = 8080,
    open_browser: bool = True,
    log_level: Optional[str] = None,
) -> None:
    """CLI entry point: build runtime and start uvicorn."""
    import logging

    import uvicorn

    from umabot.gateway import build_runtime

    logging.basicConfig(
        level=getattr(logging, (log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config, resolved_path, db, queue, tool_registry, policy_engine, skill_registry, unified_registry = (
        build_runtime(config_path=config_path)
    )

    app = create_app(
        config=config,
        config_path=resolved_path,
        db=db,
        skill_registry=skill_registry,
    )

    if open_browser:
        import threading
        import webbrowser

        def _open():
            import time
            time.sleep(1.2)
            webbrowser.open(f"http://{host}:{port}")

        threading.Thread(target=_open, daemon=True).start()

    logger.info("Starting umaBot control panel on http://%s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level=(log_level or "info").lower())
