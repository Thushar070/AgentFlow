"""AgentFlow HTTP API."""

import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from agentflow.approval_routes import router as approval_router
from agentflow.audit import audit_response, correlation_id, record_event
from agentflow.auth import require_roles, require_user
from agentflow.auth_routes import admin_router
from agentflow.auth_routes import router as auth_router
from agentflow.authorization import get_owned_run, get_owned_workflow
from agentflow.config import get_settings
from agentflow.contracts import RunResponse, WorkflowResponse, WorkflowVersionResponse
from agentflow.database import engine, get_session
from agentflow.execution import enqueue_workflow
from agentflow.models import (
    AuditEvent,
    RunStatus,
    StepRun,
    User,
    Workflow,
    WorkflowRun,
    WorkflowVersion,
    transition_status,
    utcnow,
)
from agentflow.registry import ACTION_REGISTRY, action_catalog
from agentflow.security import RequestLimitMiddleware, redact, redact_text
from agentflow.workflows import WorkflowDefinition, validate_definition


def _migration_head() -> str | None:
    script = ScriptDirectory.from_config(_alembic_config())
    return script.get_current_head()


def _alembic_config():
    from pathlib import Path

    from alembic.config import Config

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    return config


@asynccontextmanager
async def lifespan(_: FastAPI):
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    expected = _migration_head()
    if current != expected:
        raise RuntimeError(
            f"Database schema revision is {current!r}, expected {expected!r}; "
            "run `alembic upgrade head` before starting AgentFlow."
        )
    unknown_approval_actions = set(get_settings().approval_required_actions) - set(ACTION_REGISTRY)
    if unknown_approval_actions:
        raise RuntimeError(
            "APPROVAL_REQUIRED_ACTIONS contains unregistered actions: "
            + ", ".join(sorted(unknown_approval_actions))
        )
    yield


app = FastAPI(title="AgentFlow API", version="0.2.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(approval_router)
app.add_middleware(RequestLimitMiddleware, max_bytes=get_settings().max_request_body_bytes)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "OPTIONS", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Idempotency-Key", "X-CSRF-Token"],
)
logger = logging.getLogger("agentflow.api")


def safe_errors(errors):
    return [
        {
            key: redact_text(str(error[key])) if key == "msg" else error[key]
            for key in ("loc", "msg", "type")
            if key in error
        }
        for error in errors
    ]


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": safe_errors(exc.errors())})


@app.exception_handler(ValidationError)
async def validation_error_handler(_: Request, exc: ValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "detail": safe_errors(
                exc.errors(include_url=False, include_context=False, include_input=False)
            )
        },
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, _: Exception):
    request_id = correlation_id(request)
    logger.error("request_failed correlation_id=%s", request_id)
    return JSONResponse(
        status_code=500,
        content={"detail": "Request failed.", "correlation_id": request_id},
    )


@app.get("/health")
def health(session: Session = Depends(get_session)):
    try:
        session.execute(select(1))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return {"status": "ok", "service": "agentflow-api"}


@app.get("/api/actions")
def list_actions():
    return action_catalog()


def serialize_workflow(workflow: Workflow) -> dict:
    version = workflow.current_version
    return {
        "id": workflow.id,
        "name": redact_text(workflow.name),
        "description": redact_text(workflow.description) if workflow.description else None,
        "owner_id": workflow.owner_id,
        "created_at": workflow.created_at,
        "updated_at": workflow.updated_at,
        "current_version_number": version.version_number,
        "definition": redact(version.definition),
    }


