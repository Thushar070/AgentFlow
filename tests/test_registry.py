import pytest

from agentflow.registry import (
    ACTION_REGISTRY,
    ActionErrorCategory,
    ActionFailure,
    ActionSpec,
    TimeoutPolicy,
    action_catalog,
    run_action,
)


def test_registry_declares_input_output_permissions_and_timeout_policy():
    catalog = {item["id"]: item for item in action_catalog()}
    assert set(catalog) == {"set_value", "uppercase", "append"}
    for item in catalog.values():
        assert item["input_schema"]["type"] == "object"
        assert item["output_schema"]["type"] == "object"
        assert item["required_permissions"] == []
        assert item["timeout_policy"] == TimeoutPolicy.PROCESS_INTERRUPTIBLE.value
        assert item["timeout_seconds"] == 5
        assert item["max_attempts"] == 3


def test_action_output_is_validated(monkeypatch):
    spec = ACTION_REGISTRY["set_value"]
    monkeypatch.setitem(
        ACTION_REGISTRY,
        "set_value",
        ActionSpec(
            spec.input_schema,
            spec.output_schema,
            lambda _inputs: {"unexpected": "output"},
            spec.description,
        ),
    )
    with pytest.raises(ActionFailure) as failure:
        run_action("set_value", {"value": "ok"})
    assert failure.value.category is ActionErrorCategory.INVALID_OUTPUT
    assert "unexpected" not in failure.value.safe_message


def test_action_permissions_are_checked_before_handler(monkeypatch):
    spec = ACTION_REGISTRY["set_value"]
    called = False

    def handler(inputs):
        nonlocal called
        called = True
        return {"value": inputs["value"]}

    monkeypatch.setitem(
        ACTION_REGISTRY,
        "set_value",
        ActionSpec(
            spec.input_schema,
            spec.output_schema,
            handler,
            spec.description,
            required_permissions=("sensitive.write",),
        ),
    )
    with pytest.raises(ActionFailure) as failure:
        run_action("set_value", {"value": "ok"})
    assert failure.value.category is ActionErrorCategory.PERMISSION_DENIED
    assert called is False
    assert run_action("set_value", {"value": "ok"}, permissions=frozenset({"sensitive.write"})) == {
        "value": "ok"
    }


def test_handler_failure_is_classified_without_exposing_exception(monkeypatch):
    spec = ACTION_REGISTRY["set_value"]

    def handler(_inputs):
        raise RuntimeError("private handler detail")

    monkeypatch.setitem(
        ACTION_REGISTRY,
        "set_value",
        ActionSpec(spec.input_schema, spec.output_schema, handler, spec.description),
    )
    with pytest.raises(ActionFailure) as failure:
        run_action("set_value", {"value": "ok"})
    assert failure.value.category is ActionErrorCategory.EXECUTION_FAILED
    assert "private handler detail" not in failure.value.safe_message


def test_invalid_action_input_is_classified():
    with pytest.raises(ActionFailure) as failure:
        run_action("uppercase", {"value": 42})
    assert failure.value.category is ActionErrorCategory.INVALID_INPUT
