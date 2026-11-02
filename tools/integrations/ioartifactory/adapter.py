"""ioArtifactory adapter (artifact and package storage).

TODO before enabling:
- Obtain the ioArtifactory base URL, repository names, auth scheme and API docs.
- Separate read (resolve/download metadata) from write (publish) permissions.
- Publishing artifacts requires human approval and an immutable, signed artifact
  policy; never allow the model to overwrite release repositories.
"""
from __future__ import annotations

from typing import Any, Dict

from tools.base import ToolContext, ToolResult
from tools.integrations.base import not_configured

NAME = "ioArtifactory"
REQUIRED_ENV = ["IOARTIFACTORY_BASE_URL", "IOARTIFACTORY_TOKEN"]
DOCS = __doc__ or ""


async def _tool(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    payload = not_configured(NAME, REQUIRED_ENV, DOCS)
    return ToolResult("not_configured", payload, payload["message"])


async def list_artifacts(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _tool(ctx, args)


async def get_metadata(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _tool(ctx, args)


async def publish_artifact(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _tool(ctx, args)


def register(registry) -> None:
    from tools.base import ToolSpec

    registry.register(ToolSpec("ioartifactory_list_artifacts", "List ioArtifactory artifacts (adapter not configured).", {"type": "object", "properties": {"repository": {"type": "string"}, "path": {"type": "string"}}}, list_artifacts, phases=["integrations"]))
    registry.register(ToolSpec("ioartifactory_get_metadata", "Read ioArtifactory artifact metadata (adapter not configured).", {"type": "object", "properties": {"repository": {"type": "string"}, "path": {"type": "string"}}, "required": ["repository", "path"]}, get_metadata, phases=["integrations"]))
    registry.register(ToolSpec("ioartifactory_publish_artifact", "Publish an artifact to ioArtifactory (adapter not configured; requires approval).", {"type": "object", "properties": {"repository": {"type": "string"}, "path": {"type": "string"}, "source": {"type": "string"}}, "required": ["repository", "path", "source"]}, publish_artifact, phases=["integrations"], destructive=True))