def serialize_run(run: WorkflowRun) -> dict:
    total_steps = len(run.workflow_version.definition.get("steps", []))
    succeeded_steps = sum(step.status == "succeeded" for step in run.steps)
    failed_steps = sum(step.status == "failed" for step in run.steps)
    return {
        "id": run.id,
        "workflow_id": run.workflow_id,
        "workflow_version_id": run.workflow_version_id,
        "workflow_version_number": run.workflow_version.version_number,
        "status": run.status,
        "input": redact(run.input),
        "output": redact(run.output),
        "error": redact_text(run.error) if run.error else None,
        "error_category": run.error_category,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "cancel_requested_at": run.cancel_requested_at,
        "summary": {
            "total_steps": total_steps,
            "succeeded_steps": succeeded_steps,
            "failed_steps": failed_steps,
            "not_started_steps": max(
                total_steps - sum(step.status != "queued" for step in run.steps), 0
            ),
        },
        "steps": [
            {
                "id": step.step_id,
                "action": step.action,
                "status": step.status,
                "inputs": redact(step.inputs),
                "output": redact(step.output),
                "error": redact_text(step.error) if step.error else None,
                "error_category": step.error_category,
                "started_at": step.started_at,
                "finished_at": step.finished_at,
                "attempts": [
                    {
                        "attempt_number": attempt.attempt_number,
                        "status": attempt.status,
                        "output": redact(attempt.output),
                        "error": redact_text(attempt.error) if attempt.error else None,
                        "error_category": attempt.error_category,
                        "started_at": attempt.started_at,
                        "finished_at": attempt.finished_at,
                    }
                    for attempt in step.attempts
                ],
            }
            for step in run.steps
        ],
    }


def validated_definition(payload: WorkflowDefinition) -> WorkflowDefinition:
    try:
        return validate_definition(payload.model_dump(mode="json"))
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=redact_text(str(exc))) from exc


def get_workflow_or_404(session: Session, workflow_id: int) -> Workflow:
    workflow = session.scalar(
        select(Workflow).where(Workflow.id == workflow_id).options(selectinload(Workflow.versions))
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


def persist_workflow_version(
    session: Session,
    workflow: Workflow | None,
    definition: WorkflowDefinition,
    *,
    actor: User,
    request: Request,
) -> Workflow:
    data = definition.model_dump(mode="json", exclude_none=True)
    if workflow is None:
        workflow = Workflow(
            name=definition.name, description=definition.description, owner_id=actor.id
        )
        session.add(workflow)
        session.flush()
        next_version = 1
    else:
        next_version = max(version.version_number for version in workflow.versions) + 1
        workflow.name = definition.name
        workflow.description = definition.description
        from agentflow.models import utcnow

        workflow.updated_at = utcnow()
    workflow.versions.append(WorkflowVersion(version_number=next_version, definition=data))
    try:
        record_event(
            session,
            "workflow.created" if next_version == 1 else "workflow.updated",
            actor=actor,
            resource_type="workflow",
            resource_id=workflow.id,
            workflow_id=workflow.id,
            outcome="success",
            correlation_id=correlation_id(request),
            details={"version_number": next_version},
        )
        session.commit()
        session.refresh(workflow)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Workflow version conflict") from exc
    return workflow


@app.get("/api/workflows", response_model=list[WorkflowResponse])
def list_workflows(session: Session = Depends(get_session), actor: User = Depends(require_user)):
    statement = (
        select(Workflow).options(selectinload(Workflow.versions)).order_by(Workflow.id.desc())
    )
    if actor.role != "admin":
        if actor.role != "operator":
            return []
        statement = statement.where(Workflow.owner_id == actor.id)
    workflows = session.scalars(statement).all()
    return [serialize_workflow(item) for item in workflows]


@app.post("/api/workflows", status_code=201, response_model=WorkflowResponse)
def create_workflow(
    payload: WorkflowDefinition,
    request: Request,
    session: Session = Depends(get_session),
    actor: User = Depends(require_roles("admin", "operator")),
):
    definition = validated_definition(payload)
    workflow = persist_workflow_version(session, None, definition, actor=actor, request=request)
    return serialize_workflow(workflow)


@app.get("/api/workflows/{workflow_id}", response_model=WorkflowResponse)
def get_workflow(
    workflow_id: int,
    request: Request,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
):
    return serialize_workflow(get_owned_workflow(session, workflow_id, actor, request))


@app.put("/api/workflows/{workflow_id}", response_model=WorkflowResponse)
def update_workflow(
    workflow_id: int,
    payload: WorkflowDefinition,
    request: Request,
    session: Session = Depends(get_session),
    actor: User = Depends(require_roles("admin", "operator")),
):
    workflow = get_owned_workflow(session, workflow_id, actor, request, write=True)
    definition = validated_definition(payload)
    return serialize_workflow(
        persist_workflow_version(session, workflow, definition, actor=actor, request=request)
    )


@app.get("/api/workflows/{workflow_id}/versions", response_model=list[WorkflowVersionResponse])
def list_workflow_versions(
    workflow_id: int,
    request: Request,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
):
    get_owned_workflow(session, workflow_id, actor, request)
    versions = session.scalars(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == workflow_id)
        .order_by(WorkflowVersion.version_number.desc())
    ).all()
    return [
        {
            "id": item.id,
            "workflow_id": item.workflow_id,
            "version_number": item.version_number,
            "definition": redact(item.definition),
            "created_at": item.created_at,
        }
        for item in versions
    ]


