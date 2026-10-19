import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import agentflow.main as main_module
from agentflow.database import get_session

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    environment = {**os.environ, "DATABASE_URL": database_url}
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
    )
    if migration.returncode:
        raise RuntimeError(f"Test database migration failed:\n{migration.stderr}")

    engine = create_engine(database_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_session():
        with factory() as session:
            yield session

    monkeypatch.setattr(main_module, "engine", engine)
    main_module.app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(main_module.app, raise_server_exceptions=False) as api_client:
            api_client.agentflow_database_url = database_url
            yield api_client
    finally:
        main_module.app.dependency_overrides.clear()
        engine.dispose()


def run_worker_once(client):
    environment = {**os.environ, "DATABASE_URL": client.agentflow_database_url}
    result = subprocess.run(
        [sys.executable, "-m", "agentflow.worker", "--once"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
    )
    if result.returncode:
        raise RuntimeError(f"Worker failed ({result.returncode}): {result.stderr}")


@pytest.fixture
def valid_definition():
    return {
        "version": 1,
        "name": "Greeting",
        "steps": [
            {"id": "greeting", "action": "set_value", "inputs": {"value": "hello"}},
            {"id": "shout", "action": "uppercase", "inputs": {"value": {"$ref": "greeting"}}},
        ],
    }
