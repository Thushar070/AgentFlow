"""Add execution inputs, outputs, and creation timestamps.

Revision ID: 70b823ceda15
Revises: 0432ed0b773b
"""

import sqlalchemy as sa
from alembic import op

revision = "70b823ceda15"
down_revision = "0432ed0b773b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    columns = {column["name"] for column in sa.inspect(connection).get_columns("workflow_runs")}
    if {"input", "output", "created_at"}.issubset(columns):
        return
    if "created_at" in columns or "input" in columns or "output" in columns:
        raise RuntimeError(
            "workflow_runs has a partial Phase 2 execution field set; inspect the schema "
            "before retrying."
        )
    op.add_column("workflow_runs", sa.Column("input", sa.JSON(), nullable=True))
    op.add_column("workflow_runs", sa.Column("output", sa.JSON(), nullable=True))
    op.add_column(
        "workflow_runs",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    connection.exec_driver_sql(
        "UPDATE workflow_runs SET created_at=COALESCE(started_at, finished_at, CURRENT_TIMESTAMP)"
    )
    with op.batch_alter_table("workflow_runs", recreate="auto") as batch:
        batch.alter_column("created_at", existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled because it would discard persisted execution input/output data. "
        "Restore a verified database backup instead."
    )
