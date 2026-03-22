"""FastAPI dependency helpers for the control panel."""

from __future__ import annotations

from starlette.requests import HTTPConnection

from umabot.controlpanel.connector import GatewayConnector
from umabot.controlpanel.events import EventBroadcaster
from umabot.controlpanel.store import PanelStore
from umabot.storage.db import Database


def get_db(request: HTTPConnection) -> Database:
    return request.app.state.db


def get_config(request: HTTPConnection):
    return request.app.state.config


def get_config_path(request: HTTPConnection) -> str:
    return request.app.state.config_path


def get_store(request: HTTPConnection) -> PanelStore:
    return request.app.state.store


def get_broadcaster(request: HTTPConnection) -> EventBroadcaster:
    return request.app.state.broadcaster


def get_connector(request: HTTPConnection) -> GatewayConnector:
    return request.app.state.connector


def get_skill_registry(request: HTTPConnection):
    return request.app.state.skill_registry
