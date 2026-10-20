"""Real HTTP-client integration tests against a disposable local mock server."""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from pydantic import ValidationError

from agentflow.action_http import _destination
from agentflow.config import Settings
from agentflow.registry import ActionErrorCategory, ActionFailure, run_action


class MockHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/json":
            body = json.dumps({"ok": True, "token": "mock-secret"}).encode()
            status, content_type = 200, "application/json"
        elif self.path == "/text":
            body = b"plain response"
            status, content_type = 200, "text/plain"
        elif self.path == "/secret-text":
            body = b"token=super-secret password:topsecret"
            status, content_type = 200, "text/plain"
        elif self.path == "/bad-json":
            body = b"{bad"
            status, content_type = 200, "application/json"
        elif self.path == "/large":
            body = b"x" * 2048
            status, content_type = 200, "text/plain"
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/json")
            self.end_headers()
            return
        elif self.path == "/slow":
            time.sleep(0.3)
            body = b"late"
            status, content_type = 200, "text/plain"
        else:
            body, status, content_type = b"not found", 404, "text/plain"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Set-Cookie", "session=must-not-be-returned")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.server.last_body = self.rfile.read(length)
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args):
        pass


@pytest.fixture
def mock_http(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    server.last_body = b""
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(
        "agentflow.action_http.get_settings",
        lambda: Settings(
            app_environment="test",
            http_allowed_hosts=["127.0.0.1"],
            http_allowed_ports=[server.server_port, 1],
            http_max_response_bytes=1024,
        ),
    )
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=1)


def test_real_http_client_parses_json_and_safe_headers(mock_http):
    output = run_action("http_request", {"url": f"{mock_http}/json"})["value"]
    assert output["status_code"] == 200
    assert output["body"] == {"ok": True, "token": "[REDACTED]"}
    assert "set-cookie" not in output["headers"]
    text = run_action("http_request", {"url": f"{mock_http}/secret-text"})["value"]["body"]
    assert "super-secret" not in text and "topsecret" not in text


def test_http_client_handles_text_status_json_size_and_redirect_errors(mock_http):
    text = run_action("http_request", {"url": f"{mock_http}/text"})["value"]
    assert text["body"] == "plain response"
    for path, category in (
        ("/bad-json", ActionErrorCategory.INVALID_JSON),
        ("/large", ActionErrorCategory.SIZE_LIMIT),
        ("/redirect", ActionErrorCategory.HTTP_STATUS_ERROR),
    ):
        with pytest.raises(ActionFailure) as failure:
            run_action("http_request", {"url": f"{mock_http}{path}"})
        assert failure.value.category is category


def test_http_timeout_connection_failure_and_webhook_delivery(mock_http):
    with pytest.raises(ActionFailure) as timeout:
        run_action(
            "http_request",
            {"url": f"{mock_http}/slow", "timeout_seconds": 0.1},
        )
    assert timeout.value.category is ActionErrorCategory.HTTP_TIMEOUT
    with pytest.raises(ActionFailure) as connection:
        run_action(
            "http_request",
            {"url": mock_http.rsplit(":", 1)[0] + ":1/fail", "timeout_seconds": 0.2},
        )
    assert connection.value.category is ActionErrorCategory.HTTP_CONNECTION_ERROR
    result = run_action("send_webhook", {"url": f"{mock_http}/hook", "payload": {"approved": True}})
    assert result["value"]["status_code"] == 204


def test_http_rejects_unallowlisted_and_restricted_dns_targets(monkeypatch):
    settings = Settings(http_allowed_hosts=["example.test"], http_allowed_ports=[443])
    monkeypatch.setattr("agentflow.action_http.get_settings", lambda: settings)
    with pytest.raises(ActionFailure) as denied:
        _destination("https://other.test/path")
    assert denied.value.category is ActionErrorCategory.DESTINATION_REJECTED

    monkeypatch.setattr(
        "agentflow.action_http.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(ActionFailure) as rebinding:
        _destination("https://example.test/path")
    assert rebinding.value.category is ActionErrorCategory.DESTINATION_REJECTED


def test_http_requires_https_for_non_test_and_never_accepts_private_hosts(monkeypatch):
    settings = Settings(
        app_environment="production",
        http_allowed_hosts=["127.0.0.1"],
        http_allowed_ports=[80],
    )
    monkeypatch.setattr("agentflow.action_http.get_settings", lambda: settings)
    with pytest.raises(ActionFailure) as failure:
        _destination("http://127.0.0.1:80/private")
    assert failure.value.category is ActionErrorCategory.DESTINATION_REJECTED


def test_wildcard_destination_allowlists_are_rejected():
    with pytest.raises(ValidationError):
        Settings(http_allowed_hosts=["*"])


def test_app_env_setting_reads_the_documented_variable(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    assert Settings(_env_file=None).app_environment == "test"
