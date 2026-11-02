"""Controlled terminal execution through the sandbox runner."""
from __future__ import annotations

from typing import Any, Dict

from execution.policies.command_policy import allowed_env, validate_command
from tools.base import ToolContext, ToolResult


async def run_command(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    command = str(args.get("command") or "").strip()
    if not command:
        return ToolResult("error", message="command is required")
    timeout = int(args.get("timeout_seconds") or ctx.settings.command_timeout_seconds)
    timeout = max(1, min(timeout, 900))
    ok, reason, argv = validate_command(command)
    if not ok:
        return ToolResult("error", message=f"command blocked by policy: {reason}")
    result = await ctx.sandbox.execute(
        workspace_id=ctx.workspace_id,
        argv=argv or [],
        timeout_seconds=timeout,
        env=allowed_env(),
        network=False,
    )
    payload = result.to_dict()
    status = "success" if result.status == "success" else "error"
    summary = f"`{command}` exited {result.exit_code} in {result.duration_ms} ms"
    return ToolResult(status, payload, summary)


def register(registry) -> None:
    from tools.base import ToolSpec

    registry.register(ToolSpec(
        name="run_command",
        description="Run an allowlisted command inside the isolated workspace container.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_seconds": {"type": "integer"},
            },
            "required": ["command"],
        },
        handler=run_command,
        phases=["test", "devops"],
    ))
