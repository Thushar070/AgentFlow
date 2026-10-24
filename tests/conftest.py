import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import agentflow.main as main_module
from agentflow.auth import hash_password
from agentflow.database import get_session
from agentflow.models import User, utcnow

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    from agentflow.config import get_settings

    get_settings.cache_clear()
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": database_url,
        # Do not let the developer's direct Neon URL override the isolated test DB.
        "DATABASE_URL_UNPOOLED": "",
    }
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

    with factory.begin() as session:
        session.add(
            User(
                username="test-admin",
                username_normalized="test-admin",
                email="test-admin@example.test",
                email_normalized="test-admin@example.test",
                password_hash=hash_password("Admin1!x"),
                role="admin",
                is_active=True,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )

    def override_get_session():
        with factory() as session:
            yield session

    monkeypatch.setattr(main_module, "engine", engine)
    main_module.app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(main_module.app, raise_server_exceptions=False) as api_client:
            api_client.agentflow_database_url = database_url
            api_client.headers.update({"Origin": "http://localhost:3000"})
            login = api_client.post(
                "/api/auth/login",
                json={
                    "username_or_email": "test-admin",
                    "password": "Admin1!x",
                },
            )
            if login.status_code != 200:
                raise RuntimeError(f"Test admin login failed: {login.status_code} {login.text}")
            api_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            api_client.test_admin_id = login.json()["user"]["id"]
            yield api_client
    finally:
        main_module.app.dependency_overrides.clear()
        engine.dispose()
        get_settings.cache_clear()


def run_worker_once(client):
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": client.agentflow_database_url,
        "DATABASE_URL_UNPOOLED": "",
    }
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
