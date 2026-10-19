"""Durable lease-based worker for queued workflow runs."""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from agentflow.database import SessionLocal
from agentflow.models import (
    RunStatus,
    StepAttempt,
    StepRun,
    StepStatus,
    WorkflowJob,
    WorkflowRun,
    WorkflowVersion,
    transition_status,
)
from agentflow.registry import action_spec, action_timeout
from agentflow.workflows import validate_definition

LEASE_SECONDS = 30
POLL_SECONDS = 0.5
MAX_ACTION_OUTPUT_BYTES = 1024 * 1024


def now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ClaimedJob:
    job_id: int
    run_id: int
    worker_id: str
    generation: int


@dataclass(frozen=True)
class ActionOutcome:
    status: str
    output: dict | None = None
    error: str | None = None
    category: str | None = None


def claim_job(
    session: Session,
    worker_id: str,
    *,
    lease_seconds: int = LEASE_SECONDS,
) -> ClaimedJob | None:
    """Atomically claim one due job or reclaim a job whose lease expired."""
    timestamp = now()
    available = and_(WorkflowJob.status == "queued", WorkflowJob.available_at <= timestamp)
    expired = and_(WorkflowJob.status == "running", WorkflowJob.lease_expires_at <= timestamp)
    candidate = session.scalar(
        select(WorkflowJob)
        .where(or_(available, expired))
        .order_by(WorkflowJob.available_at, WorkflowJob.id)
        .limit(1)
    )
    if candidate is None:
        session.rollback()
        return None
    job_id, run_id = candidate.id, candidate.run_id
    result = session.execute(
        update(WorkflowJob)
        .where(WorkflowJob.id == job_id, or_(available, expired))
        .values(
            status="running",
            attempts=WorkflowJob.attempts + 1,
            locked_by=worker_id,
            lease_generation=WorkflowJob.lease_generation + 1,
            lease_expires_at=timestamp + timedelta(seconds=lease_seconds),
            updated_at=timestamp,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        return None
    session.commit()
    generation = session.scalar(
        select(WorkflowJob.lease_generation).where(WorkflowJob.id == job_id)
    )
    session.commit()
    return ClaimedJob(job_id, run_id, worker_id, generation)


def _heartbeat(session_factory: sessionmaker, claim: ClaimedJob) -> bool:
    timestamp = now()
    with session_factory() as session:
        result = session.execute(
            update(WorkflowJob)
            .where(
                WorkflowJob.id == claim.job_id,
                WorkflowJob.status == "running",
                WorkflowJob.locked_by == claim.worker_id,
                WorkflowJob.lease_generation == claim.generation,
                WorkflowJob.lease_expires_at > timestamp,
            )
            .values(
                lease_expires_at=timestamp + timedelta(seconds=LEASE_SECONDS),
                updated_at=timestamp,
            )
        )
        session.commit()
        return result.rowcount == 1


def _claim_is_current(session: Session, claim: ClaimedJob) -> bool:
    job = session.get(WorkflowJob, claim.job_id)
    return bool(
        job
        and job.status == "running"
        and job.locked_by == claim.worker_id
        and job.lease_generation == claim.generation
        and job.lease_expires_at
        and job.lease_expires_at > now()
    )


def _invoke_action(
    action: str,
    inputs: dict,
    timeout: float,
    *,
    cancel_requested: Callable[[], bool],
    heartbeat: Callable[[], bool],
    shutdown: threading.Event,
) -> ActionOutcome:
    request = json.dumps({"action": action, "inputs": inputs}, separators=(",", ":"))
    with (
        tempfile.TemporaryFile(mode="w+b") as input_file,
        tempfile.TemporaryFile(mode="w+b") as output_file,
    ):
        input_file.write(request.encode("utf-8"))
        input_file.seek(0)
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "agentflow.action_runner"],
                stdin=input_file,
                stdout=output_file,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError:
            return ActionOutcome(
                "failed", error="Action worker could not start.", category="execution_failed"
            )
        start = time.monotonic()
        next_heartbeat = start + LEASE_SECONDS / 3
        while process.poll() is None:
            try:
                cancelled = cancel_requested()
            except Exception:
                cancelled = False
            if cancelled:
                process.terminate()
                try:
                    process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
                return ActionOutcome(
                    "cancelled", error="Action cancelled by request.", category="cancelled"
                )
            if shutdown.is_set():
                # Finish this bounded action, then release the run for another worker.
                pass
            if time.monotonic() - start >= timeout:
                process.terminate()
                try:
                    process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
                return ActionOutcome(
                    "timed_out",
                    error=f"Action exceeded its {timeout:g}s timeout.",
                    category="action_timeout",
                )
            if time.monotonic() >= next_heartbeat:
                try:
                    lease_current = heartbeat()
                except Exception:
                    lease_current = False
                if not lease_current:
                    process.terminate()
                    try:
                        process.wait(timeout=0.25)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)
                    return ActionOutcome(
                        "interrupted", error="Worker lease was lost.", category="worker_interrupted"
                    )
                next_heartbeat = time.monotonic() + LEASE_SECONDS / 3
            time.sleep(0.05)
        process.wait()
        if time.monotonic() - start >= timeout:
            return ActionOutcome(
                "timed_out",
                error=f"Action exceeded its {timeout:g}s timeout.",
                category="action_timeout",
            )
        output_file.seek(0, os.SEEK_END)
        output_size = output_file.tell()
        if output_size > MAX_ACTION_OUTPUT_BYTES:
            return ActionOutcome(
                "failed",
                error="Action output exceeded the worker output limit.",
                category="invalid_output",
            )
        output_file.seek(0)
        try:
            response = json.loads(output_file.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ActionOutcome(
                "failed",
                error="Action worker returned an invalid response.",
                category="execution_failed",
            )
    if process.returncode != 0 or not isinstance(response, dict):
        return ActionOutcome(
            "failed", error="Action worker exited unexpectedly.", category="execution_failed"
        )
    if response.get("ok") is True and isinstance(response.get("output"), dict):
        return ActionOutcome("succeeded", output=response["output"])
    category = response.get("category")
    allowed = {
        "unknown_action",
        "invalid_input",
        "invalid_output",
        "permission_denied",
        "execution_failed",
    }
    if category not in allowed:
        category = "execution_failed"
    return ActionOutcome(
        "failed", error=str(response.get("error") or "Action execution failed."), category=category
    )


def _finish_job(session: Session, claim: ClaimedJob, status: str) -> None:
    job = session.get(WorkflowJob, claim.job_id)
    if job is None or not _claim_is_current(session, claim):
        session.rollback()
        return
    job.status = status
    job.locked_by = None
    job.lease_expires_at = None
    job.updated_at = now()


def _release_for_retry(session: Session, claim: ClaimedJob, run: WorkflowRun, delay: float) -> None:
    if not _claim_is_current(session, claim):
        session.rollback()
        return
    job = session.get(WorkflowJob, claim.job_id)
    if run.status != RunStatus.RETRYING:
        run.status = transition_status(run.status, RunStatus.RETRYING, entity="run")
    job.status = "queued"
    job.available_at = now() + timedelta(seconds=delay)
    job.locked_by = None
    job.lease_expires_at = None
    job.updated_at = now()


def _finish_cancelled(
    session: Session, claim: ClaimedJob, run: WorkflowRun, step: StepRun | None = None
) -> None:
    if step and step.status in {StepStatus.QUEUED, StepStatus.RUNNING, StepStatus.RETRYING}:
        step.status = transition_status(step.status, StepStatus.CANCELLED, entity="step")
        step.error = "Action cancelled by request."
        step.error_category = "cancelled"
        step.finished_at = now()
    if run.status != RunStatus.CANCELLED:
        run.status = transition_status(run.status, RunStatus.CANCELLED, entity="run")
    run.error = "Run cancelled by request."
    run.error_category = "cancelled"
    run.finished_at = now()
    _finish_job(session, claim, "cancelled")


def _check_cancelled(session_factory: sessionmaker, run_id: int) -> bool:
    with session_factory() as session:
        run = session.get(WorkflowRun, run_id)
        return bool(run and run.status == RunStatus.CANCEL_REQUESTED)


def _recover_interrupted_attempt(
    session: Session, claim: ClaimedJob, run: WorkflowRun, step: StepRun
) -> bool:
    active = next((item for item in reversed(step.attempts) if item.status == "running"), None)
    if active is None:
        return False
    spec = action_spec(step.action)
    active.status = "interrupted"
    active.error = "Worker stopped before persisting this attempt's result."
    active.error_category = "worker_interrupted"
    active.finished_at = now()
    if not spec.idempotent_replay:
        active.error = "Action outcome is uncertain and requires reconciliation."
        active.error_category = "reconciliation_required"
        step.status = StepStatus.FAILED
        step.error = active.error
        step.error_category = active.error_category
        step.finished_at = now()
        run.status = RunStatus.FAILED
        run.error = f"Step '{step.step_id}' requires reconciliation."
        run.error_category = active.error_category
        run.finished_at = now()
        _finish_job(session, claim, "failed")
        return True
    if active.attempt_number >= spec.max_attempts:
        step.status = StepStatus.FAILED
        step.error = "Retry limit reached after worker interruption."
        step.error_category = "retries_exhausted"
        step.finished_at = now()
        run.status = RunStatus.FAILED
        run.error = f"Step '{step.step_id}' exhausted retries after worker interruption."
        run.error_category = "retries_exhausted"
        run.finished_at = now()
        _finish_job(session, claim, "failed")
        return True
    step.status = StepStatus.RETRYING
    run.status = RunStatus.RETRYING
    delay = min(spec.backoff_base_seconds * (2 ** (active.attempt_number - 1)), 30.0)
    _release_for_retry(session, claim, run, delay)
    return True


def process_claimed_job(
    claim: ClaimedJob,
    *,
    session_factory: sessionmaker = SessionLocal,
    shutdown: threading.Event | None = None,
) -> None:
    shutdown = shutdown or threading.Event()
    with session_factory() as session:
        run = session.get(WorkflowRun, claim.run_id)
        if run is None or not _claim_is_current(session, claim):
            session.rollback()
            return
        if run.status == RunStatus.CANCEL_REQUESTED:
            _finish_cancelled(session, claim, run)
            session.commit()
            return
        version = session.get(WorkflowVersion, run.workflow_version_id)
        try:
            definition = validate_definition(version.definition)
        except Exception:
            run.status = RunStatus.FAILED
            run.error = "Stored workflow definition is no longer valid."
            run.error_category = "execution_failed"
            run.finished_at = now()
            _finish_job(session, claim, "failed")
            session.commit()
            return
        if run.status in {RunStatus.QUEUED, RunStatus.RETRYING}:
            run.status = transition_status(run.status, RunStatus.RUNNING, entity="run")
        if run.started_at is None:
            run.started_at = now()
        session.commit()

        outputs: dict[str, dict] = {}
        for order, step_definition in enumerate(definition.steps):
            if not _claim_is_current(session, claim):
                session.rollback()
                return
            if _check_cancelled(session_factory, run.id):
                _finish_cancelled(session, claim, run)
                session.commit()
                return
            if shutdown.is_set():
                run.status = RunStatus.RETRYING
                _release_for_retry(session, claim, run, 0)
                session.commit()
                return

            step = session.scalar(
                select(StepRun).where(
                    StepRun.run_id == run.id, StepRun.step_id == step_definition.id
                )
            )
            if step is not None and step.status == StepStatus.SUCCEEDED:
                outputs[step.step_id] = step.output or {}
                continue
            if step is not None and _recover_interrupted_attempt(session, claim, run, step):
                session.commit()
                return
            if step is None:
                inputs = {
                    key: outputs[value["$ref"]]["value"]
                    if isinstance(value, dict) and "$ref" in value
                    else value
                    for key, value in step_definition.inputs.items()
                }
                step = StepRun(
                    run_id=run.id,
                    step_id=step_definition.id,
                    step_order=order,
                    action=step_definition.action,
                    status=StepStatus.QUEUED,
                    inputs=inputs,
                )
                session.add(step)
                session.flush()
            else:
                inputs = step.inputs

            spec = action_spec(step.action)
            previous_attempt = max((item.attempt_number for item in step.attempts), default=0)
            attempt = StepAttempt(
                step_run_id=step.id,
                attempt_number=previous_attempt + 1,
                status="running",
                started_at=now(),
            )
            step.status = transition_status(step.status, StepStatus.RUNNING, entity="step")
            step.error = None
            step.error_category = None
            step.started_at = step.started_at or now()
            step.finished_at = None
            session.add(attempt)
            session.commit()

            outcome = _invoke_action(
                step.action,
                inputs,
                action_timeout(step.action),
                cancel_requested=lambda: _check_cancelled(session_factory, run.id),
                heartbeat=lambda: _heartbeat(session_factory, claim),
                shutdown=shutdown,
            )
            session.expire_all()
            run = session.get(WorkflowRun, claim.run_id)
            step = session.scalar(
                select(StepRun)
                .where(StepRun.run_id == run.id, StepRun.step_id == step_definition.id)
                .options()
            )
            attempt = session.get(StepAttempt, attempt.id)
            if not _claim_is_current(session, claim):
                session.rollback()
                return
            if run.status == RunStatus.CANCEL_REQUESTED or outcome.status == "cancelled":
                attempt.status = "cancelled"
                attempt.error = outcome.error or "Action cancelled by request."
                attempt.error_category = "cancelled"
                attempt.finished_at = now()
                _finish_cancelled(session, claim, run, step)
                session.commit()
                return

            if outcome.status == "succeeded":
                attempt.status = "succeeded"
                attempt.output = outcome.output
                attempt.finished_at = now()
                step.output = outcome.output
                step.status = transition_status(step.status, StepStatus.SUCCEEDED, entity="step")
                step.finished_at = now()
                outputs[step.step_id] = outcome.output or {}
                session.commit()
                if shutdown.is_set():
                    run.status = RunStatus.RETRYING
                    _release_for_retry(session, claim, run, 0)
                    session.commit()
                    return
                continue

            attempt.status = outcome.status
            attempt.error = outcome.error
            attempt.error_category = outcome.category
            attempt.finished_at = now()
            category = outcome.category or "execution_failed"
            retryable = category in spec.retryable_categories
            if retryable and spec.idempotent_replay and attempt.attempt_number < spec.max_attempts:
                step.status = transition_status(step.status, StepStatus.RETRYING, entity="step")
                step.error = outcome.error
                step.error_category = category
                run.status = transition_status(run.status, RunStatus.RETRYING, entity="run")
                run.error = f"Step '{step.step_id}' will retry after a transient failure."
                run.error_category = category
                delay = min(spec.backoff_base_seconds * (2 ** (attempt.attempt_number - 1)), 30.0)
                _release_for_retry(session, claim, run, delay)
                session.commit()
                return
            if retryable and attempt.attempt_number >= spec.max_attempts:
                category = "retries_exhausted"
                error = "Retry limit reached after repeated transient failures."
            else:
                error = outcome.error or "Action execution failed."
            if not spec.idempotent_replay and category in spec.retryable_categories:
                category = "reconciliation_required"
                error = "Action outcome is uncertain and requires reconciliation."
            step.status = transition_status(step.status, StepStatus.FAILED, entity="step")
            step.error = error
            step.error_category = category
            step.finished_at = now()
            run.status = transition_status(run.status, RunStatus.FAILED, entity="run")
            run.error = f"Step '{step.step_id}' failed: {error}"
            run.error_category = category
            run.finished_at = now()
            _finish_job(session, claim, "failed")
            session.commit()
            return

        run.status = transition_status(run.status, RunStatus.SUCCEEDED, entity="run")
        run.output = outputs.get(definition.steps[-1].id)
        run.finished_at = now()
        _finish_job(session, claim, "succeeded")
        session.commit()


def process_one_job(
    *,
    session_factory: sessionmaker = SessionLocal,
    worker_id: str | None = None,
    shutdown: threading.Event | None = None,
) -> bool:
    worker_id = worker_id or f"worker-{uuid.uuid4()}"
    with session_factory() as session:
        try:
            claim = claim_job(session, worker_id)
        except OperationalError:
            session.rollback()
            return False
    if claim is None:
        return False
    process_claimed_job(claim, session_factory=session_factory, shutdown=shutdown)
    return True


def worker_loop(
    *,
    session_factory: sessionmaker = SessionLocal,
    poll_seconds: float = POLL_SECONDS,
    shutdown: threading.Event | None = None,
) -> None:
    shutdown = shutdown or threading.Event()
    worker_id = f"worker-{uuid.uuid4()}"
    while not shutdown.is_set():
        try:
            processed = process_one_job(
                session_factory=session_factory, worker_id=worker_id, shutdown=shutdown
            )
        except Exception:
            # Leave an uncommitted claim to expire; the next worker safely recovers it.
            processed = True
        if not processed:
            shutdown.wait(poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Process AgentFlow's durable workflow queue")
    parser.add_argument("--once", action="store_true", help="process at most one available run")
    parser.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)
    args = parser.parse_args()
    shutdown = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: shutdown.set())
    signal.signal(signal.SIGINT, lambda *_: shutdown.set())
    if args.once:
        process_one_job(shutdown=shutdown)
    else:
        worker_loop(poll_seconds=args.poll_seconds, shutdown=shutdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
