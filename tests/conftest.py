import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    environment = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{tmp_path / 'test.db'}",
        "CORS_ORIGINS": '["http://localhost:3000"]',
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
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "agentflow.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    f"AgentFlow API exited during startup with code {process.returncode}"
                )
            try:
                if httpx.get(f"{base_url}/health", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("AgentFlow API did not become healthy within 8 seconds")
        with httpx.Client(base_url=base_url, timeout=3) as api_client:
            yield api_client
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


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
