import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import agentflow.ai_proposals as proposals
import agentflow.main as main_module
from agentflow.config import Settings
from agentflow.models import User
from agentflow.registry import action_catalog, action_requires_approval

ORIGIN = "http://localhost:3000"
EVALUATION_SET = Path(__file__).parent / "evaluation" / "phase7_cases.json"


def test_fixed_evaluation_set_covers_quality_and_safety_cases():
    cases = json.loads(EVALUATION_SET.read_text())
    categories = {case["category"] for case in cases}
    assert len(cases) >= 8
    assert {
        "clear request and multi-step plan",
        "ambiguous request",
        "invalid tool name",
        "malformed action arguments",
        "authorization and approval",
        "prompt injection and untrusted text",
        "malformed model output",
        "model timeout",
        "model unavailable",
    } <= categories
    assert all(case.get("prompt") and case.get("expected") for case in cases)


def admin_actor():
    return User(id=1, username="proposal-admin", role="admin", is_active=True)


def chain_definition():
    return {
        "version": 1,
        "name": "Uppercase customer name",
        "description": "Uppercase the supplied name and append a suffix.",
        "input_schema": {
            "customer_name": {"type": "string", "required": True},
            "suffix": {"type": "string", "required": True},
        },
        "steps": [
            {
                "id": "source",
                "action": "set_value",
                "inputs": {"value": {"$input": "customer_name"}},
            },
            {
                "id": "uppercase_name",
                "action": "uppercase",
                "inputs": {"value": {"$ref": "source"}},
            },
            {
                "id": "append_suffix",
                "action": "append",
                "inputs": {"value": {"$ref": "uppercase_name"}, "suffix": {"$input": "suffix"}},
            },
        ],
    }


def make_response(definition, *, status=200):
    return httpx.Response(
        status,
        json={
            "model": "test/registered-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": json.dumps(definition)},
                    "finish_reason": "stop",
                }
            ],
        },
    )


@pytest.fixture(autouse=True)
def configured_gateway(monkeypatch):
    monkeypatch.setattr(
        proposals,
        "get_settings",
        lambda: Settings(_env_file=None, freellmapi_api_key="test-unified-secret"),
    )


def test_freellmapi_request_uses_auto_routing_auth_and_real_schema(caplog):
    observed = {}

    def handler(request):
        observed.update(json.loads(request.content))
        observed["url"] = str(request.url)
        observed["authorization"] = request.headers.get("authorization")
        return make_response(chain_definition())

    result = asyncio.run(
        proposals.propose_definition(
            "Convert customer names to uppercase and append a suffix.",
            admin_actor(),
            transport=httpx.MockTransport(handler),
        )
    )
    assert observed["model"] == "auto"
    assert observed["url"] == "http://localhost:3001/v1/chat/completions"
    assert observed["authorization"] == "Bearer test-unified-secret"
    assert "test-unified-secret" not in caplog.text
    assert observed["stream"] is False
    assert observed["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "agentflow_workflow_definition",
            "strict": False,
            "schema": proposals._proposal_json_schema(),
        },
    }
    assert "input_schema" in observed["response_format"]["json_schema"]["schema"]["required"]
    assert observed["max_tokens"] <= proposals.MAX_GENERATION_TOKENS
    assert observed["temperature"] == 0.1
    assert result["steps"][1]["action"] == "uppercase"
    assert "test-unified-secret" not in json.dumps(result)
    system = observed["messages"][0]["content"]
    assert "steps" in system and "JSON Pointer" in system
    assert "CRITICAL: Include an `input_schema` object" in system
    for action in action_catalog():
        assert action["id"] in system
        assert action["description"] in system
        assert json.dumps(action["input_schema"], separators=(",", ":")) in system


def test_local_proposal_models_are_disabled(monkeypatch):
    monkeypatch.setattr(proposals, "get_settings", lambda: Settings(_env_file=None))
    with pytest.raises(proposals.ProposalFailure, match="Local model proposals are disabled"):
        asyncio.run(
            proposals.propose_definition(
                "Uppercase the customer's name.",
                admin_actor(),
                model_id="ollama-qwen3-0.6b",
            )
        )


