"""Persistence operations used by the API and the agent workflow."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import select

from persistence.db import session_scope
from persistence.models import (
    AgentApproval,
    AgentExecutionTrace,
    AgentTask,
    AgentTaskEvent,
    AgentTaskSummary,
    AgentToolCall,
    AgentWorkspace,
    utcnow,
)
from telemetry.sanitize import sanitize_obj, sanitize_text, truncate

log = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "failed", "incomplete", "needs_clarification", "rejected", "cancelled", "timeout"}


def _dumps(value: Any) -> str:
    return json.dumps(sanitize_obj(value), ensure_ascii=False, default=str)


def _loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:  # noqa: BLE001
        return value


class TaskRepository:
    def create_task(
        self,
        *,
        task_id: str,
        request: str,
        user_id: str | None = None,
        conversation_id: str | None = None,
        repo_url: str | None = None,
        model: str | None = None,
        max_attempts: int = 3,
    ) -> AgentTask:
        with session_scope() as session:
            task = AgentTask(
                id=task_id,
                request=sanitize_text(request, 20_000),
                user_id=user_id[:256] if user_id else None,
                conversation_id=conversation_id,
                repo_url=repo_url,
                model=model,
                max_attempts=max_attempts,
                status="queued",
                phase="created",
            )
            session.add(task)
            session.flush()
            return task

    def get_task(self, task_id: str) -> AgentTask | None:
        with session_scope() as session:
            return session.get(AgentTask, task_id)

    def update_task(self, task_id: str, **fields: Any) -> None:
        allowed = {
            "status", "phase", "workspace_id", "branch", "repo_url", "repo_ref",
            "plan_json", "summary", "error", "test_command", "tests_passed",
            "attempts", "diff_stat", "state_json", "started_at", "finished_at",
        }
        data = {k: v for k, v in fields.items() if k in allowed}
        if not data:
            return
        data["updated_at"] = utcnow()
        with session_scope() as session:
            task = session.get(AgentTask, task_id)
            if task is None:
                return
            for key, value in data.items():
                setattr(task, key, value)

    def set_running(self, task_id: str) -> None:
        self.update_task(task_id, status="running", started_at=utcnow(), phase="understand")

    def mark_terminal(self, task_id: str, status: str, *, summary: str | None = None, error: str | None = None) -> None:
        fields: Dict[str, Any] = {"status": status, "finished_at": utcnow()}
        if summary is not None:
            fields["summary"] = summary
        if error is not None:
            fields["error"] = error
        self.update_task(task_id, **fields)

    # --- events ---------------------------------------------------------
    def append_event(
        self,
        *,
        task_id: str,
        event_type: str,
        message: str,
        data: Any | None = None,
        display: bool = True,
    ) -> dict:
        with session_scope() as session:
            event = AgentTaskEvent(
                task_id=task_id,
                type=event_type,
                message=sanitize_text(message, 4_000),
                data_json=_dumps(data) if data is not None else None,
                display=display,
            )
            session.add(event)
            session.flush()
            return {
                "id": event.id,
                "task_id": task_id,
                "type": event_type,
                "message": event.message,
                "data": _loads(event.data_json),
                "display": display,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }

    def get_events(self, task_id: str, since_id: int = 0, limit: int = 500) -> List[dict]:
        with session_scope() as session:
            rows = session.scalars(
                select(AgentTaskEvent)
                .where(AgentTaskEvent.task_id == task_id, AgentTaskEvent.id > since_id)
                .order_by(AgentTaskEvent.id.asc())
                .limit(limit)
            ).all()
            return [
                {
                    "id": r.id,
                    "task_id": r.task_id,
                    "type": r.type,
                    "message": r.message,
                    "data": _loads(r.data_json),
                    "display": r.display,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]

    # --- tool calls -----------------------------------------------------
    def add_tool_call(
        self,
        *,
        task_id: str,
        tool_name: str,
        arguments: Any,
        result: Any,
        status: str,
        duration_ms: int,
    ) -> None:
        with session_scope() as session:
            session.add(
                AgentToolCall(
                    task_id=task_id,
                    tool_name=tool_name,
                    arguments_json=_dumps(arguments),
                    result_json=_dumps(result),
                    status=status,
                    duration_ms=duration_ms,
                )
            )

    def get_tool_calls(self, task_id: str) -> List[dict]:
        with session_scope() as session:
            rows = session.scalars(
                select(AgentToolCall).where(AgentToolCall.task_id == task_id).order_by(AgentToolCall.id.asc())
            ).all()
            return [
                {
                    "id": r.id,
                    "tool": r.tool_name,
                    "arguments": _loads(r.arguments_json),
                    "result": _loads(r.result_json),
                    "status": r.status,
                    "duration_ms": r.duration_ms,
                }
                for r in rows
            ]

    # --- approvals ------------------------------------------------------
    def create_approval(self, *, task_id: str, action: str, payload: Any, reason: str) -> dict:
        approval_id = uuid.uuid4().hex
        with session_scope() as session:
            row = AgentApproval(
                id=approval_id,
                task_id=task_id,
                action=action,
                payload_json=_dumps(payload),
                reason=sanitize_text(reason, 2_000),
                status="pending",
            )
            session.add(row)
            return {
                "id": approval_id,
                "task_id": task_id,
                "action": action,
                "payload": _loads(row.payload_json),
                "reason": row.reason,
                "status": "pending",
            }

    def get_approval(self, approval_id: str) -> AgentApproval | None:
        with session_scope() as session:
            return session.get(AgentApproval, approval_id)

    def list_pending_approvals(self, task_id: str | None = None) -> List[dict]:
        with session_scope() as session:
            q = select(AgentApproval).where(AgentApproval.status == "pending")
            if task_id:
                q = q.where(AgentApproval.task_id == task_id)
            rows = session.scalars(q.order_by(AgentApproval.requested_at.asc())).all()
            return [
                {
                    "id": r.id,
                    "task_id": r.task_id,
                    "action": r.action,
                    "payload": _loads(r.payload_json),
                    "reason": r.reason,
                    "status": r.status,
                    "requested_at": r.requested_at.isoformat() if r.requested_at else None,
                }
                for r in rows
            ]

    def decide_approval(self, approval_id: str, *, status: str, decided_by: str) -> dict | None:
        with session_scope() as session:
            row = session.get(AgentApproval, approval_id)
            if row is None:
                return None
            row.status = status
            row.decided_at = utcnow()
            row.decided_by = decided_by[:256]
            session.flush()
            return {
                "id": row.id,
                "task_id": row.task_id,
                "action": row.action,
                "payload": _loads(row.payload_json),
                "reason": row.reason,
                "status": row.status,
            }

    # --- summaries and traces ------------------------------------------
    def save_summary(self, *, task_id: str, summary: str, diff: str | None, tests_passed: bool | None, files_changed: int) -> None:
        with session_scope() as session:
            row = session.get(AgentTaskSummary, task_id)
            if row is None:
                row = AgentTaskSummary(task_id=task_id, summary="")
                session.add(row)
            row.summary = sanitize_text(summary, 20_000)
            row.diff = truncate(sanitize_text(diff or ""), 40_000)
            row.tests_passed = tests_passed
            row.files_changed = files_changed

    def get_summary(self, task_id: str) -> dict | None:
        with session_scope() as session:
            row = session.get(AgentTaskSummary, task_id)
            if row is None:
                return None
            return {
                "task_id": row.task_id,
                "summary": row.summary,
                "diff": row.diff,
                "tests_passed": row.tests_passed,
                "files_changed": row.files_changed,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }

    def save_trace(self, *, task_id: str, trace: Any) -> int:
        with session_scope() as session:
            row = AgentExecutionTrace(task_id=task_id, trace_json=_dumps(trace), approved_for_training=False)
            session.add(row)
            session.flush()
            return row.id

    def set_trace_training_approval(self, trace_id: int, approved: bool, feedback: str | None = None) -> bool:
        with session_scope() as session:
            row = session.get(AgentExecutionTrace, trace_id)
            if row is None:
                return False
            row.approved_for_training = approved
            if feedback is not None:
                row.human_feedback = sanitize_text(feedback, 4_000)
            return True

    def list_approved_traces(self) -> List[dict]:
        with session_scope() as session:
            rows = session.scalars(
                select(AgentExecutionTrace)
                .where(AgentExecutionTrace.approved_for_training.is_(True))
                .order_by(AgentExecutionTrace.id.asc())
            ).all()
            return [
                {
                    "id": r.id,
                    "task_id": r.task_id,
                    "trace": _loads(r.trace_json),
                    "human_feedback": r.human_feedback,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]

    def create_workspace(self, *, workspace_id: str, task_id: str, repo_url: str, branch: str, path: str) -> None:
        """Record a workspace idempotently.

        A task can re-enter the ``understand`` node (for example after an
        approval resume or a retry), so this must not fail with a duplicate
        primary key. It updates the existing row instead.
        """
        with session_scope() as session:
            row = session.get(AgentWorkspace, workspace_id)
            if row is None:
                row = AgentWorkspace(id=workspace_id, status="active")
                session.add(row)
            row.task_id = task_id
            row.repo_url = sanitize_text(repo_url, 2_000)
            row.branch = branch
            row.path = path
            row.status = "active"

    def list_tasks(self, limit: int = 50) -> List[dict]:
        with session_scope() as session:
            rows = session.scalars(select(AgentTask).order_by(AgentTask.created_at.desc()).limit(limit)).all()
            return [
                {
                    "id": t.id,
                    "status": t.status,
                    "phase": t.phase,
                    "request": t.request,
                    "repo_url": t.repo_url,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                    "finished_at": t.finished_at.isoformat() if t.finished_at else None,
                }
                for t in rows
            ]