def start_run_for_version(
    workflow_id: int,
    version_number: int | None,
    session: Session,
    idempotency_key: str | None,
    actor: User,
    request: Request,
) -> dict:
    workflow = get_owned_workflow(session, workflow_id, actor, request, write=True)
    if idempotency_key:
        existing = session.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.workflow_id == workflow_id,
                WorkflowRun.idempotency_key == idempotency_key,
            )
            .options(
                selectinload(WorkflowRun.steps).selectinload(StepRun.attempts),
                selectinload(WorkflowRun.workflow_version),
            )
        )
        if existing is not None:
            record_event(
                session,
                "run.idempotency_replayed",
                actor=actor,
                resource_type="run",
                resource_id=existing.id,
                workflow_id=workflow_id,
                run_id=existing.id,
                outcome="deduplicated",
                correlation_id=correlation_id(request),
            )
            session.commit()
            return serialize_run(existing)
    recent_runs = session.scalar(
        select(func.count(WorkflowRun.id)).where(
            WorkflowRun.created_by_id == actor.id,
            WorkflowRun.created_at >= utcnow() - timedelta(hours=1),
        )
    )
    if recent_runs >= get_settings().max_runs_per_hour:
        record_event(
            session,
            "run.creation_limited",
            actor=actor,
            resource_type="workflow",
            resource_id=workflow_id,
            workflow_id=workflow_id,
            outcome="rate_limited",
            correlation_id=correlation_id(request),
        )
        session.commit()
        raise HTTPException(status_code=429, detail="Run creation limit reached.")
    if version_number is None:
        version = workflow.current_version
    else:
        version = next(
            (item for item in workflow.versions if item.version_number == version_number), None
        )
        if version is None:
            raise HTTPException(status_code=404, detail="Workflow version not found")
    try:
        run = enqueue_workflow(
            session,
            workflow,
            version,
            idempotency_key=idempotency_key,
            created_by_id=actor.id,
            correlation_id=correlation_id(request),
        )
    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(
            status_code=503,
            detail="Run was not accepted because the durable queue is unavailable.",
        ) from exc
    return serialize_run(run)


@app.post("/api/workflows/{workflow_id}/runs", status_code=202, response_model=RunResponse)
def start_run(
    workflow_id: int,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
    session: Session = Depends(get_session),
    actor: User = Depends(require_roles("admin", "operator")),
):
    if idempotency_key is not None and (
        not idempotency_key or idempotency_key.strip() != idempotency_key
    ):
        raise HTTPException(status_code=422, detail="Idempotency-Key must be non-empty and trimmed")
    return start_run_for_version(workflow_id, None, session, idempotency_key, actor, request)


