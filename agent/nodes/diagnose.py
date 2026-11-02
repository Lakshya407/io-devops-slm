"""Node 6: bounded error recovery after a failed test run."""
from __future__ import annotations

import logging
from typing import Any, Dict

from agent.nodes.context import TaskContext
from agent.nodes.edit import format_test_result
from agent.nodes.edit_utils import WorkflowError, build_file_context, request_and_apply_edits
from agent.prompts.system import DIAGNOSE_PROMPT
from agent.state.state import AgentState

log = logging.getLogger(__name__)


async def diagnose_node(ctx: TaskContext, state: AgentState) -> Dict[str, Any]:
    attempts = int(state.get("attempts") or 0) + 1
    max_attempts = int(state.get("max_attempts") or ctx.settings.max_attempts)
    if attempts > max_attempts:
        message = f"Tests still fail after {max_attempts} correction attempts. Stopping to avoid an infinite loop."
        ctx.repo.update_task(ctx.task_id, status="failed", phase="diagnose", attempts=attempts, summary=message)
        await ctx.emit("task.failed", message, display=True)
        return {"status": "failed", "phase": "diagnose", "attempts": attempts, "final_summary": message, "error": message}

    await ctx.emit(
        "debugging.started",
        f"Debugging test failure (attempt {attempts}/{max_attempts})",
        {"attempt": attempts, "max_attempts": max_attempts},
        display=True,
    )
    try:
        file_context, ranges, editable = build_file_context(ctx, state)
    except Exception as exc:  # noqa: BLE001
        message = f"Could not prepare the diagnosis context: {exc}"
        ctx.repo.update_task(ctx.task_id, status="failed", phase="diagnose", error=message)
        await ctx.emit("task.failed", message, display=True)
        return {"status": "failed", "phase": "diagnose", "attempts": attempts, "error": message, "final_summary": message}

    if not editable:
        message = "No editable source file is available to correct the failure."
        ctx.repo.update_task(ctx.task_id, status="failed", phase="diagnose", attempts=attempts, summary=message)
        await ctx.emit("task.failed", message, display=True)
        return {"status": "failed", "phase": "diagnose", "attempts": attempts, "error": message, "final_summary": message}

    previous = state.get("edits") or []
    previous_text = "\n".join(
        f"- {item.get('path')}:{item.get('start_line')}-{item.get('end_line')} -> {str(item.get('after'))[:300]}"
        for item in previous[-8:]
    ) or "(none)"
    prompt = DIAGNOSE_PROMPT.format(
        request=state.get("request") or "",
        plan="\n".join(f"- {step}" for step in (state.get("plan") or [])) or "(no plan)",
        test_command=state.get("test_command") or "unknown",
        test_output=format_test_result(state.get("test_result")),
        file_context=file_context,
        previous_edits=previous_text,
    )
    try:
        outcome = await request_and_apply_edits(ctx, prompt, allowed_ranges=ranges, phase="edit")
    except WorkflowError as exc:
        message = str(exc)
        ctx.repo.update_task(ctx.task_id, status="failed", phase="diagnose", attempts=attempts, error=message)
        await ctx.emit("task.failed", message, display=True)
        return {"status": "failed", "phase": "diagnose", "attempts": attempts, "error": message, "final_summary": message}

    if outcome.approval:
        approval = ctx.repo.create_approval(
            task_id=ctx.task_id,
            action="edit",
            payload=outcome.approval,
            reason=outcome.approval.get("reason") or "Human approval is required before modifying protected files.",
        )
        ctx.repo.update_task(ctx.task_id, status="waiting_approval", phase="approval", attempts=attempts)
        await ctx.emit("approval.required", "Waiting for approval before modifying protected files", approval, display=True)
        return {
            "status": "waiting_approval",
            "phase": "approval",
            "approval": approval,
            "attempts": attempts,
            "edits": previous + (outcome.approval.get("edits") or []),
            "error": None,
        }

    combined = previous + outcome.details
    ctx.repo.update_task(ctx.task_id, phase="test", attempts=attempts)
    await ctx.emit(
        "debugging.correction_applied",
        outcome.summary or "Applied a corrective edit",
        {"attempt": attempts, "edits": outcome.details},
        display=True,
    )
    return {
        "edits": combined,
        "attempts": attempts,
        "phase": "test",
        "status": "running",
        "error": None,
    }
