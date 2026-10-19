"""Durable run submission and cancellation services shared by the API."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agentflow.models import RunStatus, Workflow, WorkflowJob, WorkflowRun, WorkflowVersion
from agentflow.workflows import validate_definition


def now() -> datetime:
    return datetime.now(UTC)


def enqueue_workflow(
    session: Session,
    workflow: Workflow,
    version: WorkflowVersion,
    *,
    idempotency_key: str | None = None,
) -> WorkflowRun:
    """Persist a queued run and its unique queue row in one database commit."""
    if version.workflow_id != workflow.id:
        raise ValueError("Workflow version does not belong to this workflow")
    validate_definition(version.definition)
    if idempotency_key:
        existing = session.scalar(
            select(WorkflowRun).where(
                WorkflowRun.workflow_id == workflow.id,
                WorkflowRun.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing

    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        status=RunStatus.QUEUED,
        created_at=now(),
        idempotency_key=idempotency_key,
    )
    session.add(run)
    try:
        session.flush()
        session.add(WorkflowJob(run_id=run.id, available_at=now()))
        session.commit()
    except IntegrityError:
        session.rollback()
        if idempotency_key:
            existing = session.scalar(
                select(WorkflowRun).where(
                    WorkflowRun.workflow_id == workflow.id,
                    WorkflowRun.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return existing
        raise
    session.refresh(run)
    return run
