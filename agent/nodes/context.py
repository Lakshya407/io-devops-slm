"""Per-task execution context passed to LangGraph nodes."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict

from agent.config import Settings
from agent.llm import LLMClient
from execution.sandbox.client import SandboxClient
from execution.workspace.manager import WorkspaceManager
from persistence.repository import TaskRepository
from telemetry.events import EventBus
from telemetry.sanitize import sanitize_obj, truncate
from tools.base import ToolContext, ToolRegistry, ToolResult

log = logging.getLogger(__name__)


@dataclass
class TaskContext:
    settings: Settings
    repo: TaskRepository
    bus: EventBus
    workspace: WorkspaceManager
    sandbox: SandboxClient
    registry: ToolRegistry
    task_id: str
    workspace_id: str
    llm: LLMClient | None = None
    user_id: str | None = None
    approval_granted: bool = False
    allow_test_edits: bool = False
    usage: Dict[str, int] = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})

    async def emit(self, event_type: str, message: str, data: Any | None = None, display: bool = True) -> dict:
        event = self.repo.append_event(
            task_id=self.task_id,
            event_type=event_type,
            message=message,
            data=sanitize_obj(data) if data is not None else None,
            display=display,
        )
        await self.bus.publish(self.task_id, event)
        return event

    def tool_context(self) -> ToolContext:
        return ToolContext(
            settings=self.settings,
            workspace=self.workspace,
            sandbox=self.sandbox,
            task_id=self.task_id,
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            approval_granted=self.approval_granted,
            allow_test_edits=self.allow_test_edits,
        )

    async def tool(self, name: str, arguments: Dict[str, Any], *, phase: str | None = None) -> ToolResult:
        spec = self.registry.get(name)
        if spec is None:
            return ToolResult("error", message=f"unknown tool: {name}")
        if phase and not ("all" in spec.phases or phase in spec.phases):
            return ToolResult("error", message=f"tool {name} is not allowed in phase {phase}")
        await self.emit("tool.call", f"Running {name}", {"tool": name, "arguments": _short_args(arguments)}, display=True)
        started = time.monotonic()
        try:
            result = await spec.handler(self.tool_context(), arguments)
        except Exception as exc:  # noqa: BLE001
            log.exception("tool %s failed", name)
            result = ToolResult("error", message=f"{exc.__class__.__name__}: {exc}")
        duration_ms = int((time.monotonic() - started) * 1000)
        stored_result = result.to_dict()
        if not self.settings.store_raw_output:
            stored_result = _preview_result(stored_result)
        self.repo.add_tool_call(
            task_id=self.task_id,
            tool_name=name,
            arguments=arguments,
            result=stored_result,
            status=result.status,
            duration_ms=duration_ms,
        )
        await self.emit(
            "tool.result",
            result.message or f"{name}: {result.status}",
            {"tool": name, "status": result.status, "duration_ms": duration_ms},
            display=result.status in {"error", "approval_required", "disabled", "not_configured"},
        )
        return result


def _short_args(arguments: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in (arguments or {}).items():
        if isinstance(value, str):
            out[key] = truncate(value, 400)
        elif isinstance(value, list):
            out[key] = f"[{len(value)} items]"
        else:
            out[key] = value
    return out


def _preview_result(result: Dict[str, Any]) -> Dict[str, Any]:
    data = result.get("data") or {}
    if isinstance(data, dict):
        preview = {}
        for key, value in data.items():
            if isinstance(value, str):
                preview[key] = truncate(value, 1500)
            elif isinstance(value, (dict, list)):
                preview[key] = truncate(json.dumps(sanitize_obj(value), default=str), 1500)
            else:
                preview[key] = value
        result = {**result, "data": preview}
    return result
