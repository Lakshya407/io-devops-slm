"""Human approval endpoints."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from api.deps import orchestrator, repository, require_api_key
from api.schemas.openai import ApprovalDecisionRequest

router = APIRouter()


@router.get("/v1/approvals")
async def list_approvals(request: Request, task_id: str | None = None, _auth: None = Depends(require_api_key)):
    return {"approvals": repository(request).list_pending_approvals(task_id)}


@router.post("/v1/approvals/{approval_id}/decide")
async def decide_approval(approval_id: str, body: ApprovalDecisionRequest, request: Request, _auth: None = Depends(require_api_key)):
    if body.decision not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="decision must be 'approve' or 'reject'")
    if not body.approver.strip():
        raise HTTPException(status_code=400, detail="approver is required")
    service = orchestrator(request)
    if body.decision == "approve":
        # Resuming can take minutes; return immediately and stream progress from the task.
        asyncio.create_task(service.resume_after_approval(approval_id=approval_id, decision="approve", approver=body.approver.strip()))
        return {"approval_id": approval_id, "status": "resuming"}
    result = await service.resume_after_approval(approval_id=approval_id, decision="reject", approver=body.approver.strip())
    return {"approval_id": approval_id, "status": result.get("status"), "summary": result.get("final_summary")}
