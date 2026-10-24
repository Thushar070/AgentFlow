from concurrent.futures import ThreadPoolExecutor
import time

from conftest import run_worker_once
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import agentflow.admin as admin_module
import agentflow.main as main_module
from agentflow.config import get_settings
from agentflow.models import (
    ApprovalRequest,
    AuditEvent,
    Base,
    StepRun,
    User,
    UserSession,
    WorkflowRun,
    utcnow,
)
from agentflow.security import ApiRateLimitMiddleware, redact

ORIGIN = "http://localhost:3000"
PASSWORD = "Test1!pw"


def login_as(parent, username, password=PASSWORD):
    client = TestClient(main_module.app, raise_server_exceptions=False)
    client.headers.update({"Origin": ORIGIN})
    result = client.post(
        "/api/auth/login", json={"username_or_email": username, "password": password}
    )
    assert result.status_code == 200, result.text
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    client.agentflow_database_url = parent.agentflow_database_url
    return client


def create_account(admin, *, username, email, role="operator"):
    response = admin.post(
        "/api/admin/users",
        json={
            "username": username,
            "email": email,
            "password": PASSWORD,
            "role": role,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_authentication_cookie_csrf_logout_and_expiry(client):
    login_response = client.post(
        "/api/auth/login",
        json={
            "username_or_email": "test-admin",
            "password": "Admin1!x",
        },
    )
    cookie_header = login_response.headers["set-cookie"]
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header
    assert "Path=/" in cookie_header
    assert "Admin1!x" not in cookie_header
    cookie = client.cookies.get("agentflow_session")
    assert cookie
    set_cookie = client.cookies  # The response attributes are covered by the login response below.
    assert set_cookie
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post("/api/workflows", json={}).status_code == 403

    engine = create_engine(client.agentflow_database_url)
    try:
        factory = sessionmaker(bind=engine)
        with factory.begin() as session:
            rows = session.scalars(
                select(UserSession).where(UserSession.user_id == client.test_admin_id)
            ).all()
            for row in rows:
                row.expires_at = utcnow()
    finally:
        engine.dispose()
    assert client.get("/api/workflows").status_code == 401


def test_security_headers_and_local_host_allowlist(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"
    denied = client.get("/health", headers={"Host": "outside.example"})
    assert denied.status_code == 400


def test_process_local_rate_limit_fallback_enforces_window():
    key = f"test-rate-limit-{time.time_ns()}"
    windows = ((60, 2),)
    now = int(time.time())
    assert ApiRateLimitMiddleware._memory_increment([key], windows, now) == 0
    assert ApiRateLimitMiddleware._memory_increment([key], windows, now) == 0
    assert ApiRateLimitMiddleware._memory_increment([key], windows, now) == 1


def test_unauthenticated_routes_and_passwords_are_protected(client):
    anon = TestClient(main_module.app, raise_server_exceptions=False)
    anon.headers.update({"Origin": ORIGIN})
    assert anon.get("/health").status_code == 200
    assert anon.get("/api/actions").status_code == 401
    assert anon.get("/api/examples").status_code == 401
    assert anon.get("/api/workflows").status_code == 401
    assert anon.post("/api/auth/register", json={}).status_code == 404
    assert anon.post("/api/admin/users", json={}).status_code == 401
    assert (
        anon.post(
            "/api/auth/login", json={"username_or_email": "test-admin", "password": "bad"}
        ).status_code
        == 401
    )
    engine = create_engine(client.agentflow_database_url)
    try:
        factory = sessionmaker(bind=engine)
        with factory() as session:
            user = session.get(User, client.test_admin_id)
            assert user.password_hash.startswith("$argon2id$")
            assert "Admin1!x" not in user.password_hash
            assert session.scalar(
                select(AuditEvent).where(AuditEvent.event_type == "auth.login_failed")
            )
    finally:
        engine.dispose()


def test_logout_password_change_and_last_admin_invalidation(client):
    assert (
        client.put(
            f"/api/admin/users/{client.test_admin_id}/role",
            json={"role": "operator", "is_active": True},
        ).status_code
        == 409
    )
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/workflows").status_code == 401

    signed_in = login_as(client, "test-admin", "Admin1!x")
    changed = signed_in.post(
        "/api/auth/password",
        json={
            "current_password": "Admin1!x",
            "new_password": "New1!abc",
        },
    )
    assert changed.status_code == 200
    assert signed_in.get("/api/workflows").status_code == 401
    assert (
        signed_in.post(
            "/api/auth/login",
            json={"username_or_email": "test-admin", "password": "Admin1!x"},
        ).status_code
        == 401
    )
    assert (
        signed_in.post(
            "/api/auth/login",
            json={"username_or_email": "test-admin", "password": "New1!abc"},
        ).status_code
        == 200
    )


def test_operator_resource_ownership_and_role_boundaries(client, valid_definition):
    create_account(client, username="operator-one", email="one@example.test")
    create_account(client, username="operator-two", email="two@example.test")
    first = login_as(client, "operator-one")
    second = login_as(client, "operator-two")
    workflow = first.post("/api/workflows", json=valid_definition).json()

    assert first.get("/api/workflows").json()[0]["id"] == workflow["id"]
    assert second.get("/api/workflows").json() == []
    assert second.get(f"/api/workflows/{workflow['id']}").status_code == 404
    assert second.put(f"/api/workflows/{workflow['id']}", json=valid_definition).status_code == 404
    assert second.post(f"/api/workflows/{workflow['id']}/runs").status_code == 404
    run = first.post(f"/api/workflows/{workflow['id']}/runs").json()
    assert second.get(f"/api/runs/{run['id']}").status_code == 404
    assert second.post(f"/api/runs/{run['id']}/cancel").status_code == 404
    assert client.get("/api/audit").status_code == 200
    assert first.get("/api/admin/users").status_code == 403
    assert first.get("/api/audit").status_code == 403
    events = client.get("/api/audit").json()
    assert any(event["event_type"] == "authorization.denied" for event in events)


def test_admin_bootstrap_is_one_time_and_interactive(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'bootstrap.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(admin_module, "SessionLocal", factory)
    answers = iter(["bootstrap-admin", "admin@example.test"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    passwords = iter(["Boot1!pw", "Boot1!pw"])
    monkeypatch.setattr(admin_module.getpass, "getpass", lambda _prompt: next(passwords))
    assert admin_module.bootstrap_admin() == 0

    answers = iter(["another-admin", "another@example.test"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    passwords = iter(["Boot2!pw", "Boot2!pw"])
    monkeypatch.setattr(admin_module.getpass, "getpass", lambda _prompt: next(passwords))
    assert admin_module.bootstrap_admin() == 1
    with factory() as session:
        assert (
            session.scalar(select(User).where(User.role == "admin")).username == "bootstrap-admin"
        )
    engine.dispose()


def test_sensitive_action_waits_for_approval_then_executes(client, valid_definition, monkeypatch):
    create_account(client, username="approver-one", email="approver@example.test", role="approver")
    approver = login_as(client, "approver-one")
    monkeypatch.setenv("APPROVAL_REQUIRED_ACTIONS", '["set_value"]')
    get_settings.cache_clear()
    workflow_definition = {
        **valid_definition,
        "steps": [{"id": "protected", "action": "set_value", "inputs": {"value": "sensitive"}}],
    }
    workflow = client.post("/api/workflows", json=workflow_definition).json()
    queued = client.post(f"/api/workflows/{workflow['id']}/runs").json()
    run_worker_once(client)
    run = client.get(f"/api/runs/{queued['id']}").json()
    assert run["status"] == "awaiting_approval"
    assert run["steps"][0]["attempts"] == []
    approvals = client.get("/api/approvals?status=pending").json()
    assert len(approvals) == 1
    approval = approvals[0]
    assert (
        client.post(
            f"/api/approvals/{approval['id']}/decision", json={"decision": "approved"}
        ).status_code
        == 403
    )

    approved = approver.post(
        f"/api/approvals/{approval['id']}/decision",
        json={"decision": "approved", "reason": "Reviewed"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert (
        approver.post(
            f"/api/approvals/{approval['id']}/decision", json={"decision": "approved"}
        ).status_code
        == 409
    )
    run_worker_once(client)
    finished = client.get(f"/api/runs/{queued['id']}").json()
    assert finished["status"] == "succeeded"
    assert finished["steps"][0]["output"] == {"value": "sensitive"}
    events = client.get("/api/audit", params={"run_id": queued["id"]}).json()
    assert {event["event_type"] for event in events} >= {
        "run.created",
        "approval.requested",
        "approval.decision",
        "run.succeeded",
    }
    monkeypatch.delenv("APPROVAL_REQUIRED_ACTIONS", raising=False)
    get_settings.cache_clear()


def test_approval_expiry_staleness_replay_and_concurrent_decision(
    client, valid_definition, monkeypatch
):
    create_account(client, username="approver-two", email="approver2@example.test", role="approver")
    approver = login_as(client, "approver-two")
    monkeypatch.setenv("APPROVAL_REQUIRED_ACTIONS", '["set_value"]')
    get_settings.cache_clear()
    definition = {
        **valid_definition,
        "steps": [{"id": "protected", "action": "set_value", "inputs": {"value": "x"}}],
    }
    workflow = client.post("/api/workflows", json=definition).json()
    client.post(f"/api/workflows/{workflow['id']}/runs")
    run_worker_once(client)
    approval = client.get("/api/approvals?status=pending").json()[0]

    engine = create_engine(client.agentflow_database_url)
    try:
        factory = sessionmaker(bind=engine)
        with factory.begin() as session:
            row = session.get(ApprovalRequest, approval["id"])
            step = session.get(StepRun, row.step_run_id)
            step.inputs = {"value": "tampered"}
    finally:
        engine.dispose()
    stale = approver.post(
        f"/api/approvals/{approval['id']}/decision", json={"decision": "approved"}
    )
    assert stale.status_code == 409
    run_worker_once(client)
    new_approval = client.get("/api/approvals?status=pending").json()[0]
    assert new_approval["id"] != approval["id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: (
                    approver.post(
                        f"/api/approvals/{new_approval['id']}/decision",
                        json={"decision": "approved"},
                    ).status_code
                ),
                range(2),
            )
        )
    assert sorted(results) == [200, 409]
    monkeypatch.delenv("APPROVAL_REQUIRED_ACTIONS", raising=False)
    get_settings.cache_clear()


def test_expired_approval_is_marked_stale_and_recreated(client, valid_definition, monkeypatch):
    create_account(
        client, username="approver-exp", email="approver-exp@example.test", role="approver"
    )
    approver = login_as(client, "approver-exp")
    monkeypatch.setenv("APPROVAL_REQUIRED_ACTIONS", '["set_value"]')
    get_settings.cache_clear()
    definition = {
        **valid_definition,
        "steps": [{"id": "protected", "action": "set_value", "inputs": {"value": "x"}}],
    }
    workflow = client.post("/api/workflows", json=definition).json()
    queued = client.post(f"/api/workflows/{workflow['id']}/runs").json()
    run_worker_once(client)
    old = client.get("/api/approvals?status=pending").json()[0]
    engine = create_engine(client.agentflow_database_url)
    try:
        factory = sessionmaker(bind=engine)
        with factory.begin() as session:
            row = session.get(ApprovalRequest, old["id"])
            row.expires_at = utcnow()
    finally:
        engine.dispose()
    expired = approver.post(f"/api/approvals/{old['id']}/decision", json={"decision": "approved"})
    assert expired.status_code == 409
    assert client.get(f"/api/runs/{queued['id']}").json()["status"] == "queued"
    run_worker_once(client)
    requests = client.get("/api/approvals").json()
    assert requests[0]["status"] == "pending"
    assert requests[0]["id"] != old["id"]
    assert requests[1]["status"] == "stale"
    assert client.get(f"/api/runs/{queued['id']}").json()["steps"][0]["attempts"] == []
    monkeypatch.delenv("APPROVAL_REQUIRED_ACTIONS", raising=False)
    get_settings.cache_clear()


def test_rejected_approval_is_terminal_and_never_runs_action(client, valid_definition, monkeypatch):
    create_account(
        client, username="approver-reject", email="approver-reject@example.test", role="approver"
    )
    approver = login_as(client, "approver-reject")
    monkeypatch.setenv("APPROVAL_REQUIRED_ACTIONS", '["set_value"]')
    get_settings.cache_clear()
    definition = {
        **valid_definition,
        "steps": [{"id": "protected", "action": "set_value", "inputs": {"value": "x"}}],
    }
    workflow = client.post("/api/workflows", json=definition).json()
    queued = client.post(f"/api/workflows/{workflow['id']}/runs").json()
    run_worker_once(client)
    approval = client.get("/api/approvals?status=pending").json()[0]
    invalid = approver.post(f"/api/approvals/{approval['id']}/decision", json={"decision": "maybe"})
    assert invalid.status_code == 422
    denied = approver.post(
        f"/api/approvals/{approval['id']}/decision",
        json={"decision": "rejected", "reason": "Not allowed"},
    )
    assert denied.status_code == 200
    run = client.get(f"/api/runs/{queued['id']}").json()
    assert run["status"] == "failed"
    assert run["error_category"] == "approval_rejected"
    assert run["steps"][0]["attempts"] == []
    run_worker_once(client)
    assert client.get(f"/api/runs/{queued['id']}").json()["status"] == "failed"
    monkeypatch.delenv("APPROVAL_REQUIRED_ACTIONS", raising=False)
    get_settings.cache_clear()


def test_redaction_recurses_and_request_limits_are_enforced(client):
    value = {
        "nested": [{"PaSsWoRd": "clear", "message": "Authorization: Bearer abcdefghijklmnop"}],
        "key": "sk-12345678901234567890",
    }
    redacted = redact(value)
    assert redacted["nested"][0]["PaSsWoRd"] == "[REDACTED]"
    assert "abcdefghijklmnop" not in redacted["nested"][0]["message"]
    assert redacted["key"] == "[REDACTED]"
    definition = {
        "version": 1,
        "name": "Redaction",
        "steps": [{"id": "one", "action": "set_value", "inputs": {"value": "ok"}}],
    }
    workflow = client.post("/api/workflows", json=definition).json()
    run = client.post(f"/api/workflows/{workflow['id']}/runs").json()
    engine = create_engine(client.agentflow_database_url)
    try:
        factory = sessionmaker(bind=engine)
        with factory.begin() as session:
            row = session.get(WorkflowRun, run["id"])
            row.output = {"nested": [{"ApiKey": "private"}]}
    finally:
        engine.dispose()
    exposed = client.get(f"/api/runs/{run['id']}").json()
    assert exposed["output"]["nested"][0]["ApiKey"] == "[REDACTED]"
    too_large = client.post("/api/workflows", content=b" " * 1_048_577)
    assert too_large.status_code == 413
    too_deep = '{"x":' * 34 + "0" + "}" * 34
    assert (
        client.post(
            "/api/workflows", content=too_deep, headers={"Content-Type": "application/json"}
        ).status_code
        == 422
    )


def test_login_and_run_creation_limits_are_bounded(client, monkeypatch, valid_definition):
    anon = TestClient(main_module.app, raise_server_exceptions=False)
    anon.headers.update({"Origin": ORIGIN})
    for _ in range(10):
        assert (
            anon.post(
                "/api/auth/login",
                json={"username_or_email": "test-admin", "password": "wrong-password"},
            ).status_code
            == 401
        )
    assert (
        anon.post(
            "/api/auth/login",
            json={
                "username_or_email": "test-admin",
                "password": "Admin1!x",
            },
        ).status_code
        == 429
    )

    monkeypatch.setenv("MAX_RUNS_PER_HOUR", "1")
    get_settings.cache_clear()
    workflow = client.post("/api/workflows", json=valid_definition).json()
    assert client.post(f"/api/workflows/{workflow['id']}/runs").status_code == 202
    assert client.post(f"/api/workflows/{workflow['id']}/runs").status_code == 429
    monkeypatch.delenv("MAX_RUNS_PER_HOUR", raising=False)
    get_settings.cache_clear()
