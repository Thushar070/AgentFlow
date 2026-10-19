"""Synchronous execution with one atomic database transaction per run."""

from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy.orm import Session

from agentflow.models import (
    RunStatus,
    StepRun,
    StepStatus,
    Workflow,
    WorkflowRun,
    WorkflowVersion,
    transition_status,
)
from agentflow.registry import run_action
from agentflow.workflows import validate_definition


def now() -> datetime:
    return datetime.now(UTC)


def execute_workflow(session: Session, workflow: Workflow, version: WorkflowVersion) -> WorkflowRun:
    """Execute one immutable version; commit all run records together."""
    if version.workflow_id != workflow.id:
        raise ValueError("Workflow version does not belong to this workflow")
    definition = validate_definition(version.definition)
    timestamp = now()
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        status=RunStatus.RUNNING,
        created_at=timestamp,
        started_at=timestamp,
    )
    session.add(run)
    outputs: dict[str, dict] = {}
    failed = False
    try:
        session.flush()
        for order, step in enumerate(definition.steps):
            inputs = {
                key: outputs[value["$ref"]]["value"]
                if isinstance(value, dict) and "$ref" in value
                else value
                for key, value in step.inputs.items()
            }
            record = StepRun(
                run_id=run.id,
                step_id=step.id,
                step_order=order,
                action=step.action,
                status=StepStatus.RUNNING,
                inputs=inputs,
                started_at=now(),
            )
            session.add(record)
            session.flush()
            try:
                record.output = run_action(step.action, inputs)
                record.status = transition_status(
                    record.status, StepStatus.SUCCEEDED, entity="step"
                )
                outputs[step.id] = record.output
            except ValidationError:
                record.status = transition_status(record.status, StepStatus.FAILED, entity="step")
                record.error = "Action inputs do not match the registered action schema."
                run.error = f"Step '{step.id}' failed: invalid action inputs."
                failed = True
            except Exception:
                record.status = transition_status(record.status, StepStatus.FAILED, entity="step")
                record.error = "Action execution failed."
                run.error = f"Step '{step.id}' failed during action execution."
                failed = True
            record.finished_at = now()
            if failed:
                break

        target = RunStatus.FAILED if failed else RunStatus.SUCCEEDED
        run.status = transition_status(run.status, target, entity="run")
        run.output = None if failed else outputs.get(definition.steps[-1].id)
        run.finished_at = now()
        session.commit()
        session.refresh(run)
        return run
    except Exception:
        session.rollback()
        raise
