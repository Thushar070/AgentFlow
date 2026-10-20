import threading
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from agentflow.execution import enqueue_workflow
from agentflow.models import (
    ApprovalRequest,
    Base,
    RunStatus,
    StepAttempt,
    StepRun,
    Workflow,
    WorkflowJob,
    WorkflowRun,
    WorkflowVersion,
    utcnow,
)
from agentflow.registry import ACTION_REGISTRY
from agentflow.worker import ActionOutcome, _invoke_action, claim_job, process_one_job


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def queued_run(factory, definition=None, *, idempotency_key=None):
    definition = definition or {
        "version": 1,
        "name": "Worker flow",
        "steps": [{"id": "one", "action": "set_value", "inputs": {"value": "ok"}}],
    }
    with factory() as session:
        workflow = Workflow(name=definition["name"], description=None)
        session.add(workflow)
        session.flush()
        version = WorkflowVersion(
            workflow_id=workflow.id,
            version_number=1,
            definition=definition,
        )
        session.add(version)
        session.commit()
        run = enqueue_workflow(session, workflow, version, idempotency_key=idempotency_key)
        return run.id, workflow.id


def test_durable_job_is_claimed_once_and_success_is_persisted(database):
    run_id, _ = queued_run(database)
    with database() as first_session, database() as second_session:
        first_claim = claim_job(first_session, "worker-a")
        second_claim = claim_job(second_session, "worker-b")
    with database() as session:
        job = session.scalar(select(WorkflowJob).where(WorkflowJob.run_id == run_id))
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        session.commit()
    assert first_claim is not None
    assert second_claim is None

    process_one_job(session_factory=database, worker_id="worker-c")
    with database() as session:
        run = session.get(WorkflowRun, run_id)
        job = session.scalar(select(WorkflowJob).where(WorkflowJob.run_id == run_id))
        assert run.status == RunStatus.SUCCEEDED
        assert run.output == {"value": "ok"}
        assert job.status == "succeeded"
        assert session.scalar(select(StepAttempt)).status == "succeeded"


@pytest.mark.parametrize(("left", "expected", "selected"), [(2, "yes", "then"), (0, "no", "else")])
def test_conditional_executes_only_selected_branch_and_persists_skips(
    database, left, expected, selected
):
    definition = {
        "version": 1,
        "name": "Branch flow",
        "steps": [
            {
                "id": "choice",
                "type": "conditional",
                "condition": {"left": left, "operator": "gt", "right": 1},
                "then_steps": [{"id": "yes", "action": "set_value", "inputs": {"value": "yes"}}],
                "else_steps": [{"id": "no", "action": "set_value", "inputs": {"value": "no"}}],
            }
        ],
    }
    run_id, _ = queued_run(database, definition)
    assert process_one_job(session_factory=database, worker_id="branch-worker")
    with database() as session:
        run = session.get(WorkflowRun, run_id)
        assert run.status == RunStatus.SUCCEEDED
        assert run.output["value"] == expected
        steps = {step.step_id: step for step in run.steps}
        assert steps[f"choice__{selected}__{expected}"].status == "succeeded"
        skipped_name = "no" if selected == "then" else "yes"
        assert (
            steps[f"choice__{'else' if selected == 'then' else 'then'}__{skipped_name}"].status
            == "skipped"
        )
        assert steps["choice"].output["decision"] is (selected == "then")


def test_approval_gate_inside_selected_branch_pauses_before_attempt(database):
    definition = {
        "version": 1,
        "name": "Branch approval flow",
        "steps": [
            {
                "id": "choice",
                "type": "conditional",
                "condition": {"left": True, "operator": "eq", "right": True},
                "then_steps": [
                    {
                        "id": "sensitive",
                        "action": "require_approval",
                        "inputs": {"value": {"request": "delete"}},
                    }
                ],
                "else_steps": [
                    {"id": "not_selected", "action": "set_value", "inputs": {"value": "no"}}
                ],
            }
        ],
    }
    run_id, _ = queued_run(database, definition)
    assert process_one_job(session_factory=database, worker_id="approval-branch-worker")
    with database() as session:
        run = session.get(WorkflowRun, run_id)
        assert run.status == RunStatus.AWAITING_APPROVAL
        steps = {step.step_id: step for step in run.steps}
        gate = steps["choice__then__sensitive"]
        assert gate.status == "queued"
        assert gate.attempts == []
        assert steps["choice__else__not_selected"].status == "skipped"
        request = session.scalar(select(ApprovalRequest).where(ApprovalRequest.run_id == run_id))
        assert request.status == "pending"
        assert request.workflow_version_id == run.workflow_version_id


