"""Docker tools.

Builds run through the controlled sandbox runner, never through a Docker socket
mounted into a coding workspace. Build execution is disabled by default
(``AGENT_ENABLE_DOCKER_BUILD=false``) and returns an explicit ``disabled``
status when off; it never pretends to have built anything.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Dict, List

import yaml

from tools.base import ToolContext, ToolResult


async def dockerfile_inspect(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    files = [f for f in ctx.workspace.list_files(ctx.workspace_id) if PurePosixPath(f).name.lower().startswith("dockerfile") or PurePosixPath(f).name.lower().endswith(".dockerfile")]
    if not files:
        return ToolResult("success", {"dockerfiles": []}, "no Dockerfile found")
    found = []
    for rel in files[:10]:
        try:
            content, truncated = ctx.workspace.read_text(ctx.workspace_id, rel, max_bytes=100_000)
        except Exception as exc:  # noqa: BLE001
            found.append({"path": rel, "error": str(exc)})
            continue
        found.append({"path": rel, "content": content, "truncated": truncated})
    return ToolResult("success", {"dockerfiles": found}, f"found {len(found)} Dockerfile(s)")


async def docker_build(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    if not ctx.settings.enable_docker_build:
        return ToolResult(
            "disabled",
            {"enabled": False},
            "Docker builds are disabled. Set AGENT_ENABLE_DOCKER_BUILD=true and use the sandbox runner's restricted build endpoint.",
        )
    dockerfile = str(args.get("dockerfile") or "Dockerfile")
    tag = str(args.get("tag") or f"io-agent-build:{ctx.workspace_id[:12]}")
    result = await ctx.sandbox.docker_build(
        workspace_id=ctx.workspace_id,
        dockerfile=dockerfile,
        tag=tag,
        timeout_seconds=min(int(args.get("timeout_seconds") or 600), 1800),
    )
    status = "success" if result.get("status") == "success" else "error"
    return ToolResult(status, result, result.get("message") or f"docker build {tag}")


async def docker_compose_validate(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    path = str(args.get("path") or "")
    candidates: List[str]
    if path:
        candidates = [path]
    else:
        candidates = [
            f for f in ctx.workspace.list_files(ctx.workspace_id)
            if PurePosixPath(f).name.lower() in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}
        ]
    if not candidates:
        return ToolResult("success", {"files": [], "valid": None}, "no compose file found")
    results = []
    for rel in candidates[:5]:
        try:
            content, _ = ctx.workspace.read_text(ctx.workspace_id, rel, max_bytes=200_000)
            document = yaml.safe_load(content)
        except Exception as exc:  # noqa: BLE001
            results.append({"path": rel, "valid": False, "errors": [f"could not parse YAML: {exc}"]})
            continue
        errors: List[str] = []
        warnings: List[str] = []
        if not isinstance(document, dict):
            errors.append("compose file must be a mapping")
        else:
            services = document.get("services")
            if not isinstance(services, dict) or not services:
                errors.append("compose file has no services mapping")
            else:
                for name, service in services.items():
                    if not isinstance(service, dict):
                        errors.append(f"service {name!r} must be a mapping")
                        continue
                    if service.get("privileged") is True:
                        errors.append(f"service {name!r} requests privileged mode")
                    if service.get("network_mode") == "host":
                        warnings.append(f"service {name!r} uses host networking")
                    for volume in service.get("volumes") or []:
                        if isinstance(volume, str) and "docker.sock" in volume:
                            errors.append(f"service {name!r} mounts the Docker socket")
        results.append({"path": rel, "valid": not errors, "errors": errors, "warnings": warnings})
    ok = all(item["valid"] for item in results)
    return ToolResult("success" if ok else "error", {"files": results, "valid": ok}, "compose validation completed")


def register(registry) -> None:
    from tools.base import ToolSpec

    registry.register(ToolSpec("dockerfile_inspect", "Locate and read Dockerfiles.", {"type": "object", "properties": {}}, dockerfile_inspect, phases=["inspect", "devops"]))
    registry.register(ToolSpec("docker_build", "Build a Docker image through the restricted sandbox runner (requires AGENT_ENABLE_DOCKER_BUILD=true).", {"type": "object", "properties": {"dockerfile": {"type": "string"}, "tag": {"type": "string"}, "timeout_seconds": {"type": "integer"}}}, docker_build, phases=["devops"], destructive=True))
    registry.register(ToolSpec("docker_compose_validate", "Validate Docker Compose files without running them.", {"type": "object", "properties": {"path": {"type": "string"}}}, docker_compose_validate, phases=["inspect", "devops"]))
