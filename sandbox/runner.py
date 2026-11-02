"""Narrow, authenticated sandbox execution service.

This is the only process in the IO Coding Agent deployment that receives the
Docker socket. The agent calls it over the private Compose network with a
shared token. Each request starts a fresh, unprivileged, read-only container
with no network, no capabilities and a hard resource limit, mounts exactly one
workspace subdirectory, runs one argv list (never a shell) and removes the
container afterwards.
"""
from __future__ import annotations

import hmac
import logging
import os
import re
import shlex
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import docker
from docker.errors import APIError, BuildError, DockerException, ImageNotFound, NotFound
from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

logging.basicConfig(level=os.environ.get("SANDBOX_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("sandbox-runner")

WORKSPACE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
DENIED_BINARIES = {
    "sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "tcsh",
    "sudo", "su", "doas", "pkexec",
    "docker", "podman", "nerdctl", "buildah", "kubectl", "helm",
    "ssh", "scp", "sftp", "rsync", "nc", "netcat", "ncat", "nmap", "telnet", "socat",
    "curl", "wget", "rm", "rmdir", "unlink", "shred", "dd", "mkfs", "mount", "umount",
    "chown", "chmod", "chattr", "setfacl", "kill", "pkill", "killall",
    "systemctl", "service", "crontab", "at", "iptables", "nft", "ufw",
    "apt", "apt-get", "dpkg", "yum", "dnf", "apk", "snap",
    "useradd", "userdel", "usermod", "passwd", "groupadd",
}
ALLOWED_ENV_KEYS = {
    "CI", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "HOME", "TMPDIR",
    "NODE_ENV", "PYTEST_ADDOPTS", "LANG", "LC_ALL", "TZ",
}
MAX_ARGS = 64
MAX_COMMAND_CHARS = 8_000


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    def __init__(self) -> None:
        self.token = os.environ.get("SANDBOX_TOKEN", "")
        self.workspaces_root = Path(os.environ.get("AGENT_WORKSPACES_HOST_DIR", "/srv/io-agent/workspaces")).resolve()
        self.image = os.environ.get("SANDBOX_IMAGE", "io-agent-sandbox:latest")
        self.allow_network = _env_bool("SANDBOX_ALLOW_NETWORK", False)
        self.enable_docker_build = _env_bool("SANDBOX_ENABLE_DOCKER_BUILD", False)
        self.max_output_bytes = int(os.environ.get("SANDBOX_MAX_OUTPUT_BYTES", "200000"))
        self.memory = os.environ.get("SANDBOX_MEMORY", "768m")
        self.cpus = float(os.environ.get("SANDBOX_CPUS", "1.0"))
        self.pids_limit = int(os.environ.get("SANDBOX_PIDS_LIMIT", "256"))
        self.tmpfs_size = os.environ.get("SANDBOX_TMPFS_SIZE", "256m")


config = Config()


class ExecRequest(BaseModel):
    workspace_id: str
    argv: List[str] = Field(default_factory=list)
    timeout_seconds: int = 300
    env: Dict[str, str] = Field(default_factory=dict)
    network: bool = False


class BuildRequest(BaseModel):
    workspace_id: str
    dockerfile: str = "Dockerfile"
    tag: str
    timeout_seconds: int = 600


def require_token(x_sandbox_token: Optional[str] = Header(default=None)) -> None:
    if not config.token:
        # Fail closed: an unauthenticated runner is never usable.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="sandbox token is not configured")
    if not x_sandbox_token or not hmac.compare_digest(x_sandbox_token, config.token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid sandbox token")


def validate_workspace_id(workspace_id: str) -> str:
    if not isinstance(workspace_id, str) or not WORKSPACE_ID_RE.match(workspace_id):
        raise HTTPException(status_code=400, detail="invalid workspace id")
    return workspace_id


def workspace_path(workspace_id: str) -> Path:
    validate_workspace_id(workspace_id)
    candidate = (config.workspaces_root / workspace_id).resolve()
    if candidate != config.workspaces_root and config.workspaces_root not in candidate.parents:
        raise HTTPException(status_code=400, detail="workspace escapes the workspace root")
    if not candidate.is_dir():
        raise HTTPException(status_code=404, detail="workspace does not exist")
    return candidate


def validate_argv(argv: List[str]) -> List[str]:
    if not isinstance(argv, list) or not argv:
        raise HTTPException(status_code=400, detail="argv is required")
    if len(argv) > MAX_ARGS:
        raise HTTPException(status_code=400, detail=f"too many arguments (max {MAX_ARGS})")
    if not all(isinstance(item, str) and item for item in argv):
        raise HTTPException(status_code=400, detail="argv entries must be non-empty strings")
    if sum(len(item) for item in argv) > MAX_COMMAND_CHARS:
        raise HTTPException(status_code=400, detail="command is too long")
    binary = argv[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "/" in argv[0] or "\\" in argv[0]:
        raise HTTPException(status_code=400, detail="executable must be a PATH command")
    if binary in DENIED_BINARIES:
        raise HTTPException(status_code=400, detail=f"binary {binary!r} is denied")
    for item in argv:
        if "\x00" in item or "\n" in item or "\r" in item:
            raise HTTPException(status_code=400, detail="argv contains control characters")
    return argv


def validate_env(env: Dict[str, str]) -> Dict[str, str]:
    clean: Dict[str, str] = {}
    for key, value in (env or {}).items():
        if key not in ALLOWED_ENV_KEYS:
            continue
        if not isinstance(value, str) or len(value) > 2000:
            continue
        clean[key] = value
    clean.setdefault("HOME", "/tmp")
    clean.setdefault("TMPDIR", "/tmp")
    clean.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    clean.setdefault("PYTHONUNBUFFERED", "1")
    return clean


def _decode(value: bytes, max_bytes: int) -> tuple[str, bool]:
    truncated = len(value) > max_bytes
    return value[:max_bytes].decode("utf-8", errors="replace"), truncated


def run_container(client: docker.DockerClient, request: ExecRequest) -> Dict[str, Any]:
    workspace = workspace_path(request.workspace_id)
    argv = validate_argv(list(request.argv))
    env = validate_env(request.env)
    timeout_seconds = max(1, min(int(request.timeout_seconds or 300), 900))
    use_network = bool(request.network)
    if use_network and not config.allow_network:
        raise HTTPException(status_code=403, detail="network access is disabled for sandbox tasks")

    name = f"io-agent-exec-{uuid.uuid4().hex[:12]}"
    started = time.monotonic()
    container = None
    timed_out = False
    exit_code: Optional[int] = None
    stdout_bytes = b""
    stderr_bytes = b""
    error: Optional[str] = None

    kwargs: Dict[str, Any] = {
        "image": config.image,
        "entrypoint": argv[0],
        "command": argv[1:],
        "name": name,
        "detach": True,
        "remove": False,
        "network_disabled": not use_network,
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "pids_limit": config.pids_limit,
        "mem_limit": config.memory,
        "memswap_limit": config.memory,
        "nano_cpus": int(config.cpus * 1_000_000_000),
        "tmpfs": {"/tmp": f"rw,exec,nosuid,size={config.tmpfs_size}"},
        "user": "10001:10001",
        "volumes": {str(workspace): {"bind": "/workspace", "mode": "rw"}},
        "working_dir": "/workspace",
        "environment": env,
        "labels": {"io-agent.task": "exec", "io-agent.workspace": request.workspace_id},
        "stdout": True,
        "stderr": True,
    }
    try:
        container = client.containers.run(**kwargs)
        try:
            result = container.wait(timeout=timeout_seconds)
            exit_code = int(result.get("StatusCode", 1))
        except Exception as exc:  # noqa: BLE001 - timeout is a requests exception from the SDK
            timed_out = True
            error = f"command timed out after {timeout_seconds}s"
            log.warning("container %s timed out (%s)", name, exc.__class__.__name__)
            try:
                container.kill()
            except Exception:  # noqa: BLE001
                pass
        try:
            stdout_bytes = container.logs(stdout=True, stderr=False)
            stderr_bytes = container.logs(stdout=False, stderr=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not read logs for %s: %s", name, exc)
            if not stdout_bytes and not stderr_bytes:
                try:
                    stdout_bytes = container.logs(stdout=True, stderr=True)
                except Exception:  # noqa: BLE001
                    pass
    except ImageNotFound:
        raise HTTPException(status_code=503, detail=f"sandbox image {config.image!r} is not available")
    except (APIError, DockerException) as exc:
        log.exception("docker execution failed")
        raise HTTPException(status_code=502, detail=f"docker execution failed: {exc.__class__.__name__}") from exc
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except NotFound:
                pass
            except Exception as exc:  # noqa: BLE001
                log.warning("could not remove container %s: %s", name, exc)

    stdout, stdout_truncated = _decode(stdout_bytes, config.max_output_bytes)
    stderr, stderr_truncated = _decode(stderr_bytes, config.max_output_bytes)
    duration_ms = int((time.monotonic() - started) * 1000)
    return {
        "status": "timeout" if timed_out else "success",
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": duration_ms,
        "timed_out": timed_out,
        "truncated": stdout_truncated or stderr_truncated,
        "command": shlex.join(argv),
        "argv": argv,
        "error": error,
    }


def build_image(client: docker.DockerClient, request: BuildRequest) -> Dict[str, Any]:
    if not config.enable_docker_build:
        raise HTTPException(status_code=403, detail="docker builds are disabled; set SANDBOX_ENABLE_DOCKER_BUILD=true")
    workspace = workspace_path(request.workspace_id)
    if not TAG_RE.match(request.tag or ""):
        raise HTTPException(status_code=400, detail="invalid image tag")
    dockerfile = (request.dockerfile or "Dockerfile").replace("\\", "/")
    if dockerfile.startswith("/") or ".." in Path(dockerfile).parts:
        raise HTTPException(status_code=400, detail="invalid dockerfile path")
    if not (workspace / dockerfile).is_file():
        raise HTTPException(status_code=404, detail="dockerfile not found")
    timeout_seconds = max(1, min(int(request.timeout_seconds or 600), 1800))
    try:
        image, logs = client.images.build(
            path=str(workspace),
            dockerfile=dockerfile,
            tag=request.tag,
            rm=True,
            forcerm=True,
            timeout=timeout_seconds,
        )
    except (BuildError, APIError, DockerException) as exc:
        raise HTTPException(status_code=502, detail=f"docker build failed: {exc}") from exc
    text = "\n".join(str(getattr(item, "get", lambda *_: "")("stream", "")) for item in logs if isinstance(item, dict))
    return {"status": "success", "tag": request.tag, "image_id": image.id, "log": text[-20_000:]}


app = FastAPI(title="IO Agent Sandbox Runner", version="0.1.0")


@app.on_event("startup")
def startup() -> None:
    if not config.token:
        log.error("SANDBOX_TOKEN is empty: all execution requests will be rejected")
    log.info("sandbox runner ready image=%s workspaces=%s network=%s", config.image, config.workspaces_root, config.allow_network)


@app.get("/health")
def health() -> Dict[str, Any]:
    payload: Dict[str, Any] = {"status": "ok", "image": config.image, "docker": False, "image_present": False}
    try:
        client = docker.from_env()
        payload["docker"] = bool(client.ping())
        try:
            client.images.get(config.image)
            payload["image_present"] = True
        except ImageNotFound:
            payload["image_present"] = False
    except DockerException as exc:
        payload["status"] = "degraded"
        payload["error"] = exc.__class__.__name__
    if not payload["docker"] or not payload["image_present"]:
        payload["status"] = "degraded"
    return payload


@app.post("/v1/exec", dependencies=[Depends(require_token)])
def execute(request: ExecRequest) -> Dict[str, Any]:
    client = docker.from_env()
    return run_container(client, request)


@app.post("/v1/docker/build", dependencies=[Depends(require_token)])
def docker_build(request: BuildRequest) -> Dict[str, Any]:
    client = docker.from_env()
    return build_image(client, request)
