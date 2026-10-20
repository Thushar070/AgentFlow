"""Approval inbox and decision endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agentflow.audit import correlation_id, record_event
from agentflow.auth import require_user
from agentflow.database import get_session
from agentflow.models import (
    ApprovalRequest,
    RunStatus,
    StepRun,
    User,
    WorkflowJob,
    WorkflowRun,
    utcnow,
)
from agentflow.security import redact, redact_text

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approved", "rejected"]
    reason: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=1000)] = None


def _approval_response(request: ApprovalRequest, session: Session) -> dict:
    run = session.get(WorkflowRun, request.run_id)
    step = session.get(StepRun, request.step_run_id)
    return {
        "id": request.id,
        "run_id": request.run_id,
        "workflow_id": run.workflow_id if run else None,
        "workflow_version_id": request.workflow_version_id,
        "step_id": step.step_id if step else None,
        "action": request.action,
        "inputs": redact(step.inputs if step else {}),
        "status": request.status,
        "requester_id": request.requester_id,
        "decided_by_id": request.decided_by_id,
        "reason": redact_text(request.reason) if request.reason else None,
        "created_at": request.created_at,
        "decided_at": request.decided_at,
        "expires_at": request.expires_at,
    }


@router.get("")
def list_approvals(
    status: str | None = Query(default=None, pattern="^(pending|approved|rejected|stale)$"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
    actor: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    statement = select(ApprovalRequest).order_by(ApprovalRequest.id.desc())
    if status:
        statement = statement.where(ApprovalRequest.status == status)
    if actor.role == "operator":
        statement = statement.where(ApprovalRequest.requester_id == actor.id)
    elif actor.role == "approver":
        statement = statement.where(
            ApprovalRequest.requester_id != actor.id,
            ApprovalRequest.status == "pending",
        )
    requests = session.scalars(statement.offset(offset).limit(limit)).all()
    return [_approval_response(item, session) for item in requests]


@router.post("/{approval_id}/decision")
def decide_approval(
    approval_id: int,
    payload: ApprovalDecision,
    request: Request,
    actor: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    if actor.role not in {"admin", "approver"}:
        record_event(
            session,
            "authorization.denied",
            actor=actor,
            resource_type="approval",
            resource_id=approval_id,
            outcome="denied",
            correlation_id=correlation_id(request),
        )
        session.commit()
        raise HTTPException(status_code=403, detail="Approver permission required.")
    approval = session.get(ApprovalRequest, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval request not found.")
    if approval.requester_id == actor.id:
        record_event(
            session,
            "approval.self_decision_denied",
            actor=actor,
            resource_type="approval",
            resource_id=approval.id,
            run_id=approval.run_id,
            outcome="denied",
            correlation_id=correlation_id(request),
        )
        session.commit()
        raise HTTPException(status_code=403, detail="Requesters cannot approve their own runs.")
    if approval.status != "pending":
        raise HTTPException(status_code=409, detail="Approval request is no longer pending.")
    run = session.get(WorkflowRun, approval.run_id)
    step = session.get(StepRun, approval.step_run_id)
    job = session.scalar(select(WorkflowJob).where(WorkflowJob.run_id == approval.run_id))
    from agentflow.security import canonical_hash

    stale = (
        run is None
        or step is None
        or job is None
        or run.status != RunStatus.AWAITING_APPROVAL
        or job.status != "awaiting_approval"
        or approval.expires_at <= utcnow()
        or run.workflow_version_id != approval.workflow_version_id
        or step.action != approval.action
        or canonical_hash(step.inputs) != approval.inputs_hash
    )
    if stale:
        approval.status = "stale"
        approval.decided_at = utcnow()
        record_event(
            session,
            "approval.stale",
            actor=actor,
            resource_type="approval",
            resource_id=approval.id,
            run_id=approval.run_id,
            outcome="stale",
            correlation_id=correlation_id(request),
        )
        if run and job and run.status == RunStatus.AWAITING_APPROVAL:
            run.status = RunStatus.QUEUED
            job.status = "queued"
            job.available_at = utcnow()
            job.locked_by = None
            job.lease_expires_at = None
        session.commit()
        raise HTTPException(status_code=409, detail="Approval request is stale or expired.")

    result = session.execute(
        update(ApprovalRequest)
        .where(ApprovalRequest.id == approval.id, ApprovalRequest.status == "pending")
        .values(
            status=payload.decision,
            decided_by_id=actor.id,
            decided_at=utcnow(),
            reason=redact_text(payload.reason) if payload.reason else None,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=409, detail="Approval request was already decided.")
    if payload.decision == "approved":
        run.status = RunStatus.QUEUED
        job.status = "queued"
        job.available_at = utcnow()
        job.locked_by = None
        job.lease_expires_at = None
    else:
        run.status = RunStatus.FAILED
        run.error = "A required approval was rejected."
        run.error_category = "approval_rejected"
        run.finished_at = utcnow()
        step.status = "failed"
        step.error = "A required approval was rejected."
        step.error_category = "approval_rejected"
        step.finished_at = utcnow()
        job.status = "failed"
        job.locked_by = None
        job.lease_expires_at = None
    record_event(
        session,
        "approval.decision",
        actor=actor,
        resource_type="approval",
        resource_id=approval.id,
        workflow_id=run.workflow_id,
        run_id=run.id,
        outcome=payload.decision,
        correlation_id=correlation_id(request),
        details={"reason": redact_text(payload.reason) if payload.reason else None},
    )
    session.commit()
    return _approval_response(approval, session)
