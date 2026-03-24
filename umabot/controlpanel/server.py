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

    # Google OAuth callback
    _add_oauth_routes(app)

    # Serve built frontend SPA (if it exists)
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app


def _add_oauth_routes(app: FastAPI) -> None:
    """Add Google OAuth2 callback endpoint."""
    from fastapi import Request
    from fastapi.responses import HTMLResponse

    @app.get("/oauth/google/callback", response_class=HTMLResponse, include_in_schema=False)
    async def google_oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        if error:
            return HTMLResponse(
                f"<h2>❌ Google authorisation failed</h2><p>{error}</p>",
                status_code=400,
            )
        if not code or not state:
            return HTMLResponse("<h2>❌ Missing code or state</h2>", status_code=400)

        config = request.app.state.config
        db = request.app.state.db

        google_cfg = getattr(config, "google", None)
        if not google_cfg:
            return HTMLResponse("<h2>❌ Google not configured</h2>", status_code=400)

        client_id = getattr(google_cfg, "client_id", "") or ""
        client_secret = getattr(google_cfg, "client_secret", "") or ""
        if not client_id or not client_secret:
            return HTMLResponse("<h2>❌ Google client credentials not set</h2>", status_code=400)

        control = config.control_panel
        redirect_uri = f"http://{control.web_host}:{control.web_port}/oauth/google/callback"

        try:
            from umabot.tools.google.auth import exchange_code
            exchange_code(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                code=code,
                state=state,
                db=db,
            )
        except Exception as exc:
            logger.error("Google OAuth exchange failed: %s", exc)
            return HTMLResponse(
                f"<h2>❌ Token exchange failed</h2><p>{exc}</p>",
                status_code=400,
            )

        return HTMLResponse(
            "<h2>✅ Google authorised successfully!</h2>"
            "<p>You can close this window and retry your request in the bot.</p>"
        )


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
