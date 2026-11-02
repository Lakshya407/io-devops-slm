"""Node 7: capture the real git diff before reporting."""
from __future__ import annotations

import logging
from typing import Any, Dict

from agent.nodes.context import TaskContext
from agent.state.state import AgentState

log = logging.getLogger(__name__)


async def verify_node(ctx: TaskContext, state: AgentState) -> Dict[str, Any]:
    await ctx.emit("changes.verifying", "Reviewing the final git diff", display=True)
    try:
        status = ctx.workspace.status(ctx.workspace_id)
        diff = ctx.workspace.diff(ctx.workspace_id)
        stat = ctx.workspace.diff_stat(ctx.workspace_id)
    except Exception as exc:  # noqa: BLE001
        message = f"Could not read the final git diff: {exc}"
        ctx.repo.update_task(ctx.task_id, status="failed", phase="verify", error=message)
        await ctx.emit("task.failed", message, display=True)
        return {"status": "failed", "phase": "verify", "error": message, "final_summary": message}

    if not diff.strip():
        message = "The workflow finished but the working tree has no changes. Nothing was modified."
        ctx.repo.update_task(ctx.task_id, status="incomplete", phase="verify", summary=message)
        await ctx.emit("task.incomplete", message, display=True)
        return {
            "git_status": status,
            "diff": diff,
            "diff_stat": stat,
            "status": "incomplete",
            "phase": "verify",
            "final_summary": message,
            "error": None,
        }

    await ctx.emit(
        "changes.verified",
        "Final diff captured:\n" + (stat or "(no stat)"),
        {"diff_stat": stat, "git_status": status},
        display=True,
    )
    return {
        "git_status": status,
        "diff": diff,
        "diff_stat": stat,
        "phase": "finalize",
        "status": "running",
        "error": None,
    }
