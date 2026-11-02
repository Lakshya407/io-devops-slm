"""Tool contracts shared by the registry and the agent nodes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List

from agent.config import Settings
from execution.sandbox.client import SandboxClient
from execution.workspace.manager import WorkspaceManager


@dataclass
class ToolResult:
    status: str  # success | error | approval_required | disabled | not_configured
    data: Dict[str, Any] = field(default_factory=dict)
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "message": self.message, "data": self.data}


@dataclass
class ToolContext:
    settings: Settings
    workspace: WorkspaceManager
    sandbox: SandboxClient
    task_id: str
    workspace_id: str
    user_id: str | None = None
    approval_granted: bool = False
    allow_test_edits: bool = False


ToolHandler = Callable[[ToolContext, Dict[str, Any]], Awaitable[ToolResult]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: ToolHandler
    phases: List[str] = field(default_factory=lambda: ["all"])
    destructive: bool = False

    def schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return sorted(self._tools)

    def allowed_actions(self, phase: str) -> set[str]:
        return {
            name for name, spec in self._tools.items()
            if "all" in spec.phases or phase in spec.phases
        }

    def specs_for(self, phase: str) -> List[Dict[str, Any]]:
        return [spec.schema() for spec in self._tools.values() if "all" in spec.phases or phase in spec.phases]
