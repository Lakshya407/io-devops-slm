"""Task inspection and structured event streaming."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from api.deps import event_bus, orchestrator, repository, require_api_key, resolve_user, workspace_manager
from api.schemas.openai import TaskCreateRequest
from persistence.repository import TERMINAL_STATUSES

router = APIRouter()


def _task_payload(task) -> Dict[str, Any]:
    plan = None
    if task.plan_json:
        try:
            plan = json.loads(task.plan_json)
        except Exception:  # noqa: BLE001
            plan = task.plan_json
    return {
        "id": task.id,
        "status": task.status,
        "phase": task.phase,
        "request": task.request,
        "repo_url": task.repo_url,
        "branch": task.branch,
        "workspace_id": task.workspace_id,
        "model": task.model,
        "plan": plan,
        "test_command": task.test_command,
        "tests_passed": task.tests_passed,
        "attempts": task.attempts,
        "max_attempts": task.max_attempts,
        "diff_stat": task.diff_stat,
        "summary": task.summary,
        "error": task.error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }


@router.post("/v1/tasks")
async def create_task(body: TaskCreateRequest, request: Request, _auth: None = Depends(require_api_key)):
    service = orchestrator(request)
    user_id = resolve_user(request, body.user)
    task_id = service.create_task(
        request=body.request,
        user_id=user_id,
        conversation_id=body.conversation_id,
        repo_url=body.repository,
        repo_ref=body.ref,
    )
    service.start_task(
        task_id=task_id,
        request=body.request,
        user_id=user_id,
        conversation=[{"role": "user", "content": body.request}],
        repo_url=body.repository,
        repo_ref=body.ref,
    )
    return {"task_id": task_id, "status": "started", "events_url": f"/v1/tasks/{task_id}/events"}


@router.get("/v1/tasks")
async def list_tasks(request: Request, limit: int = 50, _auth: None = Depends(require_api_key)):
    return {"tasks": repository(request).list_tasks(limit=max(1, min(limit, 200)))}


@router.get("/v1/tasks/{task_id}")
async def get_task(task_id: str, request: Request, _auth: None = Depends(require_api_key)):
    repo = repository(request)
    task = repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    payload = _task_payload(task)
    payload["summary_record"] = repo.get_summary(task_id)
    payload["pending_approvals"] = repo.list_pending_approvals(task_id)
    payload["tool_calls"] = repo.get_tool_calls(task_id)
    return payload


@router.get("/v1/tasks/{task_id}/events")
async def stream_events(task_id: str, request: Request, since: int = 0, follow: bool = True, _auth: None = Depends(require_api_key)):
    repo = repository(request)
    task = repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    bus = event_bus(request)

    async def generator():
        last_id = max(0, since)
        queue = bus.subscribe(task_id)
        try:
            yield ": connected\n\n"
            idle = 0
            while True:
                events = await asyncio.to_thread(repo.get_events, task_id, last_id, 500)
                for event in events:
                    if event["id"] <= last_id:
                        continue
                    last_id = event["id"]
                    yield f"id: {event['id']}\nevent: agent_event\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
                current = await asyncio.to_thread(repo.get_task, task_id)
                if not follow or (current is not None and current.status in TERMINAL_STATUSES):
                    break
                if current is not None and current.status == "waiting_approval" and not events:
                    # Keep the stream open only briefly; the task will resume after a decision.
                    pass
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=1.0)
                    if item is None:
                        break
                except asyncio.TimeoutError:
                    idle += 1
                    if idle % 15 == 0:
                        yield ": keepalive\n\n"
            yield "event: agent_stream_end\ndata: {}\n\n"
        finally:
            bus.unsubscribe(task_id, queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no", "X-Task-ID": task_id},
    )


@router.get("/v1/tasks/{task_id}/diff")
async def get_diff(task_id: str, request: Request, _auth: None = Depends(require_api_key)):
    repo = repository(request)
    task = repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if not task.workspace_id:
        raise HTTPException(status_code=409, detail="task has no workspace yet")
    manager = workspace_manager(request)
    try:
        diff = manager.diff(task.workspace_id)
        stat = manager.diff_stat(task.workspace_id)
        status = manager.status(task.workspace_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=f"could not read workspace diff: {exc}")
    return {"task_id": task_id, "branch": task.branch, "diff": diff, "stat": stat, "status": status}
