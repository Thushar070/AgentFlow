"""Server-side role and ownership checks for workflow resources."""

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentflow.audit import correlation_id, record_event
from agentflow.models import User, Workflow, WorkflowRun


def _deny(session: Session, user: User, request: Request, resource_type: str, resource_id: int):
    record_event(
        session,
        "authorization.denied",
        actor=user,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome="denied",
        correlation_id=correlation_id(request),
    )
    session.commit()
    raise HTTPException(status_code=404, detail=f"{resource_type.title()} not found.")


def get_owned_workflow(
    session: Session,
    workflow_id: int,
    actor: User,
    request: Request,
    *,
    write: bool = False,
) -> Workflow:
    workflow = session.scalar(
        select(Workflow).where(Workflow.id == workflow_id).options(selectinload_versions())
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    if actor.role == "admin":
        return workflow
    if actor.role == "operator" and workflow.owner_id == actor.id:
        return workflow
    _deny(session, actor, request, "workflow", workflow_id)


def get_owned_run(
    session: Session,
    run_id: int,
    actor: User,
    request: Request,
) -> WorkflowRun:
    run = session.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    if actor.role == "admin":
        return run
    if actor.role == "operator":
        workflow = session.get(Workflow, run.workflow_id)
        if workflow is not None and workflow.owner_id == actor.id:
            return run
    _deny(session, actor, request, "run", run_id)


def selectinload_versions():
    from sqlalchemy.orm import selectinload

    return selectinload(Workflow.versions)
