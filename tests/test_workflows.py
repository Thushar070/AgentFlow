def test_health_and_action_catalog(client):
    assert client.get("/health").json()["status"] == "ok"
    assert {item["id"] for item in client.get("/api/actions").json()} == {
        "set_value",
        "uppercase",
        "append",
    }


def test_create_and_read_workflow(client, valid_definition):
    response = client.post("/api/workflows", json=valid_definition)
    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "Greeting"
    assert client.get(f"/api/workflows/{created['id']}").json() == created
    assert client.get("/api/workflows").json()[0]["id"] == created["id"]


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


def test_executes_sequential_workflow_and_persists_run(client, valid_definition):
    workflow = client.post("/api/workflows", json=valid_definition).json()
    response = client.post(f"/api/workflows/{workflow['id']}/runs")
    assert response.status_code == 201
    run = response.json()
    assert run["status"] == "succeeded"
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
    assert [step["status"] for step in run["steps"]] == ["succeeded", "failed"]
    assert "registered action schema" in run["steps"][1]["error"]
    assert "42" not in run["steps"][1]["error"]


def test_rejects_duplicate_ids_forward_references_and_extra_fields(client, valid_definition):
    forward_reference = {
        **valid_definition,
        "steps": [{"id": "first", "action": "uppercase", "inputs": {"value": {"$ref": "later"}}}],
    }
    invalid_cases = [
        {**valid_definition, "steps": [valid_definition["steps"][0]] * 2},
        forward_reference,
        {**valid_definition, "unexpected": True},
    ]
    for invalid in invalid_cases:
        assert client.post("/api/workflows", json=invalid).status_code == 422


def test_not_found_responses(client):
    assert client.get("/api/workflows/123").status_code == 404
    assert client.post("/api/workflows/123/runs").status_code == 404
    assert client.get("/api/runs/123").status_code == 404
    assert client.get("/api/runs?limit=0").status_code == 422
