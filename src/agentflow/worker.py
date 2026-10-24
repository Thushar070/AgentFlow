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

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from agentflow.audit import record_event
from agentflow.config import get_settings
from agentflow.database import SessionLocal
from agentflow.models import (
    ApprovalRequest,
    RunStatus,
    StepAttempt,
    StepRun,
    StepStatus,
    User,
    WorkflowJob,
    WorkflowRun,
    WorkflowVersion,
    transition_status,
)
from agentflow.registry import (
    ActionErrorCategory,
    action_requires_approval,
    action_spec,
    action_timeout,
)
from agentflow.security import canonical_hash
from agentflow.workflows import (
    ConditionalDefinition,
    StepDefinition,
    WorkflowDefinition,
    validate_definition,
)

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


@dataclass(frozen=True)
class ExecutableStep:
    step: StepDefinition
    logical_id: str
    condition_root: bool = False
    branch_parent: str | None = None
    branch_value: bool | None = None
    branch_last: bool = False


def _flatten_steps(definition: WorkflowDefinition) -> list[ExecutableStep]:
    result: list[ExecutableStep] = []
    for item in definition.steps:
        if isinstance(item, ConditionalDefinition):
            condition_step = StepDefinition(
                id=item.id,
                action="compare_values",
                inputs=item.condition,
            )
            result.append(ExecutableStep(condition_step, item.id, condition_root=True))
            for branch_name, branch_value, branch_steps in (
                ("then", True, item.then_steps),
                ("else", False, item.else_steps),
            ):
                for index, branch_step in enumerate(branch_steps):
                    result.append(
                        ExecutableStep(
                            step=StepDefinition(
                                id=f"{item.id}__{branch_name}__{branch_step.id}",
                                action=branch_step.action,
                                inputs=branch_step.inputs,
                            ),
                            logical_id=branch_step.id,
                            branch_parent=item.id,
                            branch_value=branch_value,
                            branch_last=index == len(branch_steps) - 1,
                        )
                    )
        else:
            result.append(ExecutableStep(item, item.id))
    return result


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
    expired_approval = and_(
        WorkflowJob.status == "awaiting_approval",
        exists(
            select(ApprovalRequest.id).where(
                ApprovalRequest.run_id == WorkflowJob.run_id,
                ApprovalRequest.status == "pending",
                ApprovalRequest.expires_at <= timestamp,
            )
        ),
    )
    eligible = or_(available, expired, expired_approval)
    candidate = session.scalar(
        select(WorkflowJob)
        .where(eligible)
        .order_by(WorkflowJob.available_at, WorkflowJob.id)
        .limit(1)
    )
    if candidate is None:
        session.rollback()
        return None
    job_id, run_id = candidate.id, candidate.run_id
    result = session.execute(
        update(WorkflowJob)
        .where(WorkflowJob.id == job_id, eligible)
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
    allowed = {item.value for item in ActionErrorCategory} | {
        "action_timeout",
        "retries_exhausted",
        "worker_interrupted",
        "cancelled",
        "reconciliation_required",
        "approval_rejected",
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
    record_event(
        session,
        "run.cancelled",
        actor_kind="worker",
        resource_type="run",
        resource_id=run.id,
        workflow_id=run.workflow_id,
        run_id=run.id,
        outcome="cancelled",
        correlation_id=f"run:{run.id}",
    )


def _check_cancelled(session_factory: sessionmaker, run_id: int) -> bool:
    with session_factory() as session:
        run = session.get(WorkflowRun, run_id)
        return bool(run and run.status == RunStatus.CANCEL_REQUESTED)


def _resolve_json_pointer(value, pointer: str):
    current = value
    if not pointer:
        return current
    if not pointer.startswith("/"):
        raise ValueError("reference path must be a JSON Pointer")
    for raw in pointer[1:].split("/"):
        if "~" in raw and any(char not in "01" for char in raw.split("~")[1:]):
            raise ValueError("reference path contains an invalid escape")
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list) and key.isdigit() and int(key) < len(current):
            current = current[int(key)]
        else:
            raise ValueError("referenced workflow value does not exist")
    return current


def _resolve_value(value, outputs: dict[str, dict], workflow_input: dict):
    if isinstance(value, dict):
        if "$ref" in value:
            reference = value["$ref"]
            if isinstance(reference, str):
                step_id, pointer = reference, "/value"
            else:
                step_id, pointer = reference["step"], reference["path"]
            return _resolve_json_pointer(outputs[step_id], pointer)
        if "$input" in value:
            reference = value["$input"]
            if isinstance(reference, str):
                name, pointer = reference, ""
            else:
                name, pointer = reference["name"], reference["path"]
            return _resolve_json_pointer(workflow_input[name], pointer)
        return {key: _resolve_value(item, outputs, workflow_input) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_value(item, outputs, workflow_input) for item in value]
    return value


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
    record_event(
        session,
        "worker.attempt_interrupted",
        actor_kind="worker",
        resource_type="step",
        resource_id=step.id,
        workflow_id=run.workflow_id,
        run_id=run.id,
        outcome="interrupted",
        correlation_id=f"run:{run.id}",
        details={"step_id": step.step_id, "attempt_number": active.attempt_number},
    )
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


def _wait_for_approval_if_required(
    session: Session, claim: ClaimedJob, run: WorkflowRun, step: StepRun
) -> bool:
    if not action_requires_approval(step.action):
        return False
    now_value = now()
    digest = canonical_hash(step.inputs)
    requests = session.scalars(
        select(ApprovalRequest)
        .where(ApprovalRequest.run_id == run.id, ApprovalRequest.step_run_id == step.id)
        .order_by(ApprovalRequest.id.desc())
    ).all()
    current = requests[0] if requests else None
    same_binding = bool(
        current
        and current.workflow_version_id == run.workflow_version_id
        and current.action == step.action
        and current.inputs_hash == digest
    )
    if current and same_binding and current.status == "rejected":
        step.status = StepStatus.FAILED
        step.error = "A required approval was rejected."
        step.error_category = "approval_rejected"
        step.finished_at = now_value
        run.status = RunStatus.FAILED
        run.error = f"Step '{step.step_id}' was rejected by an approver."
        run.error_category = "approval_rejected"
        run.finished_at = now_value
        job = session.get(WorkflowJob, claim.job_id)
        if job:
            job.status = "failed"
            job.locked_by = None
            job.lease_expires_at = None
        record_event(
            session,
            "run.approval_rejected",
            actor_kind="worker",
            resource_type="run",
            resource_id=run.id,
            workflow_id=run.workflow_id,
            run_id=run.id,
            outcome="failed",
            correlation_id=f"run:{run.id}",
        )
        return True

    if current and same_binding and current.status == "approved":
        approver = session.get(User, current.decided_by_id) if current.decided_by_id else None
        if (
            current.expires_at > now_value
            and current.requester_id != current.decided_by_id
            and approver is not None
            and approver.is_active
            and approver.role in {"admin", "approver"}
        ):
            return False
        current.status = "stale"
        current.decided_at = now_value

    if current and same_binding and current.status == "pending" and current.expires_at > now_value:
        run.status = RunStatus.AWAITING_APPROVAL
        job = session.get(WorkflowJob, claim.job_id)
        if job:
            job.status = "awaiting_approval"
            job.locked_by = None
            job.lease_expires_at = None
            job.updated_at = now_value
        return True

    if current and current.status in {"pending", "approved"}:
        current.status = "stale"
        current.decided_at = now_value
        record_event(
            session,
            "approval.stale",
            actor_kind="worker",
            resource_type="approval",
            resource_id=current.id,
            run_id=run.id,
            outcome="stale",
            correlation_id=f"run:{run.id}",
        )

    request_row = ApprovalRequest(
        run_id=run.id,
        step_run_id=step.id,
        workflow_version_id=run.workflow_version_id,
        action=step.action,
        inputs_hash=digest,
        requester_id=run.created_by_id,
        status="pending",
        created_at=now_value,
        expires_at=now_value + timedelta(hours=get_settings().approval_ttl_hours),
    )
    session.add(request_row)
    run.status = RunStatus.AWAITING_APPROVAL
    run.error = None
    run.error_category = None
    job = session.get(WorkflowJob, claim.job_id)
    if job:
        job.status = "awaiting_approval"
        job.locked_by = None
        job.lease_expires_at = None
        job.updated_at = now_value
    requester = session.get(User, run.created_by_id) if run.created_by_id else None
    record_event(
        session,
        "approval.requested",
        actor=requester,
        actor_kind="system",
        resource_type="approval",
        resource_id=run.id,
        workflow_id=run.workflow_id,
        run_id=run.id,
        outcome="pending",
        correlation_id=f"run:{run.id}",
        details={"step_id": step.step_id, "action": step.action},
    )
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
            record_event(
                session,
                "run.started",
                actor_kind="worker",
                resource_type="run",
                resource_id=run.id,
                workflow_id=run.workflow_id,
                run_id=run.id,
                outcome="running",
                correlation_id=f"run:{run.id}",
            )
        session.commit()

        outputs: dict[str, dict] = {}
        executable_steps = _flatten_steps(definition)
        for order, executable in enumerate(executable_steps):
            step_definition = executable.step
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
            if executable.branch_parent is not None:
                condition_run = session.scalar(
                    select(StepRun).where(
                        StepRun.run_id == run.id,
                        StepRun.step_id == executable.branch_parent,
                    )
                )
                decision = (
                    bool((condition_run.output or {}).get("decision")) if condition_run else False
                )
                if condition_run is None or condition_run.status != StepStatus.SUCCEEDED:
                    run.status = RunStatus.FAILED
                    run.error = "Conditional decision was not persisted."
                    run.error_category = "execution_failed"
                    run.finished_at = now()
                    _finish_job(session, claim, "failed")
                    session.commit()
                    return
                if decision is not executable.branch_value:
                    if step is None:
                        step = StepRun(
                            run_id=run.id,
                            step_id=step_definition.id,
                            step_order=order,
                            action=step_definition.action,
                            status=StepStatus.SKIPPED,
                            inputs={},
                            finished_at=now(),
                        )
                        session.add(step)
                        record_event(
                            session,
                            "step.skipped",
                            actor_kind="worker",
                            resource_type="step",
                            resource_id=step_definition.id,
                            workflow_id=run.workflow_id,
                            run_id=run.id,
                            outcome="skipped",
                            correlation_id=f"run:{run.id}",
                            details={"step_id": step_definition.id, "branch": "not_selected"},
                        )
                        session.commit()
                    continue
            if step is not None and step.status == StepStatus.SUCCEEDED:
                outputs[step.step_id] = step.output or {}
                outputs[executable.logical_id] = step.output or {}
                continue
            if step is not None and _recover_interrupted_attempt(session, claim, run, step):
                session.commit()
                return
            if step is None:
                inputs = {
                    key: _resolve_value(value, outputs, run.input or {})
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
            if _wait_for_approval_if_required(session, claim, run, step):
                session.commit()
                return
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
            record_event(
                session,
                "step.started",
                actor_kind="worker",
                resource_type="step",
                resource_id=step.id,
                workflow_id=run.workflow_id,
                run_id=run.id,
                outcome="running",
                correlation_id=f"run:{run.id}",
                details={"step_id": step.step_id, "action": step.action},
            )
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
                persisted_output = outcome.output or {}
                if executable.condition_root:
                    persisted_output = {
                        **persisted_output,
                        "decision": bool((persisted_output.get("value") or {}).get("result")),
                    }
                attempt.output = persisted_output
                attempt.finished_at = now()
                step.output = persisted_output
                step.status = transition_status(step.status, StepStatus.SUCCEEDED, entity="step")
                step.finished_at = now()
                outputs[step.step_id] = persisted_output
                outputs[executable.logical_id] = persisted_output
                if executable.condition_root:
                    decision = persisted_output["decision"]
                    for branch_order, branch_step in enumerate(
                        executable_steps[order + 1 :], start=order + 1
                    ):
                        if (
                            branch_step.branch_parent != executable.logical_id
                            or branch_step.branch_value is decision
                        ):
                            continue
                        existing = session.scalar(
                            select(StepRun).where(
                                StepRun.run_id == run.id,
                                StepRun.step_id == branch_step.step.id,
                            )
                        )
                        if existing is None:
                            skipped = StepRun(
                                run_id=run.id,
                                step_id=branch_step.step.id,
                                step_order=branch_order,
                                action=branch_step.step.action,
                                status=StepStatus.SKIPPED,
                                inputs={},
                                finished_at=now(),
                            )
                            session.add(skipped)
                            record_event(
                                session,
                                "step.skipped",
                                actor_kind="worker",
                                resource_type="step",
                                resource_id=branch_step.step.id,
                                workflow_id=run.workflow_id,
                                run_id=run.id,
                                outcome="skipped",
                                correlation_id=f"run:{run.id}",
                                details={"step_id": branch_step.step.id, "branch": "not_selected"},
                            )
                if executable.branch_last and executable.branch_parent:
                    condition_run = session.scalar(
                        select(StepRun).where(
                            StepRun.run_id == run.id,
                            StepRun.step_id == executable.branch_parent,
                        )
                    )
                    if condition_run is not None:
                        condition_output = dict(condition_run.output or {})
                        condition_output["condition"] = condition_output.get("value")
                        condition_output["value"] = persisted_output.get("value")
                        condition_output["selected_output"] = persisted_output
                        condition_run.output = condition_output
                        if condition_run.attempts:
                            condition_run.attempts[-1].output = condition_output
                        outputs[executable.branch_parent] = condition_output
                        record_event(
                            session,
                            "condition.branch_completed",
                            actor_kind="worker",
                            resource_type="step",
                            resource_id=condition_run.id,
                            workflow_id=run.workflow_id,
                            run_id=run.id,
                            outcome="then" if executable.branch_value else "else",
                            correlation_id=f"run:{run.id}",
                        )
                record_event(
                    session,
                    "step.succeeded",
                    actor_kind="worker",
                    resource_type="step",
                    resource_id=step.id,
                    workflow_id=run.workflow_id,
                    run_id=run.id,
                    outcome="succeeded",
                    correlation_id=f"run:{run.id}",
                    details={"step_id": step.step_id, "action": step.action},
                )
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
            record_event(
                session,
                "step.attempt_failed",
                actor_kind="worker",
                resource_type="step",
                resource_id=step.id,
                workflow_id=run.workflow_id,
                run_id=run.id,
                outcome=category,
                correlation_id=f"run:{run.id}",
                details={"step_id": step.step_id, "action": step.action},
            )
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
                record_event(
                    session,
                    "run.retry_scheduled",
                    actor_kind="worker",
                    resource_type="run",
                    resource_id=run.id,
                    workflow_id=run.workflow_id,
                    run_id=run.id,
                    outcome="retrying",
                    correlation_id=f"run:{run.id}",
                    details={"step_id": step.step_id, "category": category},
                )
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
            record_event(
                session,
                "run.failed",
                actor_kind="worker",
                resource_type="run",
                resource_id=run.id,
                workflow_id=run.workflow_id,
                run_id=run.id,
                outcome=category,
                correlation_id=f"run:{run.id}",
            )
            session.commit()
            return

        run.status = transition_status(run.status, RunStatus.SUCCEEDED, entity="run")
        run.output = outputs.get(definition.steps[-1].id)
        run.finished_at = now()
        _finish_job(session, claim, "succeeded")
        record_event(
            session,
            "run.succeeded",
            actor_kind="worker",
            resource_type="run",
            resource_id=run.id,
            workflow_id=run.workflow_id,
            run_id=run.id,
            outcome="succeeded",
            correlation_id=f"run:{run.id}",
        )
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
