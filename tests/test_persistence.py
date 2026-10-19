import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agentflow.execution import execute_workflow
from agentflow.models import (
    Base,
    InvalidTransition,
    RunStatus,
    Workflow,
    WorkflowRun,
    WorkflowVersion,
    transition_status,
)

ROOT = Path(__file__).resolve().parents[1]
REVISION = "70b823ceda15"


def run_migration(database_url: str):
    env = {**os.environ, "DATABASE_URL": database_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path}"


def test_fresh_database_migration_is_repeatable_and_revisioned(tmp_path):
    database = tmp_path / "fresh.db"
    url = sqlite_url(database)
    run_migration(url)
    run_migration(url)
    with sqlite3.connect(database) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert revision == REVISION
        assert {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        } >= {"workflows", "workflow_versions", "workflow_runs", "step_runs"}
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert "workflow_version_id" in {
            row[1] for row in connection.execute("PRAGMA table_info(workflow_runs)")
        }


def test_phase1_migration_preserves_workflows_runs_and_steps(tmp_path):
    database = tmp_path / "phase1.db"
    definition = {
        "version": 1,
        "name": "Legacy flow",
        "steps": [
            {"id": "source", "action": "set_value", "inputs": {"value": "kept"}},
            {"id": "upper", "action": "uppercase", "inputs": {"value": {"$ref": "source"}}},
        ],
    }
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE workflows (
                id INTEGER PRIMARY KEY, name VARCHAR(120) NOT NULL,
                definition JSON NOT NULL, created_at DATETIME NOT NULL
            );
            CREATE TABLE workflow_runs (
                id INTEGER PRIMARY KEY, workflow_id INTEGER NOT NULL REFERENCES workflows(id),
                status VARCHAR(20) NOT NULL, started_at DATETIME, finished_at DATETIME, error TEXT
            );
            CREATE TABLE step_runs (
                id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES workflow_runs(id),
                step_id VARCHAR(80) NOT NULL, action VARCHAR(80) NOT NULL,
                status VARCHAR(20) NOT NULL, inputs JSON NOT NULL, output JSON,
                error TEXT, started_at DATETIME, finished_at DATETIME
            );
            """
        )
        connection.execute(
            "INSERT INTO workflows VALUES (?, ?, ?, ?)",
            (7, "Legacy flow", json.dumps(definition), "2025-04-01 10:00:00"),
        )
        connection.execute(
            "INSERT INTO workflow_runs VALUES (?, ?, ?, ?, ?, ?)",
            (11, 7, "succeeded", "2025-04-01 10:01:00", "2025-04-01 10:01:01", None),
        )
        connection.execute(
            "INSERT INTO workflow_runs VALUES (?, ?, ?, ?, ?, ?)",
            (12, 7, "running", "2025-04-01 10:02:00", None, None),
        )
        connection.executemany(
            "INSERT INTO step_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    21,
                    11,
                    "source",
                    "set_value",
                    "succeeded",
                    '{"value":"kept"}',
                    '{"value":"kept"}',
                    None,
                    "2025-04-01 10:01:00",
                    "2025-04-01 10:01:00",
                ),
                (
                    22,
                    11,
                    "upper",
                    "uppercase",
                    "succeeded",
                    '{"value":"kept"}',
                    '{"value":"KEPT"}',
                    None,
                    "2025-04-01 10:01:00",
                    "2025-04-01 10:01:01",
                ),
                (
                    23,
                    12,
                    "interrupted",
                    "set_value",
                    "running",
                    '{"value":"in flight"}',
                    None,
                    None,
                    "2025-04-01 10:02:00",
                    None,
                ),
            ],
        )

    run_migration(sqlite_url(database))
    run_migration(sqlite_url(database))
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == REVISION
        )
        assert connection.execute("SELECT id, name FROM workflows").fetchall() == [
            (7, "Legacy flow")
        ]
        stored_definition = json.loads(
            connection.execute(
                "SELECT definition FROM workflow_versions WHERE workflow_id=7 AND version_number=1"
            ).fetchone()[0]
        )
        assert stored_definition == definition
        assert connection.execute(
            "SELECT id, workflow_id, workflow_version_id, status FROM workflow_runs"
        ).fetchall() == [(11, 7, 1, "succeeded"), (12, 7, 1, "failed")]
        assert "created_at" in {
            row[1] for row in connection.execute("PRAGMA table_info(workflow_runs)")
        }
        assert (
            connection.execute("SELECT created_at FROM workflow_runs WHERE id=11").fetchone()[0]
            == "2025-04-01 10:01:00"
        )
        assert (
            "interrupted before migration"
            in connection.execute("SELECT error FROM workflow_runs WHERE id=12").fetchone()[0]
        )
        assert connection.execute(
            "SELECT id, step_id, step_order, status, output FROM step_runs "
            "ORDER BY run_id, step_order"
        ).fetchall() == [
            (21, "source", 0, "succeeded", '{"value":"kept"}'),
            (22, "upper", 1, "succeeded", '{"value":"KEPT"}'),
            (23, "interrupted", 0, "failed", None),
        ]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'models.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db_session:
        yield db_session
    engine.dispose()


def make_workflow(session, name="First"):
    workflow = Workflow(name=name, description=None)
    session.add(workflow)
    session.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id,
        version_number=1,
        definition={
            "version": 1,
            "name": name,
            "steps": [{"id": "one", "action": "set_value", "inputs": {"value": name}}],
        },
    )
    session.add(version)
    session.commit()
    return workflow, version


def test_model_relationships_and_utc_timestamps(session):
    workflow, version = make_workflow(session)
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        status=RunStatus.SUCCEEDED,
        created_at=datetime.now(UTC),
        output={"value": "First"},
    )
    session.add(run)
    session.commit()
    assert run.workflow == workflow
    assert run.workflow_version == version
    assert workflow.current_version == version
    assert run.created_at.tzinfo is UTC


def test_foreign_key_and_unique_constraints_reject_invalid_records(session):
    workflow1, version1 = make_workflow(session, "one")
    workflow2, version2 = make_workflow(session, "two")
    session.add(
        WorkflowRun(
            workflow_id=workflow1.id,
            workflow_version_id=version2.id,
            status="running",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    session.add(WorkflowVersion(workflow_id=workflow1.id, version_number=1, definition={}))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    session.add(
        WorkflowRun(
            workflow_id=workflow1.id,
            workflow_version_id=version1.id,
            status="queued",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    run = WorkflowRun(workflow_id=workflow1.id, workflow_version_id=version1.id, status="running")
    session.add(run)
    session.flush()
    from agentflow.models import StepRun

    session.add_all(
        [
            StepRun(
                run_id=run.id,
                step_id="step",
                step_order=0,
                action="set_value",
                status="succeeded",
                inputs={},
            ),
            StepRun(
                run_id=run.id,
                step_id="step",
                step_order=1,
                action="set_value",
                status="succeeded",
                inputs={},
            ),
        ]
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_state_transition_contract_and_atomic_failed_commit(session):
    workflow, version = make_workflow(session)
    assert transition_status("running", RunStatus.SUCCEEDED, entity="run") == "succeeded"
    with pytest.raises(InvalidTransition):
        transition_status("succeeded", "running", entity="run")
    with pytest.raises(InvalidTransition):
        transition_status("queued", "running", entity="step")

    def fail_commit(_session):
        raise RuntimeError("simulated commit failure")

    event.listen(session, "before_commit", fail_commit)
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        execute_workflow(session, workflow, version)
    event.remove(session, "before_commit", fail_commit)
    session.rollback()
    assert session.scalar(select(WorkflowRun)) is None
