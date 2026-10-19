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
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StepStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class InvalidTransition(ValueError):
    """Raised when a synchronous workflow state transition is not allowed."""


def transition_status(current: str, target: str, *, entity: str) -> str:
    allowed = {"running": {"succeeded", "failed"}}
    if target not in allowed.get(current, set()):
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
        CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="ck_run_status"),
        CheckConstraint(
            "error_category IS NULL OR error_category IN "
            "('unknown_action', 'invalid_input', 'invalid_output', "
            "'permission_denied', 'execution_failed')",
            name="ck_run_error_category",
        ),
        Index("ix_workflow_runs_workflow_status", "workflow_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"), nullable=False, index=True)
    workflow_version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RunStatus.RUNNING)
    input: Mapped[dict | None] = mapped_column(JSON)
    output: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error: Mapped[str | None] = mapped_column(Text)
    error_category: Mapped[str | None] = mapped_column(String(40))
    workflow: Mapped[Workflow] = relationship(back_populates="runs", foreign_keys=[workflow_id])
    workflow_version: Mapped[WorkflowVersion] = relationship(
        back_populates="runs", foreign_keys=[workflow_version_id]
    )
    steps: Mapped[list["StepRun"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="StepRun.step_order"
    )


class StepRun(Base):
    __tablename__ = "step_runs"
    __table_args__ = (
        UniqueConstraint("run_id", "step_id", name="uq_step_run_id"),
        UniqueConstraint("run_id", "step_order", name="uq_step_run_order"),
        CheckConstraint("step_order >= 0", name="ck_step_order_nonnegative"),
        CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="ck_step_run_status"),
        CheckConstraint(
            "error_category IS NULL OR error_category IN "
            "('unknown_action', 'invalid_input', 'invalid_output', "
            "'permission_denied', 'execution_failed')",
            name="ck_step_error_category",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False, index=True)
    step_id: Mapped[str] = mapped_column(String(80), nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=StepStatus.RUNNING)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False)
    output: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    error_category: Mapped[str | None] = mapped_column(String(40))
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    run: Mapped[WorkflowRun] = relationship(back_populates="steps")
