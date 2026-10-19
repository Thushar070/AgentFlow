"""AgentFlow HTTP API."""

from contextlib import asynccontextmanager

from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from agentflow.config import get_settings
from agentflow.contracts import RunResponse, WorkflowResponse, WorkflowVersionResponse
from agentflow.database import engine, get_session
from agentflow.execution import execute_workflow
from agentflow.models import Workflow, WorkflowRun, WorkflowVersion
from agentflow.registry import action_catalog
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
    yield


app = FastAPI(title="AgentFlow API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Content-Type"],
)


def safe_errors(errors):
    return [{key: error[key] for key in ("loc", "msg", "type") if key in error} for error in errors]


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
        "name": workflow.name,
        "description": workflow.description,
        "created_at": workflow.created_at,
        "updated_at": workflow.updated_at,
        "current_version_number": version.version_number,
        "definition": version.definition,
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
        "input": run.input,
        "output": run.output,
        "error": run.error,
        "error_category": run.error_category,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "summary": {
            "total_steps": total_steps,
            "succeeded_steps": succeeded_steps,
            "failed_steps": failed_steps,
            "not_started_steps": max(total_steps - len(run.steps), 0),
        },
        "steps": [
            {
                "id": step.step_id,
                "action": step.action,
                "status": step.status,
                "inputs": step.inputs,
                "output": step.output,
                "error": step.error,
                "error_category": step.error_category,
                "started_at": step.started_at,
                "finished_at": step.finished_at,
            }
            for step in run.steps
        ],
    }


def validated_definition(payload: WorkflowDefinition) -> WorkflowDefinition:
    try:
        return validate_definition(payload.model_dump(mode="json"))
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def get_workflow_or_404(session: Session, workflow_id: int) -> Workflow:
    workflow = session.scalar(
        select(Workflow).where(Workflow.id == workflow_id).options(selectinload(Workflow.versions))
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


def persist_workflow_version(
    session: Session, workflow: Workflow | None, definition: WorkflowDefinition
) -> Workflow:
    data = definition.model_dump(mode="json", exclude_none=True)
    if workflow is None:
        workflow = Workflow(name=definition.name, description=definition.description)
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
        session.commit()
        session.refresh(workflow)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Workflow version conflict") from exc
    return workflow


@app.get("/api/workflows", response_model=list[WorkflowResponse])
def list_workflows(session: Session = Depends(get_session)):
    workflows = session.scalars(
        select(Workflow).options(selectinload(Workflow.versions)).order_by(Workflow.id.desc())
    ).all()
    return [serialize_workflow(item) for item in workflows]


@app.post("/api/workflows", status_code=201, response_model=WorkflowResponse)
def create_workflow(payload: WorkflowDefinition, session: Session = Depends(get_session)):
    definition = validated_definition(payload)
    workflow = persist_workflow_version(session, None, definition)
    return serialize_workflow(workflow)


@app.get("/api/workflows/{workflow_id}", response_model=WorkflowResponse)
def get_workflow(workflow_id: int, session: Session = Depends(get_session)):
    return serialize_workflow(get_workflow_or_404(session, workflow_id))


@app.put("/api/workflows/{workflow_id}", response_model=WorkflowResponse)
def update_workflow(
    workflow_id: int,
    payload: WorkflowDefinition,
    session: Session = Depends(get_session),
):
    workflow = get_workflow_or_404(session, workflow_id)
    definition = validated_definition(payload)
    return serialize_workflow(persist_workflow_version(session, workflow, definition))


@app.get("/api/workflows/{workflow_id}/versions", response_model=list[WorkflowVersionResponse])
def list_workflow_versions(workflow_id: int, session: Session = Depends(get_session)):
    get_workflow_or_404(session, workflow_id)
    return session.scalars(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == workflow_id)
        .order_by(WorkflowVersion.version_number.desc())
    ).all()


def start_run_for_version(workflow_id: int, version_number: int | None, session: Session) -> dict:
    workflow = get_workflow_or_404(session, workflow_id)
    if version_number is None:
        version = workflow.current_version
    else:
        version = next(
            (item for item in workflow.versions if item.version_number == version_number), None
        )
        if version is None:
            raise HTTPException(status_code=404, detail="Workflow version not found")
    run = execute_workflow(session, workflow, version)
    return serialize_run(run)


@app.post("/api/workflows/{workflow_id}/runs", status_code=201, response_model=RunResponse)
def start_run(workflow_id: int, session: Session = Depends(get_session)):
    return start_run_for_version(workflow_id, None, session)


@app.post(
    "/api/workflows/{workflow_id}/versions/{version_number}/runs",
    status_code=201,
    response_model=RunResponse,
)
def start_version_run(
    workflow_id: int, version_number: int, session: Session = Depends(get_session)
):
    return start_run_for_version(workflow_id, version_number, session)


@app.get("/api/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: int, session: Session = Depends(get_session)):
    run = session.scalar(
        select(WorkflowRun)
        .where(WorkflowRun.id == run_id)
        .options(selectinload(WorkflowRun.steps), selectinload(WorkflowRun.workflow_version))
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return serialize_run(run)


@app.get("/api/runs", response_model=list[RunResponse])
def list_runs(
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
):
    runs = session.scalars(
        select(WorkflowRun)
        .options(selectinload(WorkflowRun.steps), selectinload(WorkflowRun.workflow_version))
        .order_by(WorkflowRun.id.desc())
        .limit(limit)
    ).all()
    return [serialize_run(run) for run in runs]
