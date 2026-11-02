"""FastAPI dependencies: authentication and service lookup."""
from __future__ import annotations

import hmac
import logging
from typing import Any

from fastapi import HTTPException, Request, status

log = logging.getLogger(__name__)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def require_api_key(request: Request) -> None:
    settings = request.app.state.settings
    if not settings.agent_api_key:
        return
    supplied = _bearer(request)
    if not supplied or not hmac.compare_digest(supplied, settings.agent_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")


def resolve_user(request: Request, body_user: Any = None) -> str | None:
    settings = request.app.state.settings
    if settings.trust_forwarded_user:
        for header in ("x-openwebui-user-email", "x-openwebui-user-id", "x-user-email", "x-user-id"):
            value = request.headers.get(header)
            if value:
                return value[:256]
    if body_user is None:
        return None
    if isinstance(body_user, dict):
        for key in ("id", "email", "name"):
            value = body_user.get(key)
            if value:
                return str(value)[:256]
        return None
    return str(body_user)[:256] if body_user else None


def orchestrator(request: Request):
    return request.app.state.orchestrator


def repository(request: Request):
    return request.app.state.repo


def event_bus(request: Request):
    return request.app.state.bus


def app_settings(request: Request):
    return request.app.state.settings


def workspace_manager(request: Request):
    return request.app.state.workspace
