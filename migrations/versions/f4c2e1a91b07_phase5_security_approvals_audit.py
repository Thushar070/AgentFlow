"""Add identities, sessions, approvals, and structured audit events.

Revision ID: f4c2e1a91b07
Revises: 6ab9d731c2f0
"""

import sqlalchemy as sa
from alembic import op

revision = "f4c2e1a91b07"
down_revision = "6ab9d731c2f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False),
        sa.Column("username_normalized", sa.String(80), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("email_normalized", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("username_normalized", name="uq_users_username_normalized"),
        sa.UniqueConstraint("email_normalized", name="uq_users_email_normalized"),
        sa.CheckConstraint("role IN ('admin', 'operator', 'approver')", name="ck_user_role"),
    )
    with op.batch_alter_table("workflows", recreate="auto") as batch:
        batch.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_workflows_owner_id_users", "users", ["owner_id"], ["id"], ondelete="SET NULL"
        )
    op.create_index("ix_workflows_owner_id", "workflows", ["owner_id"])
    with op.batch_alter_table("workflow_runs", recreate="auto") as batch:
        batch.add_column(sa.Column("created_by_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_workflow_runs_created_by_users",
            "users",
            ["created_by_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.drop_constraint("ck_run_status", type_="check")
        batch.drop_constraint("ck_run_error_category", type_="check")
        batch.create_check_constraint(
            "ck_run_status",
            "status IN ('queued', 'running', 'retrying', 'cancel_requested', 'awaiting_approval', "
            "'succeeded', 'failed', 'cancelled')",
        )
        batch.create_check_constraint(
            "ck_run_error_category",
            "error_category IS NULL OR error_category IN ('unknown_action', 'invalid_input', "
            "'invalid_output', 'permission_denied', 'execution_failed', 'action_timeout', "
            "'retries_exhausted', 'worker_interrupted', 'cancelled', 'reconciliation_required', "
            "'approval_rejected')",
        )
    with op.batch_alter_table("step_runs", recreate="auto") as batch:
        batch.drop_constraint("ck_step_error_category", type_="check")
        batch.create_check_constraint(
            "ck_step_error_category",
            "error_category IS NULL OR error_category IN ('unknown_action', 'invalid_input', "
            "'invalid_output', 'permission_denied', 'execution_failed', 'action_timeout', "
            "'retries_exhausted', 'worker_interrupted', 'cancelled', 'reconciliation_required', "
            "'approval_rejected')",
        )
    with op.batch_alter_table("step_attempts", recreate="auto") as batch:
        batch.drop_constraint("ck_step_attempt_error_category", type_="check")
        batch.create_check_constraint(
            "ck_step_attempt_error_category",
            "error_category IS NULL OR error_category IN ('unknown_action', 'invalid_input', "
            "'invalid_output', 'permission_denied', 'execution_failed', 'action_timeout', "
            "'retries_exhausted', 'worker_interrupted', 'cancelled', 'reconciliation_required', "
            "'approval_rejected')",
        )
    with op.batch_alter_table("workflow_jobs", recreate="auto") as batch:
        batch.drop_constraint("ck_workflow_job_status", type_="check")
        batch.create_check_constraint(
            "ck_workflow_job_status",
            "status IN ('queued', 'running', 'awaiting_approval', 'succeeded', 'failed', "
            "'cancelled')",
        )

    op.create_table(
        "user_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_sessions_user_expiry", "user_sessions", ["user_id", "expires_at"])
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_login_attempts_key_time", "login_attempts", ["key_hash", "created_at"])
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "step_run_id",
            sa.Integer(),
            sa.ForeignKey("step_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_version_id",
            sa.Integer(),
            sa.ForeignKey("workflow_versions.id"),
            nullable=False,
        ),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("inputs_hash", sa.String(64), nullable=False),
        sa.Column("requester_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("decided_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reason", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'stale')", name="ck_approval_status"
        ),
    )
    op.create_index(
        "ix_approval_requests_status_created", "approval_requests", ["status", "created_at"]
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("actor_kind", sa.String(20), nullable=False),
        sa.Column("resource_type", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.String(80)),
        sa.Column("workflow_id", sa.Integer(), sa.ForeignKey("workflows.id", ondelete="SET NULL")),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("workflow_runs.id", ondelete="SET NULL")),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("correlation_id", sa.String(80), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_created_id", "audit_events", ["created_at", "id"])
    op.create_index("ix_audit_events_event_type_id", "audit_events", ["event_type", "id"])
    op.create_index("ix_audit_events_run_id", "audit_events", ["run_id", "id"])


def downgrade() -> None:
    raise RuntimeError("Downgrade is disabled to preserve authentication and audit history.")
