"""Persist explicit skipped states for conditional workflow branches.

Revision ID: c8e2a47b5d19
Revises: f4c2e1a91b07
"""

from alembic import op

revision = "c8e2a47b5d19"
down_revision = "f4c2e1a91b07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("step_runs", recreate="auto") as batch:
        batch.drop_constraint("ck_step_run_status", type_="check")
        batch.create_check_constraint(
            "ck_step_run_status",
            "status IN ('queued', 'running', 'retrying', 'succeeded', 'failed', "
            "'cancelled', 'skipped')",
        )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled because it could discard conditional execution history."
    )
