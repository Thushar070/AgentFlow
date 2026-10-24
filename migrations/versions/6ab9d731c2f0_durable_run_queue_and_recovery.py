"""Add a durable database queue and persisted action attempts.

Revision ID: 6ab9d731c2f0
Revises: d9156bc2a041
"""

import sqlalchemy as sa
from alembic import op

revision = "6ab9d731c2f0"
down_revision = "d9156bc2a041"
branch_labels = None
depends_on = None

RUN_STATUSES = (
    "'queued', 'running', 'retrying', 'cancel_requested', 'succeeded', 'failed', 'cancelled'"
)
STEP_STATUSES = "'queued', 'running', 'retrying', 'succeeded', 'failed', 'cancelled'"
ERROR_CATEGORIES = (
    "'unknown_action', 'invalid_input', 'invalid_output', 'permission_denied', "
    "'execution_failed', 'action_timeout', 'retries_exhausted', 'worker_interrupted', "
    "'cancelled', 'reconciliation_required'"
)


def upgrade() -> None:
    op.add_column("workflow_runs", sa.Column("idempotency_key", sa.String(128), nullable=True))
    op.add_column(
        "workflow_runs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True)
    )
    with op.batch_alter_table("workflow_runs", recreate="auto") as batch:
        batch.drop_constraint("ck_run_status", type_="check")
        batch.drop_constraint("ck_run_error_category", type_="check")
        batch.create_check_constraint("ck_run_status", f"status IN ({RUN_STATUSES})")
        batch.create_check_constraint(
            "ck_run_error_category",
            f"error_category IS NULL OR error_category IN ({ERROR_CATEGORIES})",
        )
        batch.create_unique_constraint("uq_run_idempotency_key", ["workflow_id", "idempotency_key"])
    with op.batch_alter_table("step_runs", recreate="auto") as batch:
        batch.drop_constraint("ck_step_run_status", type_="check")
        batch.drop_constraint("ck_step_error_category", type_="check")
        batch.create_check_constraint("ck_step_run_status", f"status IN ({STEP_STATUSES})")
        batch.create_check_constraint(
            "ck_step_error_category",
            f"error_category IS NULL OR error_category IN ({ERROR_CATEGORIES})",
        )

    op.create_table(
        "workflow_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("locked_by", sa.String(120)),
        sa.Column("lease_generation", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_workflow_job_run"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_workflow_job_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_workflow_job_attempts"),
        sa.CheckConstraint("lease_generation >= 0", name="ck_workflow_job_generation"),
    )
    op.create_index("ix_workflow_jobs_available", "workflow_jobs", ["status", "available_at"])
    op.execute(
        "INSERT INTO workflow_jobs "
        "(run_id,status,attempts,available_at,lease_generation,created_at,updated_at) "
        "SELECT id,'queued',0,COALESCE(created_at,CURRENT_TIMESTAMP),0,"
        "COALESCE(created_at,CURRENT_TIMESTAMP),COALESCE(created_at,CURRENT_TIMESTAMP) "
        "FROM workflow_runs WHERE status='running'"
    )
    op.execute("UPDATE workflow_runs SET status='queued', started_at=NULL WHERE status='running'")
    op.execute(
        "UPDATE step_runs SET status='queued', started_at=NULL, finished_at=NULL "
        "WHERE status='running'"
    )

    op.create_table(
        "step_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "step_run_id",
            sa.Integer(),
            sa.ForeignKey("step_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("output", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("error_category", sa.String(40)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("step_run_id", "attempt_number", name="uq_step_attempt_number"),
        sa.CheckConstraint("attempt_number >= 1", name="ck_step_attempt_positive"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'timed_out', 'cancelled', 'interrupted')",
            name="ck_step_attempt_status",
        ),
        sa.CheckConstraint(
            f"error_category IS NULL OR error_category IN ({ERROR_CATEGORIES})",
            name="ck_step_attempt_error_category",
        ),
    )
    op.create_index("ix_step_attempts_step", "step_attempts", ["step_run_id", "attempt_number"])
    op.execute(
        "INSERT INTO step_attempts "
        "(step_run_id,attempt_number,status,output,error,error_category,started_at,finished_at) "
        "SELECT id,1,status,output,error,error_category,"
        "COALESCE(started_at,finished_at,CURRENT_TIMESTAMP),finished_at FROM step_runs "
        "WHERE status IN ('succeeded','failed')"
    )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is intentionally disabled because queue and attempt history cannot be "
        "represented by the synchronous Phase 3 schema. Restore a verified database backup."
    )
