"""IO Coding Agent FastAPI application."""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agent.config import get_settings
from agent.llm import LLMClient
from agent.orchestration.runner import TaskOrchestrator
from api.routes import approvals, capabilities, chat, health, models, tasks
from execution.sandbox.client import SandboxClient
from execution.workspace.manager import WorkspaceManager
from persistence import db
from persistence.repository import TaskRepository
from telemetry.events import EventBus
from tools.registry import build_registry

log = logging.getLogger(__name__)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _configure_logging(settings.log_level)
    log.info("starting io-coding-agent model=%s gateway=%s", settings.agent_model, settings.litellm_base_url)
    if not settings.agent_api_key:
        log.warning("AGENT_API_KEY is empty; the /v1 API is unauthenticated. Set it before exposing the service.")
    if not settings.litellm_api_key:
        log.warning("LITELLM_API_KEY is empty; model calls will likely fail with 401.")
    db.configure(settings.database_url)
    db.init_db()
    repo = TaskRepository()
    bus = EventBus()
    workspace = WorkspaceManager(settings)
    sandbox = SandboxClient(settings)
    llm = LLMClient(settings)
    registry = build_registry()
    orchestrator = TaskOrchestrator(settings, repo, bus, workspace, sandbox, llm, registry)
    app.state.settings = settings
    app.state.repo = repo
    app.state.bus = bus
    app.state.workspace = workspace
    app.state.sandbox = sandbox
    app.state.llm = llm
    app.state.registry = registry
    app.state.orchestrator = orchestrator
    try:
        yield
    finally:
        await sandbox.aclose()
        await llm.aclose()
        log.info("io-coding-agent stopped")


app = FastAPI(
    title="IO Coding Agent",
    description="Autonomous coding and DevOps agent for InfoOrigin. OpenAI-compatible for Open WebUI.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled error request_id=%s path=%s", request_id, request.url.path)
        return JSONResponse({"detail": "internal server error", "request_id": request_id}, status_code=500)
    duration_ms = int((time.monotonic() - started) * 1000)
    response.headers["X-Request-ID"] = request_id
    if request.url.path not in {"/health", "/ready"}:
        log.info("%s %s -> %s %sms request_id=%s", request.method, request.url.path, response.status_code, duration_ms, request_id)
    return response


app.include_router(health.router)
app.include_router(models.router)
app.include_router(chat.router)
app.include_router(tasks.router)
app.include_router(approvals.router)
app.include_router(capabilities.router)
