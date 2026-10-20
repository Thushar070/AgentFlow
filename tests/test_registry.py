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
    assert set(catalog) == {
        "set_value",
        "uppercase",
        "append",
        "trim_text",
        "replace_text",
        "concatenate_text",
        "parse_json",
        "get_json_field",
        "validate_email",
        "validate_url",
        "compare_values",
        "filter_records",
        "clean_records",
        "calculate_total",
        "http_request",
        "send_webhook",
        "require_approval",
        "create_result",
    }
    for item in catalog.values():
        assert item["name"]
        assert item["category"]
        assert item["description"]
        assert item["version"] == "1.0"
        assert item["input_schema"]["type"] == "object"
        assert item["output_schema"]["type"] == "object"
        assert item["timeout_policy"] == TimeoutPolicy.PROCESS_INTERRUPTIBLE.value
        assert item["timeout_seconds"] > 0
        assert item["max_attempts"] >= 1
    assert catalog["send_webhook"]["approval_required"] is True
    assert catalog["http_request"]["idempotent_replay"] is False
    assert catalog["send_webhook"]["idempotent_replay"] is False


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


def test_text_actions_and_empty_literal_replacement():
    assert run_action("trim_text", {"value": "  hello\n"}) == {"value": "hello"}
    assert run_action("replace_text", {"value": "hello", "search": "", "replacement": "x"}) == {
        "value": "hello"
    }
    assert run_action("replace_text", {"value": "a.b", "search": ".", "replacement": "!"}) == {
        "value": "a!b"
    }
    assert run_action("concatenate_text", {"values": ["a", "b"], "separator": ", "}) == {
        "value": "a, b"
    }


def test_structured_json_actions_bound_depth_and_extract_nested_values():
    assert run_action("set_value", {"value": {"rows": [{"count": 3}]}}) == {
        "value": {"rows": [{"count": 3}]}
    }
    assert run_action("parse_json", {"value": '[{"id":1}]'}) == {"value": [{"id": 1}]}
    assert run_action(
        "get_json_field", {"value": {"rows": [{"count": 3}]}, "path": "/rows/0/count"}
    ) == {"value": 3}
    with pytest.raises(ActionFailure) as malformed:
        run_action("parse_json", {"value": "{"})
    assert malformed.value.category is ActionErrorCategory.INVALID_JSON
    with pytest.raises(ActionFailure) as missing:
        run_action("get_json_field", {"value": {"rows": []}, "path": "/rows/0"})
    assert missing.value.category is ActionErrorCategory.FIELD_NOT_FOUND
    too_deep = "[" * 40 + "0" + "]" * 40
    with pytest.raises(ActionFailure):
        run_action("parse_json", {"value": too_deep})


def test_validation_comparison_filter_math_and_result_actions():
    assert run_action("validate_email", {"value": "person@example.test"})["value"]["valid"]
    assert not run_action("validate_email", {"value": "bad"})["value"]["valid"]
    assert not run_action("validate_url", {"value": "javascript:alert(1)"})["value"]["valid"]
    assert run_action("compare_values", {"left": 3, "operator": "gte", "right": 3})["value"][
        "result"
    ]
    assert run_action(
        "filter_records",
        {
            "records": [{"amount": 1}, {"amount": 3}, {}],
            "field": "amount",
            "operator": "gt",
            "value": 1,
        },
    ) == {"value": [{"amount": 3}]}
    assert run_action(
        "clean_records",
        {
            "records": [
                {"name": " Ada ", "email": " ada@example.test ", "amount": 3, "active": True},
                {"name": "Grace", "amount": 4, "active": True},
                {"name": "Lin", "email": "lin@example.test", "amount": 5, "active": False},
            ],
            "required_fields": ["name", "email", "amount", "active"],
            "trim_fields": ["name", "email"],
            "active_only": True,
            "amount_field": "amount",
        },
    ) == {
        "value": {
            "records": [{"name": "Ada", "email": "ada@example.test", "amount": 3, "active": True}],
            "amounts": [3],
            "removed_count": 2,
        }
    }
    assert run_action("calculate_total", {"values": [0.1, 0.2]}) == {"value": 0.3}
    assert run_action("calculate_total", {"values": []}) == {"value": 0.0}
    assert run_action("create_result", {"fields": {"valid": True, "total": 3}}) == {
        "value": {"valid": True, "total": 3}
    }


def test_actions_reject_unknown_fields_oversized_data_and_incompatible_types():
    with pytest.raises(ActionFailure) as extra:
        run_action("trim_text", {"value": "ok", "regex": "x"})
    assert extra.value.category is ActionErrorCategory.INVALID_INPUT
    with pytest.raises(ActionFailure):
        run_action("concatenate_text", {"values": ["x"] * 101})
    with pytest.raises(ActionFailure):
        run_action("calculate_total", {"values": [True]})
    with pytest.raises(ActionFailure) as mismatch:
        run_action("compare_values", {"left": 1, "operator": "eq", "right": "1"})
    assert mismatch.value.category is ActionErrorCategory.INVALID_COMPARISON


def test_action_output_is_bounded_and_validated(monkeypatch):
    spec = ACTION_REGISTRY["set_value"]
    monkeypatch.setitem(
        ACTION_REGISTRY,
        "set_value",
        ActionSpec(
            spec.input_schema,
            spec.output_schema,
            lambda _inputs: {"value": float("nan")},
            spec.description,
        ),
    )
    with pytest.raises(ActionFailure) as failure:
        run_action("set_value", {"value": "ok"})
    assert failure.value.category is ActionErrorCategory.INVALID_OUTPUT
