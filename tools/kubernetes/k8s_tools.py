"""Kubernetes read-only tools.

Milestone 1 deliberately ships these as disabled stubs. They return an explicit
``disabled`` status; they never claim to have queried a cluster. Milestone 3
will add a restricted runner with a read-only, least-privilege kubeconfig.
"""
from __future__ import annotations

from typing import Any, Dict

from tools.base import ToolContext, ToolResult

_DISABLED = (
    "Kubernetes tools are not enabled in this deployment. Set AGENT_ENABLE_KUBERNETES=true "
    "and configure a read-only, least-privilege kubeconfig in the restricted runner "
    "(Milestone 3). No cluster was queried."
)


async def _disabled(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return ToolResult("disabled", {"enabled": False}, _DISABLED)


async def k8s_get_pods(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _disabled(ctx, args)


async def k8s_get_deployments(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _disabled(ctx, args)


async def k8s_get_services(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _disabled(ctx, args)


async def k8s_describe(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _disabled(ctx, args)


async def k8s_logs(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _disabled(ctx, args)


async def k8s_get_events(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _disabled(ctx, args)


def register(registry) -> None:
    from tools.base import ToolSpec

    specs = [
        ("k8s_get_pods", "Read-only: list pods in a namespace.", k8s_get_pods),
        ("k8s_get_deployments", "Read-only: list deployments in a namespace.", k8s_get_deployments),
        ("k8s_get_services", "Read-only: list services in a namespace.", k8s_get_services),
        ("k8s_describe", "Read-only: describe a Kubernetes resource.", k8s_describe),
        ("k8s_logs", "Read-only: fetch pod logs.", k8s_logs),
        ("k8s_get_events", "Read-only: list namespace events.", k8s_get_events),
    ]
    for name, description, handler in specs:
        registry.register(ToolSpec(name, description, {"type": "object", "properties": {"namespace": {"type": "string"}, "name": {"type": "string"}}}, handler, phases=["devops"]))
