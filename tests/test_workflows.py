from concurrent.futures import ThreadPoolExecutor

from agentflow.workflows import MAX_DEFINITION_BYTES


def test_health_and_action_catalog(client):
    assert client.get("/health").json()["status"] == "ok"
    catalog = {item["id"]: item for item in client.get("/api/actions").json()}
    assert set(catalog) == {
        "set_value",
        "uppercase",
        "append",
    }
    assert catalog["uppercase"]["output_schema"]["type"] == "object"
    assert catalog["uppercase"]["required_permissions"] == []


def test_create_and_read_workflow(client, valid_definition):
    response = client.post("/api/workflows", json=valid_definition)
    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "Greeting"
    assert created["current_version_number"] == 1
    assert created["definition"] == valid_definition
    assert client.get(f"/api/workflows/{created['id']}").json() == created
    assert client.get("/api/workflows").json()[0]["id"] == created["id"]


def test_workflow_update_creates_version_and_old_version_can_run(client, valid_definition):
    workflow = client.post("/api/workflows", json=valid_definition).json()
    next_definition = {
        **valid_definition,
        "name": "Farewell",
        "steps": [{"id": "bye", "action": "set_value", "inputs": {"value": "goodbye"}}],
    }
    updated = client.put(f"/api/workflows/{workflow['id']}", json=next_definition)
    assert updated.status_code == 200
    assert updated.json()["id"] == workflow["id"]
    assert updated.json()["current_version_number"] == 2
    assert updated.json()["definition"] == next_definition
    versions = client.get(f"/api/workflows/{workflow['id']}/versions").json()
    assert [version["version_number"] for version in versions] == [2, 1]

    latest_run = client.post(f"/api/workflows/{workflow['id']}/runs").json()
    old_run = client.post(f"/api/workflows/{workflow['id']}/versions/1/runs").json()
    assert latest_run["workflow_version_number"] == 2
    assert latest_run["output"] == {"value": "goodbye"}
    assert old_run["workflow_version_number"] == 1
    assert old_run["output"] == {"value": "HELLO"}


def test_invalid_update_does_not_create_a_version(client, valid_definition):
    workflow = client.post("/api/workflows", json=valid_definition).json()
    invalid = {**valid_definition, "steps": [{"id": "bad", "action": "shell", "inputs": {}}]}
    assert client.put(f"/api/workflows/{workflow['id']}", json=invalid).status_code == 422
    assert [
        row["version_number"]
        for row in client.get(f"/api/workflows/{workflow['id']}/versions").json()
    ] == [1]


def test_rejects_unknown_actions_and_invalid_workflow_shapes(client, valid_definition):
    invalid = {**valid_definition, "steps": [{"id": "bad", "action": "shell", "inputs": {}}]}
    assert client.post("/api/workflows", json=invalid).status_code == 422

    invalid = {**valid_definition, "steps": [{"id": "bad", "action": "uppercase", "inputs": {}}]}
    assert client.post("/api/workflows", json=invalid).status_code == 422

    oversized = {
        **valid_definition,
        "steps": [{"id": "too_long", "action": "set_value", "inputs": {"value": "x" * 10001}}],
    }
    assert client.post("/api/workflows", json=oversized).status_code == 422


def test_rejects_workflow_definition_over_total_payload_limit(client, valid_definition):
    oversized = {
        **valid_definition,
        "steps": [
            {
                "id": "payload",
                "action": "set_value",
                "inputs": {"value": "x" * (MAX_DEFINITION_BYTES + 1)},
            }
        ],
    }
    response = client.post("/api/workflows", json=oversized)
    assert response.status_code == 422
    assert "workflow definition exceeds" in response.text


def test_executes_sequential_workflow_and_persists_run(client, valid_definition):
    workflow = client.post("/api/workflows", json=valid_definition).json()
    response = client.post(f"/api/workflows/{workflow['id']}/runs")
    assert response.status_code == 201
    run = response.json()
    assert run["status"] == "succeeded"
    assert run["error_category"] is None
    assert run["summary"] == {
        "total_steps": 2,
        "succeeded_steps": 2,
        "failed_steps": 0,
        "not_started_steps": 0,
    }
    assert [step["status"] for step in run["steps"]] == ["succeeded", "succeeded"]
    assert run["steps"][1]["output"] == {"value": "HELLO"}
    assert client.get(f"/api/runs/{run['id']}").json() == run
    assert client.get("/api/runs").json()[0] == run


def test_records_failed_step_and_stops_run(client, valid_definition):
    definition = {
        **valid_definition,
        "steps": [
            {"id": "number", "action": "set_value", "inputs": {"value": 42}},
            {"id": "needs_text", "action": "uppercase", "inputs": {"value": {"$ref": "number"}}},
            {"id": "never", "action": "set_value", "inputs": {"value": "not run"}},
        ],
    }
    workflow = client.post("/api/workflows", json=definition).json()
    run = client.post(f"/api/workflows/{workflow['id']}/runs").json()
    assert run["status"] == "failed"
    assert run["error_category"] == "invalid_input"
    assert run["summary"] == {
        "total_steps": 3,
        "succeeded_steps": 1,
        "failed_steps": 1,
        "not_started_steps": 1,
    }
    assert [step["status"] for step in run["steps"]] == ["succeeded", "failed"]
    assert run["steps"][1]["error_category"] == "invalid_input"
    assert "registered action schema" in run["steps"][1]["error"]
    assert "42" not in run["steps"][1]["error"]
    assert run["output"] is None
    assert "42" not in run["error"]


def test_rejects_duplicate_ids_forward_references_and_extra_fields(client, valid_definition):
    forward_reference = {
        **valid_definition,
        "steps": [{"id": "first", "action": "uppercase", "inputs": {"value": {"$ref": "later"}}}],
    }
    invalid_cases = [
        {**valid_definition, "steps": [valid_definition["steps"][0]] * 2},
        forward_reference,
        {
            **valid_definition,
            "steps": [
                {"id": "first", "action": "uppercase", "inputs": {"value": {"$ref": "first"}}}
            ],
        },
        {**valid_definition, "unexpected": True},
    ]
    for invalid in invalid_cases:
        assert client.post("/api/workflows", json=invalid).status_code == 422


def test_not_found_responses(client):
    assert client.get("/api/workflows/123").status_code == 404
    assert client.post("/api/workflows/123/runs").status_code == 404
    assert client.get("/api/runs/123").status_code == 404
    assert client.get("/api/runs?limit=0").status_code == 422


def test_repeated_run_requests_create_distinct_persisted_runs(client, valid_definition):
    workflow = client.post("/api/workflows", json=valid_definition).json()
    first = client.post(f"/api/workflows/{workflow['id']}/runs").json()
    second = client.post(f"/api/workflows/{workflow['id']}/runs").json()
    assert first["id"] != second["id"]
    history = client.get("/api/runs?limit=2").json()
    assert [item["id"] for item in history] == [second["id"], first["id"]]


def test_concurrent_run_requests_create_consistent_history(client, valid_definition):
    workflow = client.post("/api/workflows", json=valid_definition).json()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(client.post, f"/api/workflows/{workflow['id']}/runs") for _ in range(2)
        ]
    runs = [future.result().json() for future in futures]
    assert all(run["status"] == "succeeded" for run in runs)
    assert len({run["id"] for run in runs}) == 2
    assert len(client.get("/api/runs?limit=2").json()) == 2
