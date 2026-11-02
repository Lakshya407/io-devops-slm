"""ioGitLab adapter (source repositories and Git operations).

TODO before enabling:
- Confirm the ioGitLab base URL, API version, authentication (PAT/OAuth) and
  whether the GitLab API is self-hosted and version-compatible.
- Define project, repository-file, branch and merge-request resource paths from
  the published API documentation. Do not guess paths.
- Create a least-privilege service account; per-user authorization must be
  enforced by the adapter before any write.
- Remote pushes remain disabled until an explicit approval workflow is added.
"""
from __future__ import annotations

from typing import Any, Dict

from tools.base import ToolContext, ToolResult
from tools.integrations.base import not_configured

NAME = "ioGitLab"
REQUIRED_ENV = ["IOGITLAB_BASE_URL", "IOGITLAB_TOKEN"]
DOCS = __doc__ or ""


async def _tool(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    payload = not_configured(NAME, REQUIRED_ENV, DOCS)
    return ToolResult("not_configured", payload, payload["message"])


async def list_projects(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _tool(ctx, args)


async def get_file(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _tool(ctx, args)


async def create_branch(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _tool(ctx, args)


async def open_merge_request(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _tool(ctx, args)


def register(registry) -> None:
    from tools.base import ToolSpec

    registry.register(ToolSpec("iogitlab_list_projects", "List ioGitLab projects (adapter not configured).", {"type": "object", "properties": {"search": {"type": "string"}}}, list_projects, phases=["integrations"]))
    registry.register(ToolSpec("iogitlab_get_file", "Read a file from an ioGitLab repository (adapter not configured).", {"type": "object", "properties": {"project_id": {"type": "string"}, "path": {"type": "string"}, "ref": {"type": "string"}}, "required": ["project_id", "path"]}, get_file, phases=["integrations"]))
    registry.register(ToolSpec("iogitlab_create_branch", "Create a branch in ioGitLab (adapter not configured; requires approval).", {"type": "object", "properties": {"project_id": {"type": "string"}, "branch": {"type": "string"}, "ref": {"type": "string"}}, "required": ["project_id", "branch"]}, create_branch, phases=["integrations"], destructive=True))
    registry.register(ToolSpec("iogitlab_open_merge_request", "Open a merge request in ioGitLab (adapter not configured; requires approval).", {"type": "object", "properties": {"project_id": {"type": "string"}, "source_branch": {"type": "string"}, "target_branch": {"type": "string"}, "title": {"type": "string"}}, "required": ["project_id", "source_branch", "target_branch", "title"]}, open_merge_request, phases=["integrations"], destructive=True))
