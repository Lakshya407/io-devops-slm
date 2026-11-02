"""OpenAI-compatible model listing."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.deps import require_api_key
from api.schemas.openai import ModelCard, ModelList

router = APIRouter()


@router.get("/v1/models", response_model=ModelList)
async def list_models(request: Request, _auth: None = Depends(require_api_key)):
    settings = request.app.state.settings
    return ModelList(data=[ModelCard(id=settings.agent_public_model_id, owned_by="infoorigin")])


@router.get("/v1/models/{model_id}", response_model=ModelCard)
async def get_model(model_id: str, request: Request, _auth: None = Depends(require_api_key)):
    settings = request.app.state.settings
    if model_id != settings.agent_public_model_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="model not found")
    return ModelCard(id=settings.agent_public_model_id, owned_by="infoorigin")