@app.post(
    "/api/workflows/{workflow_id}/versions/{version_number}/runs",
    status_code=202,
    response_model=RunResponse,
)
def start_version_run(
    workflow_id: int,
    version_number: int,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
    session: Session = Depends(get_session),
    actor: User = Depends(require_roles("admin", "operator")),
):
    if idempotency_key is not None and (
        not idempotency_key or idempotency_key.strip() != idempotency_key
    ):
        raise HTTPException(status_code=422, detail="Idempotency-Key must be non-empty and trimmed")
    return start_run_for_version(
        workflow_id, version_number, session, idempotency_key, actor, request
    )


@app.post("/api/runs/{run_id}/cancel", response_model=RunResponse)
def cancel_run(
    run_id: int,
    request: Request,
    session: Session = Depends(get_session),
    actor: User = Depends(require_roles("admin", "operator")),
):
    get_owned_run(session, run_id, actor, request)
    run = session.scalar(
        select(WorkflowRun)
        .where(WorkflowRun.id == run_id)
        .options(
            selectinload(WorkflowRun.steps).selectinload(StepRun.attempts),
            selectinload(WorkflowRun.workflow_version),
            selectinload(WorkflowRun.job),
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
        return serialize_run(run)
    timestamp = utcnow()
    if run.status in {
        RunStatus.QUEUED,
        RunStatus.RETRYING,
        RunStatus.AWAITING_APPROVAL,
    }:
        run.status = transition_status(run.status, RunStatus.CANCELLED, entity="run")
        run.error = "Run cancelled before the worker started it."
        run.error_category = "cancelled"
        run.finished_at = timestamp
        if run.job:
            run.job.status = "cancelled"
            run.job.locked_by = None
            run.job.lease_expires_at = None
            run.job.updated_at = timestamp
    elif run.status == RunStatus.RUNNING:
        run.status = transition_status(run.status, RunStatus.CANCEL_REQUESTED, entity="run")
        run.cancel_requested_at = timestamp
    record_event(
        session,
        "run.cancelled" if run.status == RunStatus.CANCELLED else "run.cancel_requested",
        actor=actor,
        resource_type="run",
        resource_id=run.id,
        workflow_id=run.workflow_id,
        run_id=run.id,
        outcome=run.status,
        correlation_id=correlation_id(request),
    )
    session.commit()
    session.refresh(run)
    return serialize_run(run)


@app.get("/api/runs/{run_id}", response_model=RunResponse)
def get_run(
    run_id: int,
    request: Request,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
):
    get_owned_run(session, run_id, actor, request)
    run = session.scalar(
        select(WorkflowRun)
        .where(WorkflowRun.id == run_id)
        .options(
            selectinload(WorkflowRun.steps).selectinload(StepRun.attempts),
            selectinload(WorkflowRun.workflow_version),
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return serialize_run(run)


@app.get("/api/runs", response_model=list[RunResponse])
def list_runs(
    limit: int = Query(default=20, ge=1, le=100),
    actor: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    statement = select(WorkflowRun)
    if actor.role == "operator":
        statement = statement.join(Workflow).where(Workflow.owner_id == actor.id)
    elif actor.role != "admin":
        return []
    runs = session.scalars(
        statement.options(
            selectinload(WorkflowRun.steps).selectinload(StepRun.attempts),
            selectinload(WorkflowRun.workflow_version),
        )
        .order_by(WorkflowRun.id.desc())
        .limit(limit)
    ).all()
    return [serialize_run(run) for run in runs]


@app.get("/api/audit")
def list_audit_events(
    event_type: str | None = Query(default=None, max_length=80),
    run_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
    actor: User = Depends(require_roles("admin")),
    session: Session = Depends(get_session),
):
    statement = select(AuditEvent)
    if event_type:
        statement = statement.where(AuditEvent.event_type == event_type)
    if run_id:
        statement = statement.where(AuditEvent.run_id == run_id)
    events = session.scalars(
        statement.order_by(AuditEvent.id.desc()).offset(offset).limit(limit)
    ).all()
    return [audit_response(event, session) for event in events]
