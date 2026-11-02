"""Node 8: produce an honest, verified final report."""
from __future__ import annotations

import logging
from typing import Any, Dict

from agent.nodes.context import TaskContext
from agent.state.state import AgentState
from telemetry.sanitize import truncate

log = logging.getLogger(__name__)


def _last_output(result: Dict[str, Any] | None, max_chars: int = 4_000) -> str:
    if not result:
        return "(no test run)"
    parts = [
        f"$ {result.get('command')}",
        f"exit code: {result.get('exit_code')}",
        "--- stdout ---",
        str(result.get("stdout") or "").strip()[-max_chars // 2:],
        "--- stderr ---",
        str(result.get("stderr") or "").strip()[-max_chars // 2:],
    ]
    return truncate("\n".join(parts), max_chars)


async def finalize_node(ctx: TaskContext, state: AgentState) -> Dict[str, Any]:
    request = state.get("request") or ""
    test_result = state.get("test_result")
    tests_passed = bool(test_result and test_result.get("passed"))
    diff = state.get("diff") or ""
    has_diff = bool(diff.strip())
    attempts = int(state.get("attempts") or 0)
    status = state.get("status") or "incomplete"
    if status in {"running", "waiting_approval", None}:
        status = "completed" if tests_passed and has_diff else ("failed" if test_result and not tests_passed else "incomplete")

    files = sorted({str(item.get("path")) for item in (state.get("edits") or []) if item.get("path")})
    header = "✅ Task completed" if status == "completed" else ("❌ Task failed" if status == "failed" else "⚠️ Task incomplete")
    lines = [
        f"## {header}",
        "",
        f"**Repository:** {state.get('repo_url') or 'unknown'}",
        f"**Branch:** {state.get('branch') or 'unknown'}",
        f"**Workspace:** `{state.get('workspace_id') or ctx.workspace_id}`",
        f"**Model:** `{ctx.settings.agent_model}`",
        "",
        "### Verification",
        f"- Test command: `{(test_result or {}).get('command') or state.get('test_command') or 'none'}`",
        f"- Result: {'PASSED' if tests_passed else ('FAILED' if test_result else 'NOT RUN')}"
        + (f" (exit {test_result.get('exit_code')}, {test_result.get('duration_ms')} ms)" if test_result else ""),
        f"- Correction attempts: {attempts}/{state.get('max_attempts') or ctx.settings.max_attempts}",
        f"- Files changed: {len(files)}",
        "",
    ]
    if state.get("diff_stat"):
        lines += ["### Diff stat", "```text", state["diff_stat"], "```", ""]
    if status == "completed":
        lines += [
            "### Final diff",
            "```diff",
            truncate(diff, 12_000),
            "```",
            "",
            "The changes are on the dedicated branch above. Nothing was pushed to a remote. "
            "Review the diff and approve publishing separately.",
        ]
    elif status == "failed":
        lines += ["### Last test output", "```text", _last_output(test_result), "```", "", state.get("error") or "The task did not verify successfully."]
    else:
        lines += [state.get("error") or state.get("final_summary") or "The task did not reach a verified state."]

    summary = "\n".join(lines).strip()
    ctx.repo.save_summary(
        task_id=ctx.task_id,
        summary=summary,
        diff=diff,
        tests_passed=tests_passed if test_result else None,
        files_changed=len(files),
    )
    trace = {
        "task_id": ctx.task_id,
        "request": request,
        "repo_url": state.get("repo_url"),
        "branch": state.get("branch"),
        "plan": state.get("plan") or [],
        "edits": state.get("edits") or [],
        "test_result": test_result,
        "outcome": status,
        "attempts": attempts,
        "diff_stat": state.get("diff_stat"),
        "diff": truncate(diff, 20_000),
        "model": ctx.settings.agent_model,
        "usage": ctx.usage,
    }
    ctx.repo.save_trace(task_id=ctx.task_id, trace=trace)
    ctx.repo.mark_terminal(ctx.task_id, status, summary=summary, error=state.get("error"))
    event_type = {
        "completed": "task.completed",
        "failed": "task.failed",
        "incomplete": "task.incomplete",
        "needs_clarification": "task.needs_clarification",
        "rejected": "task.rejected",
    }.get(status, "task.incomplete")
    await ctx.emit(event_type, summary, {"status": status, "tests_passed": tests_passed, "files": files}, display=True)
    return {"status": status, "phase": "finalize", "final_summary": summary}
