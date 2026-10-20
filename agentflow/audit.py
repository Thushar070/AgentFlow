"""Append structured audit events without copying request payloads into the log."""

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentflow.models import AuditEvent, User, utcnow
from agentflow.security import redact


def record_event(
    session: Session,
    event_type: str,
    *,
    actor: User | None = None,
    actor_kind: str | None = None,
    resource_type: str,
    resource_id: int | str | None = None,
    workflow_id: int | None = None,
    run_id: int | None = None,
    outcome: str = "success",
    correlation_id: str = "system",
    details: dict | None = None,
) -> AuditEvent:
    event = AuditEvent(
        event_type=event_type,
        actor_user_id=actor.id if actor else None,
        actor_kind=actor_kind or ("user" if actor else "system"),
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        workflow_id=workflow_id,
        run_id=run_id,
        outcome=outcome,
        correlation_id=correlation_id[:80],
        details=redact(details or {}),
        created_at=utcnow(),
    )
    session.add(event)
    return event


def correlation_id(request: Request) -> str:
    return getattr(request.state, "correlation_id", "unknown")


def audit_response(event: AuditEvent, session: Session) -> dict:
    actor = (
        session.scalar(select(User).where(User.id == event.actor_user_id))
        if event.actor_user_id
        else None
    )
    return {
        "id": event.id,
        "event_type": event.event_type,
        "actor": {"id": actor.id, "username": actor.username, "role": actor.role}
        if actor
        else {"kind": event.actor_kind},
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "workflow_id": event.workflow_id,
        "run_id": event.run_id,
        "outcome": event.outcome,
        "correlation_id": event.correlation_id,
        "details": redact(event.details),
        "created_at": event.created_at,
    }
