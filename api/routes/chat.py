"""OpenAI-compatible chat-completions endpoint used by Open WebUI."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from api.deps import event_bus, orchestrator, repository, require_api_key, resolve_user
from api.schemas.openai import ChatCompletionRequest
from persistence.repository import TERMINAL_STATUSES

log = logging.getLogger(__name__)
router = APIRouter()

TERMINAL_EVENTS = {
    "task.completed", "task.failed", "task.incomplete", "task.needs_clarification",
    "task.rejected", "task.timeout", "approval.waiting", "approval.required",
}

EVENT_ICONS = {
    "task.started": "🚀",
    "repo.inspecting": "🔍",
    "repo.ready": "📦",
    "repo.analysed": "🔎",
    "plan.preparing": "🧭",
    "plan.ready": "🧭",
    "code.editing": "✏️",
    "file.edited": "   •",
    "code.edited": "✅",
    "test.running": "🧪",
    "test.passed": "✅",
    "test.failed": "❌",
    "debugging.started": "🩺",
    "debugging.correction_applied": "🔧",
    "changes.verifying": "📊",
    "changes.verified": "📊",
    "approval.required": "⏸️",
    "approval.waiting": "⏸️",
    "approval.approved": "▶️",
}


def _last_user_message(body: ChatCompletionRequest) -> tuple[str, List[Dict[str, str]]]:
    messages = [m for m in body.messages if m.role == "user"]
    if not messages:
        raise HTTPException(status_code=400, detail="at least one user message is required")
    conversation = [
        {"role": m.role, "content": m.text()[:20_000]}
        for m in body.messages[-12:]
        if m.role in {"user", "assistant"} and m.text()
    ]
    return messages[-1].text().strip(), conversation


def _metadata_fields(body: ChatCompletionRequest) -> tuple[str | None, str | None, str | None]:
    metadata = body.metadata or {}
    agent_meta = metadata.get("agent") if isinstance(metadata.get("agent"), dict) else {}
    repo = agent_meta.get("repository") or agent_meta.get("repo") or metadata.get("repository") or metadata.get("repo")
    ref = agent_meta.get("ref") or metadata.get("ref")
    conversation_id = metadata.get("chat_id") or metadata.get("conversation_id") or metadata.get("session_id")
    return (str(repo) if repo else None, str(ref) if ref else None, str(conversation_id) if conversation_id else None)


def _chunk(*, task_id: str, model: str, content: str | None = None, role: str | None = None, finish: str | None = None) -> str:
    delta: Dict[str, Any] = {}
    if role:
        delta["role"] = role
    if content is not None:
        delta["content"] = content
    payload = {
        "id": f"chatcmpl-{task_id}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _format_event(event: dict) -> str | None:
    etype = event.get("type") or ""
    message = event.get("message") or ""
    if etype in {"tool.call", "tool.result"}:
        if etype == "tool.result" and event.get("data", {}).get("status") in {"error", "disabled", "not_configured", "approval_required"}:
            return f"⚠️ {message}\n"
        return None
    if etype in {"task.completed", "task.failed", "task.incomplete", "task.needs_clarification", "task.rejected", "task.timeout"}:
        return f"\n\n{message}\n"
    if etype in {"approval.required", "approval.waiting"}:
        return f"\n⏸️ {message}\n\n"
    icon = EVENT_ICONS.get(etype)
    if icon:
        if etype == "plan.ready":
            return f"{icon} {message}\n\n"
        return f"{icon} {message}\n"
    return None


async def _stream_task(request: Request, task_id: str, model: str, timeout_seconds: int):
    repo = repository(request)
    bus = event_bus(request)
    queue = bus.subscribe(task_id)
    last_id = 0
    started = time.monotonic()
    idle_ticks = 0
    try:
        yield _chunk(task_id=task_id, model=model, role="assistant")
        while True:
            events = await asyncio.to_thread(repo.get_events, task_id, last_id, 200)
            for event in events:
                if event["id"] <= last_id:
                    continue
                last_id = event["id"]
                text = _format_event(event)
                if text:
                    yield _chunk(task_id=task_id, model=model, content=text)
                if event.get("type") in TERMINAL_EVENTS:
                    yield _chunk(task_id=task_id, model=model, finish="stop")
                    yield "data: [DONE]\n\n"
                    return
            if time.monotonic() - started > timeout_seconds + 120:
                yield _chunk(task_id=task_id, model=model, content="\n\n❌ The agent exceeded the response time limit. Check the task status endpoint for details.\n")
                yield _chunk(task_id=task_id, model=model, finish="stop")
                yield "data: [DONE]\n\n"
                return
            try:
                item = await asyncio.wait_for(queue.get(), timeout=1.0)
                if item is None:
                    # Task closed; drain any last events before finishing.
                    events = await asyncio.to_thread(repo.get_events, task_id, last_id, 200)
                    for event in events:
                        if event["id"] <= last_id:
                            continue
                        last_id = event["id"]
                        text = _format_event(event)
                        if text:
                            yield _chunk(task_id=task_id, model=model, content=text)
                    break
            except asyncio.TimeoutError:
                idle_ticks += 1
                if idle_ticks % 15 == 0:
                    yield ": keepalive\n\n"
                task = await asyncio.to_thread(repo.get_task, task_id)
                if task is not None and task.status in TERMINAL_STATUSES:
                    # No terminal event was persisted; surface the stored summary.
                    summary = task.summary or task.error or f"Task finished with status {task.status}."
                    yield _chunk(task_id=task_id, model=model, content=f"\n\n{summary}\n")
                    break
        yield _chunk(task_id=task_id, model=model, finish="stop")
        yield "data: [DONE]\n\n"
    finally:
        bus.unsubscribe(task_id, queue)


@router.post("/v1/chat/completions")
async def chat_completions(body: ChatCompletionRequest, request: Request, _auth: None = Depends(require_api_key)):
    settings = request.app.state.settings
    orchestrator_service = orchestrator(request)
    request_text, conversation = _last_user_message(body)
    repo_url, repo_ref, conversation_id = _metadata_fields(body)
    user_id = resolve_user(request, body.user)
    task_id = orchestrator_service.create_task(
        request=request_text,
        user_id=user_id,
        conversation_id=conversation_id or (f"{user_id}:{uuid.uuid4().hex[:8]}" if user_id else None),
        repo_url=repo_url,
        repo_ref=repo_ref,
    )
    if body.stream:
        orchestrator_service.start_task(
            task_id=task_id,
            request=request_text,
            user_id=user_id,
            conversation=conversation,
            repo_url=repo_url,
            repo_ref=repo_ref,
        )
        return StreamingResponse(
            _stream_task(request, task_id, settings.agent_public_model_id, settings.task_timeout_seconds),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no", "X-Task-ID": task_id},
        )

    result = await asyncio.wait_for(
        orchestrator_service.run_task(
            task_id=task_id,
            request=request_text,
            user_id=user_id,
            conversation=conversation,
            repo_url=repo_url,
            repo_ref=repo_ref,
        ),
        timeout=settings.task_timeout_seconds + 30,
    )
    task = repository(request).get_task(task_id)
    summary = (task.summary if task else None) or result.get("final_summary") or result.get("error") or "The task finished without a summary."
    payload = {
        "id": f"chatcmpl-{task_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": settings.agent_public_model_id,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": summary}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "task_id": task_id,
        "status": (task.status if task else result.get("status")),
    }
    return JSONResponse(payload, headers={"X-Task-ID": task_id})
