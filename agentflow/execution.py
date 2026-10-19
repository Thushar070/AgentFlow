"""Synchronous execution coordinator with durable state transitions."""

from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy.orm import Session

from agentflow.models import StepRun, Workflow, WorkflowRun
from agentflow.registry import run_action
from agentflow.workflows import validate_definition


def now() -> datetime:
    return datetime.now(UTC)


def execute_workflow(session: Session, workflow: Workflow) -> WorkflowRun:
    definition = validate_definition(workflow.definition)
    run = WorkflowRun(workflow_id=workflow.id, status="running", started_at=now())
    session.add(run)
    session.flush()
    outputs: dict[str, dict] = {}

    for step in definition.steps:
        inputs = {
            key: outputs[value["$ref"]]["value"]
            if isinstance(value, dict) and "$ref" in value
            else value
            for key, value in step.inputs.items()
        }
        record = StepRun(
            run_id=run.id,
            step_id=step.id,
            action=step.action,
            status="running",
            inputs=inputs,
            started_at=now(),
        )
        session.add(record)
        session.flush()
        try:
            record.output = run_action(step.action, inputs)
            record.status = "succeeded"
            outputs[step.id] = record.output
        except Exception as exc:
            record.status = "failed"
            record.error = (
                "Action inputs do not match the registered action schema."
                if isinstance(exc, ValidationError)
                else "Action execution failed."
            )
            record.finished_at = now()
            run.status = "failed"
            run.error = f"Step '{step.id}' failed: {exc}"
            run.finished_at = now()
            session.commit()
            session.refresh(run)
            return run
        record.finished_at = now()
        session.commit()

    run.status = "succeeded"
    run.finished_at = now()
    session.commit()
    session.refresh(run)
    return run
