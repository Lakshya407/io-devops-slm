"""Task orchestration: create tasks, run the LangGraph workflow, resume approvals."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List

from agent.config import Settings
from agent.graph.builder import build_continuation_graph, build_graph
from agent.llm import LLMClient
from agent.nodes.context import TaskContext
from execution.policies.path_policy import task_allows_test_edits
from execution.sandbox.client import SandboxClient
from execution.workspace.manager import WorkspaceManager
from persistence.repository import TERMINAL_STATUSES, TaskRepository
from telemetry.events import EventBus
from telemetry.sanitize import sanitize_text, truncate
from tools.base import ToolRegistry
from tools.filesystem.edit import EditError, LineEdit, apply_line_edits as apply_pure, check_syntax

log = logging.getLogger(__name__)


class TaskOrchestrator:
    def __init__(
        self,
        settings: Settings,
        repo: TaskRepository,
        bus: EventBus,
        workspace: WorkspaceManager,
        sandbox: SandboxClient,
        llm: LLMClient,
        registry: ToolRegistry,
    ):
        self.settings = settings
        self.repo = repo
        self.bus = bus
        self.workspace = workspace
        self.sandbox = sandbox
        self.llm = llm
        self.registry = registry
        self.semaphore = asyncio.Semaphore(settings.max_concurrent_tasks)
        self._running: Dict[str, asyncio.Task] = {}

    # --- task creation --------------------------------------------------
    def create_task(
        self,
        *,
        request: str,
        user_id: str | None,
        conversation_id: str | None,
        repo_url: str | None = None,
        repo_ref: str | None = None,
    ) -> str:
        task_id = uuid.uuid4().hex
        self.repo.create_task(
            task_id=task_id,
            request=request,
            user_id=user_id,
            conversation_id=conversation_id,
            repo_url=repo_url,
            model=self.settings.agent_model,
            max_attempts=self.settings.max_attempts,
        )
        return task_id

    def start_task(
        self,
        *,
        task_id: str,
        request: str,
        user_id: str | None,
        conversation: List[Dict[str, str]] | None = None,
        repo_url: str | None = None,
        repo_ref: str | None = None,
    ) -> asyncio.Task:
        existing = self._running.get(task_id)
        if existing and not existing.done():
            return existing
        task = asyncio.create_task(
            self.run_task(
                task_id=task_id,
                request=request,
                user_id=user_id,
                conversation=conversation or [],
                repo_url=repo_url,
                repo_ref=repo_ref,
            )
        )
        self._running[task_id] = task
        task.add_done_callback(lambda _t: self._running.pop(task_id, None))
        return task

    # --- execution ------------------------------------------------------
    def _make_context(self, task_id: str, user_id: str | None, request: str, approval_granted: bool = False) -> TaskContext:
        allow_test_edits = True if not self.settings.protect_tests else task_allows_test_edits(request)
        return TaskContext(
            settings=self.settings,
            repo=self.repo,
            bus=self.bus,
            workspace=self.workspace,
            sandbox=self.sandbox,
            registry=self.registry,
            task_id=task_id,
            workspace_id=task_id,
            llm=self.llm,
            user_id=user_id,
            approval_granted=approval_granted,
            allow_test_edits=allow_test_edits,
        )

    async def run_task(
        self,
        *,
        task_id: str,
        request: str,
        user_id: str | None,
        conversation: List[Dict[str, str]],
        repo_url: str | None,
        repo_ref: str | None,
    ) -> Dict[str, Any]:
        async with self.semaphore:
            self.repo.set_running(task_id)
            ctx = self._make_context(task_id, user_id, request)
            await ctx.emit("task.started", "Starting IO Coding Agent task", {"request": truncate(request, 2000), "model": self.settings.agent_model}, display=True)
            state: Dict[str, Any] = {
                "task_id": task_id,
                "request": request,
                "conversation": conversation,
                "user_id": user_id,
                "repo_url": repo_url,
                "repo_ref": repo_ref,
                "workspace_id": task_id,
                "attempts": 0,
                "max_attempts": self.settings.max_attempts,
                "edits": [],
                "status": "running",
                "phase": "understand",
                "approval_granted": False,
            }
            try:
                graph = build_graph(ctx)
                last = await asyncio.wait_for(
                    self._consume_graph(graph, state, ctx),
                    timeout=self.settings.task_timeout_seconds,
                )
                if last and last.get("status") not in TERMINAL_STATUSES and last.get("status") != "waiting_approval":
                    self.repo.update_task(task_id, status="incomplete", phase=last.get("phase") or "finalize")
                return last or {}
            except asyncio.TimeoutError:
                message = f"Task exceeded the {self.settings.task_timeout_seconds}s execution limit and was stopped."
                self.repo.update_task(task_id, status="timeout", phase="timeout", error=message, summary=message)
                await ctx.emit("task.timeout", message, display=True)
                return {"status": "timeout", "final_summary": message}
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("task %s failed", task_id)
                message = f"Agent execution failed: {exc.__class__.__name__}: {exc}"
                self.repo.update_task(task_id, status="failed", phase="error", error=sanitize_text(message, 4000), summary=message)
                await ctx.emit("task.failed", message, display=True)
                return {"status": "failed", "final_summary": message}
            finally:
                await self.bus.close(task_id)

    async def _consume_graph(self, graph, state: Dict[str, Any], ctx: TaskContext) -> Dict[str, Any]:
        last: Dict[str, Any] = dict(state)
        async for snapshot in graph.astream(state, {"recursion_limit": 60}, stream_mode="values"):
            if not isinstance(snapshot, dict):
                continue
            last = {**last, **snapshot}
            self._persist_snapshot(ctx.task_id, last)
        return last

    def _persist_snapshot(self, task_id: str, state: Dict[str, Any]) -> None:
        test_result = state.get("test_result") or {}
        minimal = {
            "task_id": task_id,
            "request": state.get("request"),
            "repo_url": state.get("repo_url"),
            "repo_ref": state.get("repo_ref"),
            "workspace_id": state.get("workspace_id"),
            "branch": state.get("branch"),
            "plan": state.get("plan") or [],
            "plan_files": state.get("plan_files") or [],
            "edit_paths": state.get("edit_paths") or [],
            "test_command": state.get("test_command"),
            "attempts": state.get("attempts") or 0,
            "max_attempts": state.get("max_attempts") or self.settings.max_attempts,
            "edits": state.get("edits") or [],
            "diff_stat": state.get("diff_stat"),
            "status": state.get("status"),
            "phase": state.get("phase"),
            "approval": state.get("approval"),
            "error": state.get("error"),
            "final_summary": state.get("final_summary"),
            "approval_granted": state.get("approval_granted", False),
        }
        fields: Dict[str, Any] = {
            "phase": state.get("phase"),
            "attempts": int(state.get("attempts") or 0),
            "tests_passed": test_result.get("passed") if test_result else None,
            "diff_stat": state.get("diff_stat"),
            "state_json": json.dumps(minimal, default=str),
        }
        if state.get("status") == "waiting_approval":
            fields["status"] = "waiting_approval"
        self.repo.update_task(task_id, **fields)

    # --- approvals ------------------------------------------------------
    async def resume_after_approval(self, *, approval_id: str, decision: str, approver: str) -> Dict[str, Any]:
        approval = self.repo.get_approval(approval_id)
        if approval is None:
            raise ValueError("approval not found")
        if approval.status != "pending":
            raise ValueError(f"approval is already {approval.status}")
        status = "approved" if decision == "approve" else "rejected"
        decided = self.repo.decide_approval(approval_id, status=status, decided_by=approver)
        task = self.repo.get_task(approval.task_id)
        if task is None:
            raise ValueError("task not found")
        if status == "rejected":
            message = f"The requested action was rejected by {approver}."
            self.repo.mark_terminal(task.id, "rejected", summary=message)
            ctx = self._make_context(task.id, task.user_id, task.request)
            await ctx.emit("task.rejected", message, display=True)
            return {"status": "rejected", "final_summary": message}

        state: Dict[str, Any] = {}
        if task.state_json:
            try:
                state = json.loads(task.state_json)
            except Exception:  # noqa: BLE001
                state = {}
        state.update({
            "task_id": task.id,
            "request": task.request,
            "workspace_id": task.workspace_id or task.id,
            "repo_url": task.repo_url,
            "branch": task.branch,
            "approval_granted": True,
            "status": "running",
            "phase": "test",
            "attempts": state.get("attempts") or 0,
            "max_attempts": state.get("max_attempts") or self.settings.max_attempts,
            "edits": state.get("edits") or [],
        })
        ctx = self._make_context(task.id, task.user_id, task.request, approval_granted=True)
        await ctx.emit("approval.approved", f"Approved by {approver}; resuming task", {"approval_id": approval_id}, display=True)
        try:
            details = await self._apply_approved_edits(ctx, state, (decided or {}).get("payload") or {})
            state["edits"] = (state.get("edits") or []) + details
            await self._rehydrate_context(ctx, state)
            self.repo.set_running(task.id)
            graph = build_continuation_graph(ctx)
            last = await asyncio.wait_for(
                self._consume_graph(graph, state, ctx),
                timeout=self.settings.task_timeout_seconds,
            )
            return last
        except Exception as exc:  # noqa: BLE001
            log.exception("approval resume failed")
            message = f"Could not resume the approved task: {exc}"
            self.repo.update_task(task.id, status="failed", error=message, summary=message)
            await ctx.emit("task.failed", message, display=True)
            return {"status": "failed", "final_summary": message}
        finally:
            await self.bus.close(task.id)

    async def _apply_approved_edits(self, ctx: TaskContext, state: Dict[str, Any], payload: Dict[str, Any]) -> List[dict]:
        raw = payload.get("edits") or []
        grouped: Dict[str, List[LineEdit]] = {}
        for item in raw:
            path = item.get("path")
            if not path:
                continue
            grouped.setdefault(path, []).append(
                LineEdit(
                    path=path,
                    start_line=int(item.get("start_line")),
                    end_line=int(item.get("end_line")),
                    replacement=item.get("replacement") or "",
                    reason=item.get("reason"),
                )
            )
        details: List[dict] = []
        for path, edits in grouped.items():
            original, _ = ctx.workspace.read_text(ctx.workspace_id, path, max_bytes=ctx.settings.max_file_bytes)
            new_content, file_details = apply_pure(original, edits)
            syntax_error = check_syntax(path, new_content)
            if syntax_error:
                raise EditError(f"{path}: {syntax_error}")
            ctx.workspace.write_text(ctx.workspace_id, path, new_content)
            details.extend(file_details)
            await ctx.emit("file.edited", f"Edited {path} after approval", {"path": path, "details": file_details}, display=True)
        return details

    async def _rehydrate_context(self, ctx: TaskContext, state: Dict[str, Any]) -> None:
        paths = list(dict.fromkeys((state.get("edit_paths") or []) + (state.get("plan_files") or [])))
        if not paths:
            paths = ctx.workspace.list_code_files(ctx.workspace_id)[: ctx.settings.max_context_files]
        context_files: Dict[str, str] = {}
        for rel in paths[: ctx.settings.max_context_files]:
            try:
                content, _ = ctx.workspace.read_text(ctx.workspace_id, rel, max_bytes=ctx.settings.max_file_bytes)
                context_files[rel] = truncate(content, ctx.settings.max_prompt_file_chars * 2)
            except Exception:  # noqa: BLE001
                continue
        state["context_files"] = context_files
        state["edit_paths"] = [p for p in context_files if p in paths]
