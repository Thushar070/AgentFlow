"""Durable workflow definitions and synchronous execution records."""

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agentflow.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Store UTC values and restore timezone information on SQLite reads."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, _dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, _dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvalidTransition(ValueError):
    """Raised when a synchronous workflow state transition is not allowed."""


def transition_status(current: str, target: str, *, entity: str) -> str:
    allowed = {
        "run": {
            "queued": {"running", "cancelled"},
            "running": {"retrying", "cancel_requested", "succeeded", "failed", "cancelled"},
            "retrying": {"running", "cancel_requested", "failed", "cancelled"},
            "cancel_requested": {"cancelled", "succeeded", "failed"},
        },
        "step": {
            "queued": {"running", "cancelled"},
            "running": {"retrying", "succeeded", "failed", "cancelled"},
            "retrying": {"running", "failed", "cancelled"},
        },
    }
    if target not in allowed.get(entity, {}).get(current, set()):
        raise InvalidTransition(f"Cannot transition {entity} from {current} to {target}")
    return target


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    versions: Mapped[list["WorkflowVersion"]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        order_by="WorkflowVersion.version_number.desc()",
    )
    runs: Mapped[list["WorkflowRun"]] = relationship(
        back_populates="workflow", foreign_keys="WorkflowRun.workflow_id"
    )

    @property
    def current_version(self) -> "WorkflowVersion":
        if not self.versions:
            raise ValueError("Workflow has no versions")
        return max(self.versions, key=lambda item: item.version_number)

    @property
    def definition(self) -> dict:
        """Current definition retained as the compatible API response field."""
        return self.current_version.definition


class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "version_number", name="uq_workflow_version_number"),
        UniqueConstraint("id", "workflow_id", name="uq_workflow_version_id_workflow"),
        CheckConstraint("version_number >= 1", name="ck_workflow_version_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    workflow: Mapped[Workflow] = relationship(back_populates="versions")
    runs: Mapped[list["WorkflowRun"]] = relationship(
        back_populates="workflow_version", foreign_keys="WorkflowRun.workflow_version_id"
    )


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workflow_version_id", "workflow_id"],
            ["workflow_versions.id", "workflow_versions.workflow_id"],
            name="fk_run_version_workflow",
        ),
        UniqueConstraint("workflow_id", "idempotency_key", name="uq_run_idempotency_key"),
        CheckConstraint(
            "status IN ('queued', 'running', 'retrying', 'cancel_requested', "
            "'succeeded', 'failed', 'cancelled')",
            name="ck_run_status",
        ),
        CheckConstraint(
            "error_category IS NULL OR error_category IN "
            "('unknown_action', 'invalid_input', 'invalid_output', "
            "'permission_denied', 'execution_failed', 'action_timeout', "
            "'retries_exhausted', 'worker_interrupted', 'cancelled', 'reconciliation_required')",
            name="ck_run_error_category",
        ),
        Index("ix_workflow_runs_workflow_status", "workflow_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"), nullable=False, index=True)
    workflow_version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RunStatus.QUEUED)
    input: Mapped[dict | None] = mapped_column(JSON)
    output: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error: Mapped[str | None] = mapped_column(Text)
    error_category: Mapped[str | None] = mapped_column(String(40))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    workflow: Mapped[Workflow] = relationship(back_populates="runs", foreign_keys=[workflow_id])
    workflow_version: Mapped[WorkflowVersion] = relationship(
        back_populates="runs", foreign_keys=[workflow_version_id]
    )
    steps: Mapped[list["StepRun"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="StepRun.step_order"
    )
    job: Mapped["WorkflowJob | None"] = relationship(back_populates="run", uselist=False)


class StepRun(Base):
    __tablename__ = "step_runs"
    __table_args__ = (
        UniqueConstraint("run_id", "step_id", name="uq_step_run_id"),
        UniqueConstraint("run_id", "step_order", name="uq_step_run_order"),
        CheckConstraint("step_order >= 0", name="ck_step_order_nonnegative"),
        CheckConstraint(
            "status IN ('queued', 'running', 'retrying', 'succeeded', 'failed', 'cancelled')",
            name="ck_step_run_status",
        ),
        CheckConstraint(
            "error_category IS NULL OR error_category IN "
            "('unknown_action', 'invalid_input', 'invalid_output', "
            "'permission_denied', 'execution_failed', 'action_timeout', "
            "'retries_exhausted', 'worker_interrupted', 'cancelled', 'reconciliation_required')",
            name="ck_step_error_category",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False, index=True)
    step_id: Mapped[str] = mapped_column(String(80), nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=StepStatus.QUEUED)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False)
    output: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    error_category: Mapped[str | None] = mapped_column(String(40))
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    run: Mapped[WorkflowRun] = relationship(back_populates="steps")
    attempts: Mapped[list["StepAttempt"]] = relationship(
        back_populates="step_run",
        cascade="all, delete-orphan",
        order_by="StepAttempt.attempt_number",
    )


class WorkflowJob(Base):
    """Durable database queue row, committed atomically with its workflow run."""

    __tablename__ = "workflow_jobs"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_workflow_job_run"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_workflow_job_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_workflow_job_attempts"),
        CheckConstraint("lease_generation >= 0", name="ck_workflow_job_generation"),
        Index("ix_workflow_jobs_available", "status", "available_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    locked_by: Mapped[str | None] = mapped_column(String(120))
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    run: Mapped[WorkflowRun] = relationship(back_populates="job")


class StepAttempt(Base):
    """One persisted invocation attempt; prior outputs survive retries and recovery."""

    __tablename__ = "step_attempts"
    __table_args__ = (
        UniqueConstraint("step_run_id", "attempt_number", name="uq_step_attempt_number"),
        CheckConstraint("attempt_number >= 1", name="ck_step_attempt_positive"),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'timed_out', 'cancelled', 'interrupted')",
            name="ck_step_attempt_status",
        ),
        CheckConstraint(
            "error_category IS NULL OR error_category IN "
            "('unknown_action', 'invalid_input', 'invalid_output', 'permission_denied', "
            "'execution_failed', 'action_timeout', 'retries_exhausted', 'worker_interrupted', "
            "'cancelled', 'reconciliation_required')",
            name="ck_step_attempt_error_category",
        ),
        Index("ix_step_attempts_step", "step_run_id", "attempt_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    step_run_id: Mapped[int] = mapped_column(
        ForeignKey("step_runs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    output: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    error_category: Mapped[str | None] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    step_run: Mapped[StepRun] = relationship(back_populates="attempts")
