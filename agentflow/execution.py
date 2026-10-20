"""Durable run submission and cancellation services shared by the API."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agentflow.models import (
    AuditEvent,
    RunStatus,
    Workflow,
    WorkflowJob,
    WorkflowRun,
    WorkflowVersion,
    utcnow,
)
from agentflow.workflows import validate_definition, validate_run_input


def now() -> datetime:
    return datetime.now(UTC)


def enqueue_workflow(
    session: Session,
    workflow: Workflow,
    version: WorkflowVersion,
    *,
    idempotency_key: str | None = None,
    run_input: dict | None = None,
    created_by_id: int | None = None,
    correlation_id: str = "system",
) -> WorkflowRun:
    """Persist a queued run and its unique queue row in one database commit."""
    if version.workflow_id != workflow.id:
        raise ValueError("Workflow version does not belong to this workflow")
    definition = validate_definition(version.definition)
    validated_input = validate_run_input(definition, run_input)
    if idempotency_key:
        existing = session.scalar(
            select(WorkflowRun).where(
                WorkflowRun.workflow_id == workflow.id,
                WorkflowRun.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            session.add(
                AuditEvent(
                    event_type="run.idempotency_replayed",
                    actor_user_id=created_by_id,
                    actor_kind="user" if created_by_id is not None else "system",
                    resource_type="run",
                    resource_id=str(existing.id),
                    workflow_id=workflow.id,
                    run_id=existing.id,
                    outcome="deduplicated",
                    correlation_id=correlation_id[:80],
                    details={},
                    created_at=utcnow(),
                )
            )
            session.commit()
            return existing

    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        status=RunStatus.QUEUED,
        input=validated_input,
        created_at=now(),
        idempotency_key=idempotency_key,
        created_by_id=created_by_id,
    )
    session.add(run)
    try:
        session.flush()
        session.add(WorkflowJob(run_id=run.id, available_at=now()))
        session.add(
            AuditEvent(
                event_type="run.created",
                actor_user_id=created_by_id,
                actor_kind="user" if created_by_id is not None else "system",
                resource_type="run",
                resource_id=str(run.id),
                workflow_id=workflow.id,
                run_id=run.id,
                outcome="queued",
                correlation_id=correlation_id[:80],
                details={"workflow_version_id": version.id},
                created_at=utcnow(),
            )
        )
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
                session.add(
                    AuditEvent(
                        event_type="run.idempotency_replayed",
                        actor_user_id=created_by_id,
                        actor_kind="user" if created_by_id is not None else "system",
                        resource_type="run",
                        resource_id=str(existing.id),
                        workflow_id=workflow.id,
                        run_id=existing.id,
                        outcome="deduplicated",
                        correlation_id=correlation_id[:80],
                        details={},
                        created_at=utcnow(),
                    )
                )
                session.commit()
                return existing
        raise
    session.refresh(run)
    return run
