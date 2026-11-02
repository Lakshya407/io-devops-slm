"""Capability and integration status endpoints."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request

from api.deps import require_api_key

router = APIRouter()

INTEGRATIONS = {
    "ioProjects": ["IOPROJECTS_BASE_URL", "IOPROJECTS_TOKEN"],
    "ioGitLab": ["IOGITLAB_BASE_URL", "IOGITLAB_TOKEN"],
    "ioAgrco": ["IOAGRCO_BASE_URL", "IOAGRCO_TOKEN"],
    "ioArtifactory": ["IOARTIFACTORY_BASE_URL", "IOARTIFACTORY_TOKEN"],
    "ioDB": ["IODB_BASE_URL", "IODB_TOKEN"],
}


@router.get("/v1/capabilities")
async def capabilities(request: Request, _auth: None = Depends(require_api_key)):
    settings = request.app.state.settings
    registry = request.app.state.registry
    integrations = {
        name: {
            "configured": all(bool(os.environ.get(key)) for key in keys),
            "required_env": keys,
        }
        for name, keys in INTEGRATIONS.items()
    }
    return {
        "model": settings.agent_public_model_id,
        "upstream_model": settings.agent_model,
        "model_is_local": settings.model_is_local,
        "tools": registry.names(),
        "phases": {
            phase: sorted(registry.allowed_actions(phase))
            for phase in ("inspect", "edit", "test", "verify", "devops", "integrations")
        },
        "integrations": integrations,
        "milestone": 1,
        "docker_build_enabled": settings.enable_docker_build,
        "kubernetes_enabled": settings.enable_kubernetes,
    }


@router.get("/v1/tools")
async def tools(request: Request, _auth: None = Depends(require_api_key)):
    registry = request.app.state.registry
    return {"tools": [spec.schema() for spec in registry._tools.values()]}  # noqa: SLF001
