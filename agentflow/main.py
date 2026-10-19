"""AgentFlow HTTP API."""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from agentflow.config import get_settings
from agentflow.database import Base, engine, get_session
from agentflow.execution import execute_workflow
from agentflow.models import Workflow, WorkflowRun
from agentflow.registry import action_catalog
from agentflow.workflows import validate_definition


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="AgentFlow API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(ValidationError)
async def validation_error_handler(_: Request, exc: ValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "detail": exc.errors(
                include_url=False,
                include_context=False,
                include_input=False,
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


def serialize_run(run: WorkflowRun) -> dict:
    return {
        "id": run.id,
        "workflow_id": run.workflow_id,
        "status": run.status,
        "error": run.error,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "steps": [
            {
                "id": step.step_id,
                "action": step.action,
                "status": step.status,
                "inputs": step.inputs,
                "output": step.output,
                "error": step.error,
                "started_at": step.started_at.isoformat() if step.started_at else None,
                "finished_at": step.finished_at.isoformat() if step.finished_at else None,
            }
            for step in sorted(run.steps, key=lambda item: item.id)
        ],
    }


@app.get("/api/workflows")
def list_workflows(session: Session = Depends(get_session)):
    workflows = session.scalars(select(Workflow).order_by(Workflow.id.desc())).all()
    return [{"id": item.id, "name": item.name, "definition": item.definition} for item in workflows]


@app.post("/api/workflows", status_code=201)
def create_workflow(payload: dict, session: Session = Depends(get_session)):
    try:
        definition = validate_definition(payload)
    except (ValidationError, ValueError) as exc:
        detail = (
            exc.errors(include_url=False, include_context=False, include_input=False)
            if isinstance(exc, ValidationError)
            else str(exc)
        )
        raise HTTPException(status_code=422, detail=detail) from exc
    workflow = Workflow(name=definition.name, definition=definition.model_dump(mode="json"))
    session.add(workflow)
    session.commit()
    session.refresh(workflow)
    return {"id": workflow.id, "name": workflow.name, "definition": workflow.definition}


@app.get("/api/workflows/{workflow_id}")
def get_workflow(workflow_id: int, session: Session = Depends(get_session)):
    workflow = session.get(Workflow, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"id": workflow.id, "name": workflow.name, "definition": workflow.definition}


@app.post("/api/workflows/{workflow_id}/runs", status_code=201)
def start_run(workflow_id: int, session: Session = Depends(get_session)):
    workflow = session.get(Workflow, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    run = execute_workflow(session, workflow)
    return serialize_run(run)


@app.get("/api/runs/{run_id}")
def get_run(run_id: int, session: Session = Depends(get_session)):
    run = session.scalar(
        select(WorkflowRun).where(WorkflowRun.id == run_id).options(selectinload(WorkflowRun.steps))
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return serialize_run(run)


@app.get("/api/runs")
def list_runs(
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
):
    runs = session.scalars(
        select(WorkflowRun)
        .options(selectinload(WorkflowRun.steps))
        .order_by(WorkflowRun.id.desc())
        .limit(limit)
    ).all()
    return [serialize_run(run) for run in runs]
