"""Node 1: understand the request and create an isolated workspace."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from agent.nodes.context import TaskContext
from agent.state.state import AgentState

log = logging.getLogger(__name__)

URL_RE = re.compile(r"(?:https?://|file://)[^\s<>\"'`]+")
EXPLICIT_RE = re.compile(r"(?im)^\s*(?:repo(?:sitory)?|workspace)\s*:\s*(\S+)\s*$")
FIXTURE_RE = re.compile(r"\bfixture://[A-Za-z0-9._-]+")


def extract_repo(text: str) -> str | None:
    if not text:
        return None
    explicit = EXPLICIT_RE.search(text)
    if explicit:
        return explicit.group(1).strip().strip(".,;:)]}")
    fixture = FIXTURE_RE.search(text)
    if fixture:
        return fixture.group(0)
    match = URL_RE.search(text)
    if match:
        return match.group(0).strip().strip(".,;:)]}")
    return None


def repo_from_conversation(messages: List[Dict[str, str]] | None) -> str | None:
    for message in reversed(messages or []):
        if message.get("role") == "user":
            found = extract_repo(message.get("content") or "")
            if found:
                return found
    return None


def _workspace_id_from_text(text: str) -> str | None:
    match = re.search(r"(?im)^\s*workspace\s*:\s*([A-Za-z0-9_-]{2,64})\s*$", text or "")
    return match.group(1) if match else None


async def understand_node(ctx: TaskContext, state: AgentState) -> Dict[str, Any]:
    await ctx.emit("repo.inspecting", "Inspecting repository and preparing an isolated workspace", display=True)
    request = state.get("request") or ""
    conversation = state.get("conversation") or []
    repo_url = state.get("repo_url") or extract_repo(request) or repo_from_conversation(conversation)
    workspace_id = state.get("workspace_id") or ctx.workspace_id
    ref = state.get("repo_ref")

    if not repo_url:
        message = (
            "Please provide a repository to work on. Include an HTTPS Git URL, a `repo:` line, "
            "or `workspace: <id>` for an existing workspace. For example:\n"
            "`repo: https://iogitlab.infoorigin.com/group/project.git`\n\n"
            "Fix the failing test in the project and show me the diff."
        )
        ctx.repo.update_task(ctx.task_id, status="needs_clarification", phase="understand", summary=message)
        await ctx.emit("task.needs_clarification", message, display=True)
        return {
            "status": "needs_clarification",
            "phase": "understand",
            "final_summary": message,
            "error": None,
        }

    # Existing workspace support (resume or pre-seeded).
    existing = _workspace_id_from_text(request)
    if existing and not state.get("workspace_id"):
        workspace_id = existing

    try:
        path = ctx.workspace.open(workspace_id)
        info = {"workspace_id": workspace_id, "path": str(path), "branch": state.get("branch") or "unknown", "repo_url": repo_url}
        if not state.get("repo_url"):
            branch_result = ctx.workspace.git(workspace_id, ["branch", "--show-current"])
            info["branch"] = branch_result["stdout"].strip() or "unknown"
    except Exception:
        try:
            info = ctx.workspace.create(workspace_id=workspace_id, repo_url=repo_url, ref=ref)
        except Exception as exc:  # noqa: BLE001
            message = f"Could not prepare the repository workspace: {exc}"
            ctx.repo.update_task(ctx.task_id, status="failed", phase="understand", error=message, summary=message)
            await ctx.emit("task.failed", message, display=True)
            return {"status": "failed", "phase": "understand", "error": message, "final_summary": message}

    ctx.repo.create_workspace(
        workspace_id=info["workspace_id"],
        task_id=ctx.task_id,
        repo_url=info.get("repo_url") or repo_url,
        branch=info.get("branch") or "unknown",
        path=info.get("path") or "",
    )
    ctx.repo.update_task(
        ctx.task_id,
        workspace_id=info["workspace_id"],
        branch=info.get("branch"),
        repo_url=info.get("repo_url") or repo_url,
        phase="inspect",
    )
    await ctx.emit(
        "repo.ready",
        f"Repository ready on branch {info.get('branch')}",
        {"workspace_id": info["workspace_id"], "branch": info.get("branch")},
        display=True,
    )
    return {
        "repo_url": info.get("repo_url") or repo_url,
        "workspace_id": info["workspace_id"],
        "branch": info.get("branch"),
        "phase": "inspect",
        "status": "running",
        "error": None,
    }
