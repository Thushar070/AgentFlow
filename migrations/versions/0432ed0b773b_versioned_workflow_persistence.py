"""Normalize workflow versions and strengthen SQLite execution integrity.

Revision ID: 0432ed0b773b
Revises:
Create Date: 2026-10-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0432ed0b773b"
down_revision = None
branch_labels = None
depends_on = None


def _create_fresh_schema() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(1000)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_workflows_name", "workflows", ["name"])
    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name="fk_workflow_versions_workflow",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workflow_id", "version_number", name="uq_workflow_version_number"),
        sa.UniqueConstraint("id", "workflow_id", name="uq_workflow_version_id_workflow"),
        sa.CheckConstraint("version_number >= 1", name="ck_workflow_version_positive"),
    )
    op.create_index("ix_workflow_versions_workflow_id", "workflow_versions", ["workflow_id"])
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("workflow_version_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflows.id"], name="fk_workflow_runs_workflow"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_version_id", "workflow_id"],
            ["workflow_versions.id", "workflow_versions.workflow_id"],
            name="fk_run_version_workflow",
        ),
        sa.CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="ck_run_status"),
    )
    op.create_index("ix_workflow_runs_workflow_id", "workflow_runs", ["workflow_id"])
    op.create_index("ix_workflow_runs_workflow_status", "workflow_runs", ["workflow_id", "status"])
    op.create_table(
        "step_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("step_id", sa.String(80), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], name="fk_step_runs_run"),
        sa.UniqueConstraint("run_id", "step_id", name="uq_step_run_id"),
        sa.UniqueConstraint("run_id", "step_order", name="uq_step_run_order"),
        sa.CheckConstraint("step_order >= 0", name="ck_step_order_nonnegative"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')", name="ck_step_run_status"
        ),
    )
    op.create_index("ix_step_runs_run_id", "step_runs", ["run_id"])


def _upgrade_phase1_schema(connection) -> None:
    table_names = set(sa.inspect(connection).get_table_names())
    expected = {"workflows", "workflow_runs", "step_runs"}
    if table_names.intersection(expected) != expected:
        raise RuntimeError(
            "Cannot migrate partial/unknown AgentFlow schema; expected all legacy tables "
            "workflows, workflow_runs, and step_runs. No data was intentionally changed."
        )
    columns = {
        table: {column["name"] for column in sa.inspect(connection).get_columns(table)}
        for table in expected
    }
    if (
        "definition" not in columns["workflows"]
        or "workflow_version_id" in columns["workflow_runs"]
    ):
        raise RuntimeError(
            "Existing schema is not the supported Phase 1 layout. Inspect it and add a "
            "reviewed migration."
        )
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"Legacy database contains foreign-key orphans: {violations[:5]}")

    allowed_run_statuses = {"queued", "running", "succeeded", "failed"}
    run_statuses = {
        row[0] for row in connection.exec_driver_sql("SELECT DISTINCT status FROM workflow_runs")
    }
    step_statuses = {
        row[0] for row in connection.exec_driver_sql("SELECT DISTINCT status FROM step_runs")
    }
    if not run_statuses <= allowed_run_statuses or not step_statuses <= allowed_run_statuses:
        raise RuntimeError(
            f"Legacy database contains unsupported statuses: runs={run_statuses}, "
            f"steps={step_statuses}; no status rows were changed."
        )
    duplicate_ids = connection.exec_driver_sql(
        "SELECT run_id, step_id FROM step_runs GROUP BY run_id, step_id HAVING COUNT(*) > 1 LIMIT 1"
    ).first()
    if duplicate_ids:
        raise RuntimeError(f"Legacy database has duplicate step ids in one run: {duplicate_ids}")

    if "workflow_versions" not in table_names:
        op.create_table(
            "workflow_versions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("workflow_id", sa.Integer(), nullable=False),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("definition", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["workflow_id"],
                ["workflows.id"],
                name="fk_workflow_versions_workflow",
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint("workflow_id", "version_number", name="uq_workflow_version_number"),
            sa.UniqueConstraint("id", "workflow_id", name="uq_workflow_version_id_workflow"),
            sa.CheckConstraint("version_number >= 1", name="ck_workflow_version_positive"),
        )
        op.create_index("ix_workflow_versions_workflow_id", "workflow_versions", ["workflow_id"])
    version_count = connection.exec_driver_sql(
        "SELECT COUNT(*) FROM workflow_versions"
    ).scalar_one()
    if version_count:
        raise RuntimeError(
            "An incomplete version table contains rows alongside the legacy definition column. "
            "Restore a verified pre-migration backup to a separate file and retry; "
            "no rows were deleted."
        )
    else:
        version_columns = {
            column["name"] for column in sa.inspect(connection).get_columns("workflow_versions")
        }
        if (
            not {"id", "workflow_id", "version_number", "definition", "created_at"}
            <= version_columns
        ):
            raise RuntimeError(
                "An empty workflow_versions table has an unknown schema; inspect it "
                "before retrying."
            )
        indexes = {
            index["name"] for index in sa.inspect(connection).get_indexes("workflow_versions")
        }
        if "ix_workflow_versions_workflow_id" not in indexes:
            op.create_index(
                "ix_workflow_versions_workflow_id", "workflow_versions", ["workflow_id"]
            )
    connection.exec_driver_sql(
        "INSERT INTO workflow_versions (workflow_id, version_number, definition, created_at) "
        "SELECT id, 1, definition, created_at FROM workflows ORDER BY id"
    )

    op.add_column("workflow_runs", sa.Column("workflow_version_id", sa.Integer(), nullable=True))
    connection.exec_driver_sql(
        "UPDATE workflow_runs SET workflow_version_id = ("
        "SELECT id FROM workflow_versions v WHERE v.workflow_id = workflow_runs.workflow_id "
        "AND v.version_number = 1)"
    )
    # A queued/running Phase 1 row means the process stopped before a terminal result.
    # Preserve the record while giving it an explicit terminal explanation.
    connection.exec_driver_sql(
        "UPDATE workflow_runs SET status='failed', "
        "error=COALESCE(error, 'Legacy execution was interrupted before migration'), "
        "finished_at=COALESCE(finished_at, CURRENT_TIMESTAMP) "
        "WHERE status IN ('queued', 'running')"
    )
    connection.exec_driver_sql(
        "UPDATE step_runs SET status='failed', "
        "error=COALESCE(error, 'Legacy step was interrupted before migration'), "
        "finished_at=COALESCE(finished_at, CURRENT_TIMESTAMP) "
        "WHERE status IN ('queued', 'running')"
    )
    with op.batch_alter_table("workflow_runs", recreate="always") as batch:
        batch.alter_column("workflow_version_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key(
            "fk_run_version_workflow",
            "workflow_versions",
            ["workflow_version_id", "workflow_id"],
            ["id", "workflow_id"],
        )
        batch.create_check_constraint(
            "ck_run_status", "status IN ('running', 'succeeded', 'failed')"
        )
    op.create_index("ix_workflow_runs_workflow_status", "workflow_runs", ["workflow_id", "status"])

    rows = connection.exec_driver_sql(
        "SELECT id, run_id FROM step_runs ORDER BY run_id, id"
    ).fetchall()
    op.add_column("step_runs", sa.Column("step_order", sa.Integer(), nullable=True))
    next_order = {}
    for row_id, run_id in rows:
        order = next_order.get(run_id, 0)
        connection.exec_driver_sql("UPDATE step_runs SET step_order=? WHERE id=?", (order, row_id))
        next_order[run_id] = order + 1
    with op.batch_alter_table("step_runs", recreate="always") as batch:
        batch.alter_column("step_order", existing_type=sa.Integer(), nullable=False)
        batch.create_unique_constraint("uq_step_run_id", ["run_id", "step_id"])
        batch.create_unique_constraint("uq_step_run_order", ["run_id", "step_order"])
        batch.create_check_constraint("ck_step_order_nonnegative", "step_order >= 0")
        batch.create_check_constraint(
            "ck_step_run_status", "status IN ('running', 'succeeded', 'failed')"
        )

    op.add_column("workflows", sa.Column("description", sa.String(1000), nullable=True))
    op.add_column(
        "workflows",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    connection.exec_driver_sql("UPDATE workflows SET updated_at=created_at")
    with op.batch_alter_table("workflows", recreate="always") as batch:
        batch.alter_column("updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.drop_column("definition")
    op.create_index("ix_workflows_name", "workflows", ["name"])
    op.create_index("ix_workflow_runs_workflow_id", "workflow_runs", ["workflow_id"])
    op.create_index("ix_step_runs_run_id", "step_runs", ["run_id"])


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        raise RuntimeError("Phase 2 migration is currently verified for SQLite only")
    tables = set(sa.inspect(connection).get_table_names()) - {"alembic_version"}
    if not tables:
        _create_fresh_schema()
    elif tables == {"workflows", "workflow_versions", "workflow_runs", "step_runs"}:
        columns = {
            table: {column["name"] for column in sa.inspect(connection).get_columns(table)}
            for table in tables
        }
        if (
            "definition" not in columns["workflows"]
            and "workflow_version_id" in columns["workflow_runs"]
            and "step_order" in columns["step_runs"]
        ):
            # A completed schema whose version stamp was not committed can be stamped safely.
            return
        _upgrade_phase1_schema(connection)
    else:
        _upgrade_phase1_schema(connection)


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is intentionally disabled because it would discard immutable workflow versions. "
        "Restore a verified database backup instead."
    )