@pytest.mark.parametrize(
    "prompt",
    [
        "Create a Python file that prints hello.",
        "What is the capital of France?",
        "Create a workflow that writes a Python script to disk.",
    ],
)
def test_out_of_scope_prompts_are_rejected_before_provider_call(prompt):
    called = False

    def handler(_request):
        nonlocal called
        called = True
        return make_response(chain_definition())

    with pytest.raises(proposals.ProposalFailure, match="only drafts workflows"):
        asyncio.run(
            proposals.propose_definition(
                prompt,
                admin_actor(),
                transport=httpx.MockTransport(handler),
            )
        )
    assert called is False


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (
            {
                "version": 1,
                "name": "Bad action",
                "steps": [{"id": "bad", "action": "shell", "inputs": {"command": "echo nope"}}],
            },
            "not registered",
        ),
        (
            {
                "version": 1,
                "name": "Bad inputs",
                "steps": [{"id": "bad", "action": "uppercase", "inputs": {"value": 12}}],
            },
            "invalid action arguments",
        ),
        (
            {
                "version": 1,
                "name": "Forward ref",
                "steps": [
                    {"id": "first", "action": "uppercase", "inputs": {"value": {"$ref": "later"}}}
                ],
            },
            "workflow rules",
        ),
        (
            {
                "version": 1,
                "name": "Branch unsupported",
                "steps": [
                    {
                        "id": "condition",
                        "type": "conditional",
                        "condition": {"left": 1, "operator": "eq", "right": 1},
                        "then_steps": [],
                        "else_steps": [],
                    }
                ],
            },
            "branching that is not supported",
        ),
    ],
)
def test_invalid_model_plans_are_rejected(candidate, expected):
    transport = httpx.MockTransport(lambda _request: make_response(candidate))
    with pytest.raises(proposals.ProposalFailure, match=expected):
        asyncio.run(
            proposals.propose_definition("make a workflow", admin_actor(), transport=transport)
        )


def test_malformed_model_json_is_rejected():
    response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": "not json"}}]},
    )
    with pytest.raises(proposals.ProposalFailure, match="malformed"):
        asyncio.run(
            proposals.propose_definition(
                "make a workflow", admin_actor(), transport=httpx.MockTransport(lambda _: response)
            )
        )


def test_prompt_and_gateway_response_limits_are_enforced():
    with pytest.raises(proposals.ProposalFailure, match="1 to 4000 characters"):
        asyncio.run(
            proposals.propose_definition("x" * (proposals.MAX_PROMPT_CHARS + 1), admin_actor())
        )
    oversized = httpx.Response(
        200,
        json={"choices": [{"message": {"content": "x" * proposals.MAX_PROVIDER_RESPONSE_BYTES}}]},
    )
    with pytest.raises(proposals.ProposalFailure, match="response exceeded the size limit"):
        asyncio.run(
            proposals.propose_definition(
                "make a workflow", admin_actor(), transport=httpx.MockTransport(lambda _: oversized)
            )
        )


def test_freellmapi_settings_default_and_loopback_restriction():
    settings = Settings(_env_file=None)
    assert settings.freellmapi_base_url == "http://localhost:3001/v1"
    assert settings.freellmapi_model == "auto"
    assert settings.freellmapi_timeout_seconds == 120
    assert settings.ollama_base_url == "http://localhost:11434/v1"
    assert Settings(
        _env_file=None, freellmapi_base_url="http://127.0.0.1:13001/v1"
    ).freellmapi_base_url == ("http://127.0.0.1:13001/v1")
    with pytest.raises(ValueError, match="loopback"):
        Settings(_env_file=None, freellmapi_base_url="https://gateway.example.test/v1")
    with pytest.raises(ValueError, match="API_HOST"):
        Settings(_env_file=None, api_host="0.0.0.0")
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        Settings(_env_file=None, cors_origins=["https://example.test"])
    with pytest.raises(ValueError, match="/v1 path"):
        Settings(_env_file=None, freellmapi_base_url="http://localhost:3001")
    with pytest.raises(ValueError, match="loopback"):
        Settings(_env_file=None, ollama_base_url="https://ollama.example.test/v1")


@pytest.mark.parametrize(
    ("exception", "status", "message"),
    [
        (httpx.ReadTimeout("internal timeout detail"), 504, "configured timeout"),
        (httpx.ConnectError("internal connection detail"), 503, "could not be reached"),
    ],
)
def test_timeout_and_unavailable_errors_are_safe(exception, status, message):
    def handler(request):
        raise exception

    with pytest.raises(proposals.ProposalFailure) as caught:
        asyncio.run(
            proposals.propose_definition(
                "make a workflow", admin_actor(), transport=httpx.MockTransport(handler)
            )
        )
    assert caught.value.status_code == status
    assert message in caught.value.message
    assert "internal" not in caught.value.message


