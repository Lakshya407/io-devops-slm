"""Node 5: execute the real test suite in the isolated sandbox."""
from __future__ import annotations

import logging
from typing import Any, Dict

from agent.nodes.context import TaskContext
from agent.state.state import AgentState
from execution.policies.command_policy import validate_command

log = logging.getLogger(__name__)


async def test_node(ctx: TaskContext, state: AgentState) -> Dict[str, Any]:
    command = state.get("test_command")
    if not command:
        message = (
            "No test command could be detected for this repository. The code changes were applied, "
            "but I cannot claim they are verified. Provide a test command (for example `python -m pytest -q`) "
            "and rerun the task."
        )
        ctx.repo.update_task(ctx.task_id, status="incomplete", phase="test", summary=message)
        await ctx.emit("task.incomplete", message, display=True)
        return {"status": "incomplete", "phase": "test", "final_summary": message, "error": None, "test_result": None}

    ok, reason, argv = validate_command(command)
    if not ok:
        message = f"The detected test command was blocked by policy: {reason}"
        ctx.repo.update_task(ctx.task_id, status="failed", phase="test", error=message)
        await ctx.emit("task.failed", message, display=True)
        return {"status": "failed", "phase": "test", "error": message, "final_summary": message, "test_result": None}

    await ctx.emit("test.running", f"Running tests: {command}", {"command": command}, display=True)
    result = await ctx.tool("run_command", {"command": command, "timeout_seconds": ctx.settings.command_timeout_seconds}, phase="test")
    data = result.data or {}
    exit_code = data.get("exit_code")
    passed = result.status == "success" and exit_code == 0
    payload = {
        "command": command,
        "status": result.status,
        "exit_code": exit_code,
        "stdout": data.get("stdout", ""),
        "stderr": data.get("stderr", ""),
        "duration_ms": data.get("duration_ms", 0),
        "timed_out": data.get("timed_out", False),
        "truncated": data.get("truncated", False),
        "error": data.get("error"),
        "passed": passed,
    }
    ctx.repo.update_task(ctx.task_id, tests_passed=passed, phase="verify" if passed else "diagnose")
    if passed:
        await ctx.emit(
            "test.passed",
            f"Tests passed (exit 0, {payload['duration_ms']} ms)",
            {"command": command, "exit_code": exit_code, "duration_ms": payload["duration_ms"]},
            display=True,
        )
        return {"test_result": payload, "phase": "verify", "status": "running", "error": None}
    await ctx.emit(
        "test.failed",
        f"Tests failed (exit {exit_code}); diagnosing",
        {"command": command, "exit_code": exit_code, "duration_ms": payload["duration_ms"]},
        display=True,
    )
    return {"test_result": payload, "phase": "diagnose", "status": "running", "error": None}
