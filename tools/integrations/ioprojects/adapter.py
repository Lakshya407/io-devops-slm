"""ioProjects adapter (daily updates, project information, task tracking).

TODO before enabling:
- Obtain the published ioProjects API base URL, authentication scheme and
  OpenAPI/Swagger document.
- Define the exact task, project and daily-update resource paths.
- Create a least-privilege service account and document per-user authorization
  (the agent must act on behalf of the requesting user, not with a global admin
  token).
- Add the base URL and token to the environment; never commit them.
"""
from __future__ import annotations

from typing import Any, Dict

from tools.base import ToolContext, ToolResult
from tools.integrations.base import not_configured

NAME = "ioProjects"
REQUIRED_ENV = ["IOPROJECTS_BASE_URL", "IOPROJECTS_TOKEN"]
DOCS = __doc__ or ""


async def _tool(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    payload = not_configured(NAME, REQUIRED_ENV, DOCS)
    return ToolResult("not_configured", payload, payload["message"])


async def get_task(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _tool(ctx, args)


async def update_task(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _tool(ctx, args)


async def add_daily_update(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _tool(ctx, args)


def register(registry) -> None:
    from tools.base import ToolSpec

    registry.register(ToolSpec("ioprojects_get_task", "Read a task from ioProjects (adapter not configured).", {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}, get_task, phases=["integrations"]))
    registry.register(ToolSpec("ioprojects_update_task", "Update task status/comment in ioProjects (adapter not configured; requires approval).", {"type": "object", "properties": {"task_id": {"type": "string"}, "status": {"type": "string"}, "comment": {"type": "string"}}, "required": ["task_id"]}, update_task, phases=["integrations"], destructive=True))
    registry.register(ToolSpec("ioprojects_add_daily_update", "Post a daily update to ioProjects (adapter not configured).", {"type": "object", "properties": {"project_id": {"type": "string"}, "text": {"type": "string"}}, "required": ["project_id", "text"]}, add_daily_update, phases=["integrations"], destructive=True))
