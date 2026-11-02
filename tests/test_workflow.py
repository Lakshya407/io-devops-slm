from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List

import pytest

from agent.config import Settings
from agent.llm import LLMResponse
from agent.nodes.context import TaskContext
from agent.orchestration.runner import TaskOrchestrator
from execution.sandbox.client import ExecResult
from execution.workspace.manager import WorkspaceManager
from persistence import db
from persistence.repository import TaskRepository
from telemetry.events import EventBus
from tools.registry import build_registry


class FakeLLM:
    """Deterministic stand-in for the Qwen model."""

    def __init__(self, edit_replacement: str = "def add(a, b):\n    return a + b") -> None:
        self.edit_replacement = edit_replacement
        self.calls: List[str] = []

    async def complete(self, messages, *, max_tokens: int = 900, temperature: float = 0.0, json_mode: bool = False, **_: Any) -> LLMResponse:
        user = messages[-1]["content"] if messages else ""
        self.calls.append(user[:120])
        if "Choose ONE next inspection action" in user:
            text = json.dumps({"thought": "enough context", "action": "finish", "arguments": {}})
        elif "Write an implementation plan" in user:
            text = json.dumps({
                "plan": ["Inspect add()", "Correct the operator", "Run pytest"],
                "files": ["app/calculator.py"],
                "test_command": "python -m pytest -q",
            })
        elif "The tests failed. Diagnose" in user:
            text = json.dumps({
                "thought": "the operator is still wrong",
                "edits": [{
                    "path": "app/calculator.py",
                    "start_line": 1,
                    "end_line": 2,
                    "replacement": self.edit_replacement,
                    "reason": "make add() add",
                }],
                "summary": "corrected add()",
            })
        elif "Make the minimal code change needed" in user:
            text = json.dumps({
                "thought": "add() subtracts instead of adds",
                "edits": [{
                    "path": "app/calculator.py",
                    "start_line": 1,
                    "end_line": 2,
                    "replacement": self.edit_replacement,
                    "reason": "use addition",
                }],
                "summary": "fixed add()",
            })
        else:
            text = json.dumps({"status": "incomplete", "summary": "unexpected prompt"})
        return LLMResponse(text=text, model="fake", latency_ms=1, prompt_tokens=10, completion_tokens=10, total_tokens=20)

    async def aclose(self) -> None:
        return None


