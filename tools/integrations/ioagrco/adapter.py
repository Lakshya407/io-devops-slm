"""ioAgrco adapter (CI/CD pipelines).

TODO before enabling:
- Obtain the ioAgrco API base URL, auth scheme and pipeline/job resource paths.
- Define read-only pipeline inspection first; triggering a pipeline must require
  human approval and a scoped service account.
- Never expose a global CI token to the model or to a workspace.
"""
from __future__ import annotations

from typing import Any, Dict

from tools.base import ToolContext, ToolResult
from tools.integrations.base import not_configured

NAME = "ioAgrco"
REQUIRED_ENV = ["IOAGRCO_BASE_URL", "IOAGRCO_TOKEN"]
DOCS = __doc__ or ""


async def _tool(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    payload = not_configured(NAME, REQUIRED_ENV, DOCS)
    return ToolResult("not_configured", payload, payload["message"])


async def list_pipelines(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _tool(ctx, args)


async def get_pipeline(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _tool(ctx, args)


async def trigger_pipeline(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _tool(ctx, args)


def register(registry) -> None:
    from tools.base import ToolSpec

    registry.register(ToolSpec("ioagrco_list_pipelines", "List ioAgrco pipelines (adapter not configured).", {"type": "object", "properties": {"project_id": {"type": "string"}}}, list_pipelines, phases=["integrations"]))
    registry.register(ToolSpec("ioagrco_get_pipeline", "Read an ioAgrco pipeline and its jobs (adapter not configured).", {"type": "object", "properties": {"pipeline_id": {"type": "string"}}, "required": ["pipeline_id"]}, get_pipeline, phases=["integrations"]))
    registry.register(ToolSpec("ioagrco_trigger_pipeline", "Trigger an ioAgrco pipeline (adapter not configured; requires approval).", {"type": "object", "properties": {"pipeline_id": {"type": "string"}, "variables": {"type": "object"}}, "required": ["pipeline_id"]}, trigger_pipeline, phases=["integrations"], destructive=True))
