"""Persist classified action failures on runs and steps.

Revision ID: d9156bc2a041
Revises: 70b823ceda15
"""

import sqlalchemy as sa
from alembic import op

revision = "d9156bc2a041"
down_revision = "70b823ceda15"
branch_labels = None
depends_on = None

ERROR_CATEGORIES = (
    "'unknown_action', 'invalid_input', 'invalid_output', 'permission_denied', 'execution_failed'"
)


def upgrade() -> None:
    op.add_column("workflow_runs", sa.Column("error_category", sa.String(40), nullable=True))
    op.add_column("step_runs", sa.Column("error_category", sa.String(40), nullable=True))
    with op.batch_alter_table("workflow_runs", recreate="auto") as batch:
        batch.create_check_constraint(
            "ck_run_error_category",
            f"error_category IS NULL OR error_category IN ({ERROR_CATEGORIES})",
        )
    with op.batch_alter_table("step_runs", recreate="auto") as batch:
        batch.create_check_constraint(
            "ck_step_error_category",
            f"error_category IS NULL OR error_category IN ({ERROR_CATEGORIES})",
        )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is intentionally disabled to preserve classified execution history. "
        "Restore a verified database backup instead."
    )
