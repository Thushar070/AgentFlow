"""Persist safe error categories for structured and outbound actions.

Revision ID: d142f6a8c903
Revises: c8e2a47b5d19
"""

from alembic import op

revision = "d142f6a8c903"
down_revision = "c8e2a47b5d19"
branch_labels = None
depends_on = None

_CATEGORIES = (
    "'unknown_action', 'invalid_input', 'invalid_output', 'permission_denied', "
    "'execution_failed', 'action_timeout', 'retries_exhausted', 'worker_interrupted', "
    "'cancelled', 'reconciliation_required', 'approval_rejected', 'field_not_found', "
    "'invalid_json', 'destination_rejected', 'http_timeout', 'http_connection_error', "
    "'http_status_error', 'size_limit', 'invalid_comparison'"
)


def upgrade() -> None:
    for table, constraint in (
        ("workflow_runs", "ck_run_error_category"),
        ("step_runs", "ck_step_error_category"),
        ("step_attempts", "ck_step_attempt_error_category"),
    ):
        with op.batch_alter_table(table, recreate="auto") as batch:
            batch.drop_constraint(constraint, type_="check")
            batch.create_check_constraint(
                constraint, f"error_category IS NULL OR error_category IN ({_CATEGORIES})"
            )


def downgrade() -> None:
    raise RuntimeError("Downgrade is disabled because it could discard action failure history.")
