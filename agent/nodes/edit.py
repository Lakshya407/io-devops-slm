"""Node 4: ask the model for a minimal edit and apply it safely."""
from __future__ import annotations

import logging
from typing import Any, Dict

from agent.nodes.context import TaskContext
from agent.nodes.edit_utils import WorkflowError, build_file_context, request_and_apply_edits
from agent.prompts.system import EDIT_PROMPT
from agent.state.state import AgentState
from telemetry.sanitize import truncate

log = logging.getLogger(__name__)


def format_test_result(result: Dict[str, Any] | None, max_chars: int = 6_000) -> str:
    if not result:
        return "The test suite has not run yet. Use the test files above to understand the expected behaviour."
    parts = [
        f"command: {result.get('command') or '(unknown)'}",
        f"exit_code: {result.get('exit_code')}",
        f"timed_out: {result.get('timed_out')}",
        "stdout:",
        str(result.get("stdout") or "")[-max_chars // 2:],
        "stderr:",
        str(result.get("stderr") or "")[-max_chars // 2:],
    ]
    return truncate("\n".join(parts), max_chars)


async def edit_node(ctx: TaskContext, state: AgentState) -> Dict[str, Any]:
    request = state.get("request") or ""
    plan = state.get("plan") or []
    test_command = state.get("test_command")
    await ctx.emit("code.editing", "Editing files", display=True)
    try:
        file_context, ranges, editable = build_file_context(ctx, state)
    except Exception as exc:  # noqa: BLE001
        message = f"Could not prepare the edit context: {exc}"
        ctx.repo.update_task(ctx.task_id, status="failed", phase="edit", error=message)
        await ctx.emit("task.failed", message, display=True)
        return {"status": "failed", "phase": "edit", "error": message, "final_summary": message}

    if not editable:
        message = "No editable source file was found. I will not modify test files to make a test pass."
        ctx.repo.update_task(ctx.task_id, status="incomplete", phase="edit", summary=message)
        await ctx.emit("task.incomplete", message, display=True)
        return {"status": "incomplete", "phase": "edit", "final_summary": message, "error": None}

    prompt = EDIT_PROMPT.format(
        request=request,
        plan="\n".join(f"- {step}" for step in plan) or "(no plan)",
        file_context=file_context,
        test_command=test_command or "none detected",
        test_output=format_test_result(state.get("test_result")),
    )
    try:
        outcome = await request_and_apply_edits(ctx, prompt, allowed_ranges=ranges, phase="edit")
    except WorkflowError as exc:
        message = str(exc)
        ctx.repo.update_task(ctx.task_id, status="failed", phase="edit", error=message)
        await ctx.emit("task.failed", message, display=True)
        return {"status": "failed", "phase": "edit", "error": message, "final_summary": message}

    if outcome.approval:
        approval = ctx.repo.create_approval(
            task_id=ctx.task_id,
            action="edit",
            payload=outcome.approval,
            reason=outcome.approval.get("reason") or "Human approval is required before modifying protected files.",
        )
        ctx.repo.update_task(ctx.task_id, status="waiting_approval", phase="approval")
        await ctx.emit(
            "approval.required",
            "Waiting for approval before modifying protected files",
            approval,
            display=True,
        )
        return {
            "status": "waiting_approval",
            "phase": "approval",
            "approval": approval,
            "edits": outcome.approval.get("edits") or [],
            "error": None,
        }

    ctx.repo.update_task(ctx.task_id, phase="test", attempts=0)
    await ctx.emit(
        "code.edited",
        outcome.summary or "Code changes applied",
        {"edits": outcome.details, "files": sorted({d['path'] for d in outcome.details})},
        display=True,
    )
    return {
        "edits": outcome.details,
        "phase": "test",
        "status": "running",
        "error": None,
    }
