"""Client for the controlled sandbox execution service.

The agent never talks to the Docker socket. Only the sandbox runner does, and it
exposes a narrow, authenticated API for running commands inside ephemeral,
unprivileged containers.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

import httpx

from agent.config import Settings

log = logging.getLogger(__name__)


@dataclass
class ExecResult:
    status: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    truncated: bool = False
    command: str = ""
    argv: List[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "truncated": self.truncated,
            "command": self.command,
            "error": self.error,
        }

    @property
    def ok(self) -> bool:
        return self.status == "success" and self.exit_code == 0


class SandboxError(RuntimeError):
    pass


class SandboxClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.sandbox_url,
            timeout=httpx.Timeout(connect=10.0, read=settings.command_timeout_seconds + 60, write=30.0, pool=10.0),
            headers={"X-Sandbox-Token": settings.sandbox_token} if settings.sandbox_token else {},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> bool:
        try:
            response = await self._client.get("/health", timeout=5.0)
            return response.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    async def execute(
        self,
        *,
        workspace_id: str,
        argv: List[str],
        timeout_seconds: int,
        env: Dict[str, str] | None = None,
        network: bool = False,
    ) -> ExecResult:
        payload = {
            "workspace_id": workspace_id,
            "argv": argv,
            "timeout_seconds": timeout_seconds,
            "env": env or {},
            "network": network,
        }
        try:
            response = await self._client.post("/v1/exec", json=payload)
        except httpx.HTTPError as exc:
            log.error("sandbox request failed: %s", exc)
            return ExecResult(status="error", error=f"sandbox unavailable: {exc}", argv=argv)
        if response.status_code >= 400:
            detail = response.text[:1000]
            return ExecResult(status="error", error=f"sandbox returned {response.status_code}: {detail}", argv=argv)
        data = response.json()
        return ExecResult(
            status=data.get("status", "error"),
            exit_code=data.get("exit_code"),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            duration_ms=int(data.get("duration_ms", 0)),
            timed_out=bool(data.get("timed_out", False)),
            truncated=bool(data.get("truncated", False)),
            command=data.get("command", ""),
            argv=data.get("argv", argv),
            error=data.get("error"),
        )

    async def docker_build(
        self,
        *,
        workspace_id: str,
        dockerfile: str = "Dockerfile",
        tag: str,
        timeout_seconds: int = 600,
    ) -> Dict[str, Any]:
        payload = {
            "workspace_id": workspace_id,
            "dockerfile": dockerfile,
            "tag": tag,
            "timeout_seconds": timeout_seconds,
        }
        try:
            response = await self._client.post("/v1/docker/build", json=payload, timeout=timeout_seconds + 60)
        except httpx.HTTPError as exc:
            return {"status": "error", "error": f"sandbox unavailable: {exc}"}
        if response.status_code >= 400:
            return {"status": "error", "error": f"sandbox returned {response.status_code}: {response.text[:1000]}"}
        return response.json()