def test_transient_failure_retries_with_persisted_attempt_history(database, monkeypatch):
    run_id, _ = queued_run(database)
    spec = ACTION_REGISTRY["set_value"]
    monkeypatch.setitem(
        ACTION_REGISTRY,
        "set_value",
        replace(spec, max_attempts=2, backoff_base_seconds=0),
    )
    outcomes = iter(
        [
            ActionOutcome("failed", error="temporary", category="execution_failed"),
            ActionOutcome("succeeded", output={"value": "ok"}),
        ]
    )
    monkeypatch.setattr("agentflow.worker._invoke_action", lambda *_a, **_kw: next(outcomes))

    assert process_one_job(session_factory=database, worker_id="worker-a")
    with database() as session:
        run = session.get(WorkflowRun, run_id)
        assert run.status == RunStatus.RETRYING
        assert [attempt.status for attempt in run.steps[0].attempts] == ["failed"]
        job = session.scalar(select(WorkflowJob).where(WorkflowJob.run_id == run_id))
        assert job.status == "queued"
        assert job.available_at >= utcnow() - timedelta(seconds=0.1)

    assert process_one_job(session_factory=database, worker_id="worker-b")
    with database() as session:
        run = session.get(WorkflowRun, run_id)
        assert run.status == RunStatus.SUCCEEDED
        assert [attempt.status for attempt in run.steps[0].attempts] == ["failed", "succeeded"]


def test_retry_exhaustion_is_terminal_and_observable(database, monkeypatch):
    run_id, _ = queued_run(database)
    spec = ACTION_REGISTRY["set_value"]
    monkeypatch.setitem(
        ACTION_REGISTRY,
        "set_value",
        replace(spec, max_attempts=1, backoff_base_seconds=0),
    )
    monkeypatch.setattr(
        "agentflow.worker._invoke_action",
        lambda *_a, **_kw: ActionOutcome("failed", error="temporary", category="execution_failed"),
    )
    assert process_one_job(session_factory=database, worker_id="worker-a")
    with database() as session:
        run = session.get(WorkflowRun, run_id)
        assert run.status == RunStatus.FAILED
        assert run.error_category == "retries_exhausted"
        assert run.steps[0].attempts[0].error_category == "execution_failed"
        assert run.steps[0].error_category == "retries_exhausted"


def test_expired_worker_lease_recovers_interrupted_step(database, monkeypatch):
    run_id, _ = queued_run(database)
    spec = ACTION_REGISTRY["set_value"]
    monkeypatch.setitem(
        ACTION_REGISTRY,
        "set_value",
        replace(spec, backoff_base_seconds=0),
    )
    with database() as session:
        claim = claim_job(session, "worker-died")
        assert claim is not None
        run = session.get(WorkflowRun, run_id)
        run.status = RunStatus.RUNNING
        step = StepRun(
            run_id=run_id,
            step_id="one",
            step_order=0,
            action="set_value",
            status="running",
            inputs={"value": "ok"},
            started_at=utcnow(),
        )
        session.add(step)
        session.flush()
        session.add(
            StepAttempt(
                step_run_id=step.id,
                attempt_number=1,
                status="running",
                started_at=utcnow(),
            )
        )
        job = session.get(WorkflowJob, claim.job_id)
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        session.commit()

    assert process_one_job(session_factory=database, worker_id="worker-recovered")
    with database() as session:
        run = session.get(WorkflowRun, run_id)
        assert run.status == RunStatus.RETRYING
        assert run.steps[0].attempts[0].status == "interrupted"
        assert run.steps[0].attempts[0].error_category == "worker_interrupted"

    assert process_one_job(session_factory=database, worker_id="worker-recovered")
    with database() as session:
        run = session.get(WorkflowRun, run_id)
        assert run.status == RunStatus.SUCCEEDED
        assert [item.status for item in run.steps[0].attempts] == ["interrupted", "succeeded"]


def test_child_action_timeout_terminates_the_action_process():
    outcome = _invoke_action(
        "set_value",
        {"value": "ok"},
        1e-9,
        cancel_requested=lambda: False,
        heartbeat=lambda: True,
        shutdown=threading.Event(),
    )
    assert outcome.status == "timed_out"
    assert outcome.category == "action_timeout"


def test_worker_shutdown_releases_claim_for_later_worker(database):
    run_id, _ = queued_run(database)
    shutdown = threading.Event()
    shutdown.set()
    assert process_one_job(session_factory=database, worker_id="worker-stop", shutdown=shutdown)
    with database() as session:
        run = session.get(WorkflowRun, run_id)
        job = session.scalar(select(WorkflowJob).where(WorkflowJob.run_id == run_id))
        assert run.status == RunStatus.RETRYING
        assert job.status == "queued"
        assert job.locked_by is None


def test_active_cancel_request_stops_run_and_persists_cancelled_attempt(database, monkeypatch):
    run_id, _ = queued_run(database)
    with database() as session:
        claim = claim_job(session, "worker-active")
    assert claim is not None

    def cancel_during_action(*_args, **_kwargs):
        with database() as session:
            run = session.get(WorkflowRun, run_id)
            run.status = RunStatus.CANCEL_REQUESTED
            run.cancel_requested_at = utcnow()
            session.commit()
        return ActionOutcome(
            "cancelled", error="Action cancelled by request.", category="cancelled"
        )

    monkeypatch.setattr("agentflow.worker._invoke_action", cancel_during_action)
    from agentflow.worker import process_claimed_job

    process_claimed_job(claim, session_factory=database)
    with database() as session:
        run = session.get(WorkflowRun, run_id)
        assert run.status == RunStatus.CANCELLED
        assert run.error_category == "cancelled"
        assert run.steps[0].status == "cancelled"
        assert run.steps[0].attempts[0].status == "cancelled"