class FakeSandbox:
    """Runs commands in the real workspace with the host Python for tests."""

    def __init__(self, workspace: WorkspaceManager) -> None:
        self.workspace = workspace

    async def execute(self, *, workspace_id: str, argv: List[str], timeout_seconds: int, env: Dict[str, str] | None = None, network: bool = False) -> ExecResult:
        cwd = self.workspace.path_for(workspace_id)
        started = __import__("time").monotonic()
        environment = {**os.environ, **(env or {})}
        environment.setdefault("PYTHONPATH", str(cwd))
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            return ExecResult(
                status="success" if completed.returncode == 0 else "error",
                exit_code=completed.returncode,
                stdout=completed.stdout[-20000:],
                stderr=completed.stderr[-20000:],
                duration_ms=int((__import__("time").monotonic() - started) * 1000),
                command=" ".join(argv),
                argv=argv,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(status="timeout", timed_out=True, command=" ".join(argv), argv=argv, error="timeout")

    async def health(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_workspace(root: Path, workspace_id: str) -> Path:
    path = root / workspace_id
    (path / "app").mkdir(parents=True)
    (path / "tests").mkdir()
    (path / "app" / "__init__.py").write_text("", encoding="utf-8")
    (path / "app" / "calculator.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (path / "tests" / "test_calculator.py").write_text(
        "from app.calculator import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    (path / "requirements.txt").write_text("pytest==8.3.4\n", encoding="utf-8")
    _git(path, "init", "-q", "-b", "main")
    _git(path, "add", ".")
    _git(path, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "fixture")
    _git(path, "checkout", "-q", "-b", f"io-agent/{workspace_id[:12]}")
    return path


@pytest.mark.asyncio
async def test_full_graph_fixes_failing_test_and_verifies_diff(tmp_path: Path) -> None:
    settings = Settings(
        workspaces_dir=str(tmp_path / "workspaces"),
        repos_dir=str(tmp_path / "repos"),
        max_context_files=6,
        max_inspect_steps=1,
        max_attempts=2,
        protect_tests=True,
        agent_model="qwen2.5-coder:1.5b",
    )
    (tmp_path / "repos").mkdir()
    db.configure("sqlite://")
    db.init_db()

    task_id = uuid.uuid4().hex
    workspace_manager = WorkspaceManager(settings)
    workspace_path = make_workspace(workspace_manager.root, task_id)

    repo = TaskRepository()
    repo.create_task(task_id=task_id, request="Fix the failing test", user_id="tester", model=settings.agent_model, max_attempts=2)

    llm = FakeLLM()
    sandbox = FakeSandbox(workspace_manager)
    orchestrator = TaskOrchestrator(
        settings=settings,
        repo=repo,
        bus=EventBus(),
        workspace=workspace_manager,
        sandbox=sandbox,  # type: ignore[arg-type]
        llm=llm,  # type: ignore[arg-type]
        registry=build_registry(),
    )

    result = await orchestrator.run_task(
        task_id=task_id,
        request=f"Fix the failing test and show me the verified diff.\nrepo: file://{workspace_path}",
        user_id="tester",
        conversation=[],
        repo_url=None,
        repo_ref=None,
    )

    assert result["status"] == "completed", result
    assert "return a + b" in (result.get("diff") or "")
    assert "return a - b" in (result.get("diff") or "")

    stored = repo.get_task(task_id)
    assert stored is not None
    assert stored.status == "completed"
    assert stored.tests_passed is True
    assert stored.branch and stored.branch.startswith("io-agent/")

    summary = repo.get_summary(task_id)
    assert summary is not None
    assert "Task completed" in summary["summary"]
    assert summary["tests_passed"] is True

    event_types = [event["type"] for event in repo.get_events(task_id)]
    assert "task.completed" in event_types
    assert "test.passed" in event_types
    assert "changes.verified" in event_types

    final_content = (workspace_path / "app" / "calculator.py").read_text(encoding="utf-8")
    assert "return a + b" in final_content


@pytest.mark.asyncio
async def test_protected_path_requires_approval(tmp_path: Path) -> None:
    from agent.nodes.edit_utils import build_file_context, request_and_apply_edits

    settings = Settings(
        workspaces_dir=str(tmp_path / "workspaces"),
        repos_dir=str(tmp_path / "repos"),
        max_context_files=4,
    )
    (tmp_path / "repos").mkdir()
    db.configure("sqlite://")
    db.init_db()

    task_id = uuid.uuid4().hex
    workspace_manager = WorkspaceManager(settings)
    workspace_path = make_workspace(workspace_manager.root, task_id)
    (workspace_path / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")

    repo = TaskRepository()
    repo.create_task(task_id=task_id, request="Update the Dockerfile", model=settings.agent_model)
    ctx = TaskContext(
        settings=settings,
        repo=repo,
        bus=EventBus(),
        workspace=workspace_manager,
        sandbox=FakeSandbox(workspace_manager),  # type: ignore[arg-type]
        registry=build_registry(),
        task_id=task_id,
        workspace_id=task_id,
        allow_test_edits=False,
    )

    class DockerfileLLM(FakeLLM):
        async def complete(self, messages, **kwargs):  # type: ignore[override]
            text = json.dumps({
                "thought": "base image",
                "edits": [{
                    "path": "Dockerfile",
                    "start_line": 1,
                    "end_line": 1,
                    "replacement": "FROM python:3.13-slim",
                    "reason": "update base image",
                }],
                "summary": "update",
            })
            return LLMResponse(text=text, model="fake", latency_ms=1)

    fake = DockerfileLLM()
    ctx.llm = fake  # type: ignore[attr-defined]
    state = {
        "context_files": {"Dockerfile": "FROM python:3.12-slim\n"},
        "edit_paths": ["Dockerfile"],
        "plan_files": ["Dockerfile"],
    }
    file_context, ranges, editable = build_file_context(ctx, state)  # type: ignore[arg-type]
    assert editable == ["Dockerfile"]
    outcome = await request_and_apply_edits(ctx, "Update the base image\n\n" + file_context, allowed_ranges=ranges, phase="edit")
    assert outcome.approval is not None
    assert outcome.approval["paths"] == ["Dockerfile"]
    assert (workspace_path / "Dockerfile").read_text(encoding="utf-8") == "FROM python:3.12-slim\n"
