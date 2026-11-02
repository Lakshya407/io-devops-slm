"""Health and readiness endpoints."""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from persistence import db

router = APIRouter()
STARTED = time.time()


@router.get("/")
async def root(request: Request):
    settings = request.app.state.settings
    return {
        "service": "io-coding-agent",
        "model": settings.agent_public_model_id,
        "upstream_model": settings.agent_model,
        "api": "/v1",
        "docs": "/docs",
        "health": "/health",
    }


@router.get("/health")
async def health(request: Request):
    settings = request.app.state.settings
    return {
        "status": "ok",
        "service": "io-coding-agent",
        "model": settings.agent_public_model_id,
        "upstream_model": settings.agent_model,
        "uptime_seconds": int(time.time() - STARTED),
    }


@router.get("/ready")
async def ready(request: Request):
    checks = {}
    checks["database"] = await asyncio.to_thread(db.check_connection)
    try:
        checks["sandbox"] = await request.app.state.sandbox.health()
    except Exception:  # noqa: BLE001
        checks["sandbox"] = False
    try:
        checks["litellm"] = (await request.app.state.llm.health()).get("ok", False)
    except Exception:  # noqa: BLE001
        checks["litellm"] = False
    try:
        workspace_ok = request.app.state.workspace.root.is_dir()
    except Exception:  # noqa: BLE001
        workspace_ok = False
    checks["workspaces"] = workspace_ok
    ok = all(checks.values())
    return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)
