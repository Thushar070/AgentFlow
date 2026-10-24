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
    AWAITING_APPROVAL = "awaiting_approval"
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
    SKIPPED = "skipped"


class InvalidTransition(ValueError):
    """Raised when a synchronous workflow state transition is not allowed."""


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username_normalized", name="uq_users_username_normalized"),
        UniqueConstraint("email_normalized", name="uq_users_email_normalized"),
        CheckConstraint("role IN ('admin', 'operator', 'approver')", name="ck_user_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), nullable=False)
    username_normalized: Mapped[str] = mapped_column(String(80), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(254), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="operator")
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (Index("ix_user_sessions_user_expiry", "user_id", "expires_at"),)

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_key_time", "key_hash", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    succeeded: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


def transition_status(current: str, target: str, *, entity: str) -> str:
    allowed = {
        "run": {
            "queued": {"running", "cancelled"},
            "running": {
                "retrying",
                "cancel_requested",
                "awaiting_approval",
                "succeeded",
                "failed",
                "cancelled",
            },
            "retrying": {"running", "cancel_requested", "failed", "cancelled"},
            "cancel_requested": {"cancelled", "succeeded", "failed"},
            "awaiting_approval": {"queued", "cancelled", "failed"},
        },
        "step": {
            "queued": {"running", "failed", "cancelled"},
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
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
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
            "status IN ('queued', 'running', 'retrying', 'cancel_requested', 'awaiting_approval', "
            "'succeeded', 'failed', 'cancelled')",
            name="ck_run_status",
        ),
        CheckConstraint(
            "error_category IS NULL OR error_category IN "
            "('unknown_action', 'invalid_input', 'invalid_output', "
            "'permission_denied', 'execution_failed', 'action_timeout', "
            "'retries_exhausted', 'worker_interrupted', 'cancelled', 'reconciliation_required', "
            "'approval_rejected', 'field_not_found', 'invalid_json', 'destination_rejected', "
            "'http_timeout', 'http_connection_error', 'http_status_error', 'size_limit', "
            "'invalid_comparison')",
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
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
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
            "status IN ('queued', 'running', 'retrying', 'succeeded', 'failed', "
            "'cancelled', 'skipped')",
            name="ck_step_run_status",
        ),
        CheckConstraint(
            "error_category IS NULL OR error_category IN "
            "('unknown_action', 'invalid_input', 'invalid_output', "
            "'permission_denied', 'execution_failed', 'action_timeout', "
            "'retries_exhausted', 'worker_interrupted', 'cancelled', 'reconciliation_required', "
            "'approval_rejected', 'field_not_found', 'invalid_json', 'destination_rejected', "
            "'http_timeout', 'http_connection_error', 'http_status_error', 'size_limit', "
            "'invalid_comparison')",
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
            "status IN ('queued', 'running', 'awaiting_approval', 'succeeded', 'failed', "
            "'cancelled')",
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
            "'cancelled', 'reconciliation_required', 'approval_rejected', 'field_not_found', "
            "'invalid_json', 'destination_rejected', 'http_timeout', 'http_connection_error', "
            "'http_status_error', 'size_limit', 'invalid_comparison')",
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


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'stale')", name="ck_approval_status"
        ),
        Index("ix_approval_requests_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    step_run_id: Mapped[int] = mapped_column(
        ForeignKey("step_runs.id", ondelete="CASCADE"), nullable=False
    )
    workflow_version_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_versions.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    requester_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reason: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_created_id", "created_at", "id"),
        Index("ix_audit_events_event_type_id", "event_type", "id"),
        Index("ix_audit_events_run_id", "run_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(80))
    workflow_id: Mapped[int | None] = mapped_column(ForeignKey("workflows.id", ondelete="SET NULL"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("workflow_runs.id", ondelete="SET NULL"))
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(80), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