def test_unavailable_automatic_route_error_is_clear():
    transport = httpx.MockTransport(lambda _: httpx.Response(404, text="private provider detail"))
    with pytest.raises(proposals.ProposalFailure, match="active fallback chain") as caught:
        asyncio.run(
            proposals.propose_definition("make a workflow", admin_actor(), transport=transport)
        )
    assert caught.value.status_code == 503
    assert "private" not in caught.value.message


@pytest.mark.parametrize(
    ("status", "expected_status", "message"),
    [
        (401, 503, "unified API key"),
        (403, 503, "unified API key"),
        (429, 503, "upstream quota"),
        (400, 502, "structured JSON output"),
        (422, 502, "structured JSON output"),
            (500, 503, "active fallback chain"),
    ],
)
def test_gateway_and_upstream_errors_are_actionable_and_redacted(status, expected_status, message):
    transport = httpx.MockTransport(lambda _: httpx.Response(status, text="secret upstream detail"))
    with pytest.raises(proposals.ProposalFailure) as caught:
        asyncio.run(
            proposals.propose_definition("make a workflow", admin_actor(), transport=transport)
        )
    assert caught.value.status_code == expected_status
    assert message in caught.value.message
    assert "secret" not in caught.value.message


@pytest.mark.parametrize(
    ("body", "expected_status", "message"),
    [
        ({"choices": [{"message": {"content": ""}}]}, 502, "malformed"),
        (
            {"choices": [{"message": {"content": "{}", "refusal": "private refusal text"}}]},
            422,
            "declined",
        ),
        (
            {"choices": [{"message": {"content": "{}"}, "finish_reason": "length"}]},
            502,
            "malformed",
        ),
    ],
)
def test_empty_refusal_and_truncated_responses_fail_safely(body, expected_status, message):
    with pytest.raises(proposals.ProposalFailure) as caught:
        asyncio.run(
            proposals.propose_definition(
                "make a workflow",
                admin_actor(),
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
            )
        )
    assert caught.value.status_code == expected_status
    assert message in caught.value.message
    assert "private refusal text" not in caught.value.message


def test_missing_gateway_key_fails_before_network_access(monkeypatch):
    monkeypatch.setattr(proposals, "get_settings", lambda: Settings(_env_file=None))
    calls = []
    with pytest.raises(proposals.ProposalFailure, match="FREELLMAPI_API_KEY") as caught:
        asyncio.run(
            proposals.propose_definition(
                "make a workflow",
                admin_actor(),
                transport=httpx.MockTransport(lambda _: calls.append(1)),
            )
        )
    assert caught.value.status_code == 503
    assert calls == []


def test_sensitive_action_proposal_stays_unsaved_and_worker_policy_is_unchanged(
    client, monkeypatch
):
    webhook = {
        "version": 1,
        "name": "Approved webhook proposal",
        "steps": [
            {
                "id": "notify",
                "action": "send_webhook",
                "inputs": {"url": "https://example.test/hook", "payload": {"ok": True}},
            }
        ],
    }
    monkeypatch.setattr(
        proposals,
        "_freellmapi_transport",
        lambda: httpx.MockTransport(lambda _: make_response(webhook)),
    )
    response = client.post("/api/workflows/proposals", json={"prompt": "notify a webhook"})
    assert response.status_code == 200, response.text
    assert "test-unified-secret" not in response.text
    assert response.json()["steps"][0]["action"] == "send_webhook"
    assert action_requires_approval("send_webhook") is True
    assert client.get("/api/workflows").json() == []
    assert client.get("/api/runs").json() == []


def test_proposal_route_requires_auth_and_denies_approver(client, monkeypatch):
    anon = TestClient(main_module.app, raise_server_exceptions=False)
    anon.headers["Origin"] = ORIGIN
    assert (
        anon.post("/api/workflows/proposals", json={"prompt": "uppercase names"}).status_code == 401
    )

    created = client.post(
        "/api/admin/users",
        json={
            "username": "proposal-approver",
            "email": "proposal-approver@example.test",
            "password": "Prop1!pw",
            "role": "approver",
        },
    )
    assert created.status_code == 201
    approver = TestClient(main_module.app, raise_server_exceptions=False)
    approver.headers["Origin"] = ORIGIN
    login = approver.post(
        "/api/auth/login",
        json={
            "username_or_email": "proposal-approver",
            "password": "Prop1!pw",
        },
    )
    approver.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    calls = []
    monkeypatch.setattr(
        proposals,
        "_freellmapi_transport",
        lambda: httpx.MockTransport(
            lambda _: calls.append(True) or make_response(chain_definition())
        ),
    )
    denied = approver.post("/api/workflows/proposals", json={"prompt": "uppercase names"})
    assert denied.status_code == 403
    assert calls == []
