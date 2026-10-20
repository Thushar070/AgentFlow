"""Versioned workflow contract and definition validation."""

import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

StepId = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,79}$")]
MAX_DEFINITION_BYTES = 512 * 1024


class StepDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["action"] = "action"
    id: StepId
    action: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    inputs: dict[str, Any] = Field(default_factory=dict)


class ConditionalDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: StepId
    type: Literal["conditional"] = "conditional"
    condition: dict[str, Any]
    then_steps: list[StepDefinition] = Field(default_factory=list, max_length=20)
    else_steps: list[StepDefinition] = Field(default_factory=list, max_length=20)


WorkflowStep = StepDefinition | ConditionalDefinition


class WorkflowInputField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(pattern=r"^(string|number|integer|boolean|object|array)$")
    required: bool = True
    description: Annotated[str | None, StringConstraints(max_length=300)] = None
    default: Any = None


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(default=1, ge=1, le=1)
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    description: Annotated[
        str | None, StringConstraints(strip_whitespace=True, max_length=1000)
    ] = None
    input_schema: dict[StepId, WorkflowInputField] = Field(default_factory=dict, max_length=50)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_step_ids_and_references(self):
        seen: set[str] = set()
        all_ids: set[str] = set()
        for step in self.steps:
            if step.id in all_ids:
                raise ValueError(f"duplicate step id: {step.id}")
            all_ids.add(step.id)
            if isinstance(step, ConditionalDefinition):
                if set(step.condition) != {"left", "operator", "right"}:
                    raise ValueError("condition must contain left, operator, and right")
                if step.condition["operator"] not in {
                    "eq",
                    "ne",
                    "gt",
                    "gte",
                    "lt",
                    "lte",
                    "is_empty",
                    "is_not_empty",
                }:
                    raise ValueError("condition uses an unsupported comparison operator")
                for ref in _references(step.condition):
                    if "step" in ref and ref["step"] not in seen:
                        raise ValueError("condition references must point to earlier steps")
                    if "input" in ref and ref["input"] not in self.input_schema:
                        raise ValueError("workflow input reference is not declared")
                branch_ids = set()
                for branch in (step.then_steps, step.else_steps):
                    available = set(seen)
                    for child in branch:
                        if child.id in all_ids or child.id in branch_ids:
                            raise ValueError("step IDs must be unique across workflow branches")
                        branch_ids.add(child.id)
                        all_ids.add(child.id)
                        for value in child.inputs.values():
                            for ref in _references(value):
                                if "step" in ref and ref["step"] not in available:
                                    raise ValueError(
                                        "branch references must point to earlier steps on this path"
                                    )
                                if "input" in ref and ref["input"] not in self.input_schema:
                                    raise ValueError("workflow input reference is not declared")
                        available.add(child.id)
                        from agentflow.registry import validate_step

                        validate_step(child, available - {child.id})
                seen.add(step.id)
                continue
            for value in step.inputs.values():
                for reference in _references(value):
                    if "step" in reference and reference["step"] not in seen:
                        raise ValueError("step references must point to an earlier step")
                    if "input" in reference and reference["input"] not in self.input_schema:
                        raise ValueError("workflow input reference is not declared")
            seen.add(step.id)
        flattened_count = sum(
            1 + len(step.then_steps) + len(step.else_steps)
            if isinstance(step, ConditionalDefinition)
            else 1
            for step in self.steps
        )
        if flattened_count > 50:
            raise ValueError("workflow may contain no more than 50 total action and branch steps")
        size = len(json.dumps(self.model_dump(mode="json"), separators=(",", ":")).encode("utf-8"))
        if size > MAX_DEFINITION_BYTES:
            raise ValueError(f"workflow definition exceeds {MAX_DEFINITION_BYTES} bytes")
        return self


def validate_definition(value: dict[str, Any]) -> WorkflowDefinition:
    """Validate structure, allowlisted actions, action inputs, and references."""
    definition = WorkflowDefinition.model_validate(value)
    from agentflow.registry import validate_step

    prior_ids: set[str] = set()
    prior_steps: dict[str, StepDefinition | ConditionalDefinition] = {}
    for step in definition.steps:
        if isinstance(step, StepDefinition):
            validate_step(step, prior_ids)
            _validate_reference_types(step, prior_steps, definition.input_schema)
        else:
            branch_prior = dict(prior_steps)
            for branch in (step.then_steps, step.else_steps):
                for child in branch:
                    validate_step(child, set(branch_prior))
                    _validate_reference_types(child, branch_prior, definition.input_schema)
                    branch_prior[child.id] = child
        prior_ids.add(step.id)
        prior_steps[step.id] = step
    return definition


def _schema_at_path(schema: dict[str, Any], path: str) -> dict[str, Any] | None:
    current: dict[str, Any] | None = schema
    if path in {"", "/"}:
        return current
    for token in path.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if current is None:
            return None
        if current.get("type") == "array":
            current = current.get("items")
            continue
        properties = current.get("properties", {})
        if token in properties:
            current = properties[token]
        elif current.get("additionalProperties") is True:
            return None
        else:
            raise ValueError("reference path does not exist in the source output")
    return current


def _literal_schema(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        return {"type": "array", "items": _literal_schema(value[0])} if value else {"type": "array"}
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": {key: _literal_schema(item) for key, item in value.items()},
            "additionalProperties": False,
        }
    return {}


def _output_schema(step: StepDefinition | ConditionalDefinition) -> dict[str, Any] | None:
    if isinstance(step, ConditionalDefinition):
        return None
    if step.action == "set_value":
        value = step.inputs.get("value")
        if isinstance(value, dict) and ("$ref" in value or "$input" in value):
            return None
        return {"type": "object", "properties": {"value": _literal_schema(value)}}
    from agentflow.registry import action_spec

    return action_spec(step.action).output_schema.model_json_schema()


def _schema_types(schema: dict[str, Any] | None) -> set[str]:
    if schema is None:
        return set()
    result = set()
    if isinstance(schema.get("type"), str):
        result.add(schema["type"])
    elif isinstance(schema.get("type"), list):
        result.update(item for item in schema["type"] if isinstance(item, str))
    for alternative in schema.get("anyOf", []) + schema.get("oneOf", []):
        result.update(_schema_types(alternative))
    return result


def _validate_reference_types(
    step: StepDefinition,
    prior_steps: dict[str, StepDefinition | ConditionalDefinition],
    input_schema: dict[str, WorkflowInputField],
) -> None:
    from agentflow.registry import action_spec

    target_properties = (
        action_spec(step.action).input_schema.model_json_schema().get("properties", {})
    )
    for key, raw in step.inputs.items():
        # A reference nested in an object/array has a more specific child
        # schema than the top-level action field; the action validator handles
        # that resolved payload at execution time.
        if not isinstance(raw, dict) or not ("$ref" in raw or "$input" in raw):
            continue
        for reference in _references(raw):
            source_schema: dict[str, Any] | None = None
            path = ""
            if "input" in reference:
                field = input_schema[reference["input"]]
                path = reference.get("path", "")
                if path and field.type not in {"object", "array"}:
                    raise ValueError("reference path does not exist in the workflow input")
                source_schema = None if path else {"type": field.type}
            elif "step" in reference:
                source = prior_steps[reference["step"]]
                schema = _output_schema(source)
                path = reference.get("path", "/value")
                if schema is not None:
                    source_schema = _schema_at_path(schema, path)
            if source_schema is None:
                continue
            target_types = _schema_types(target_properties.get(key))
            source_types = _schema_types(source_schema)
            compatible = bool(target_types & source_types)
            if "number" in target_types and "integer" in source_types:
                compatible = True
            if target_types and source_types and not compatible:
                raise ValueError(
                    f"reference type is incompatible with input '{key}' in step '{step.id}'"
                )


def validate_run_input(
    definition: WorkflowDefinition, value: dict[str, Any] | None
) -> dict[str, Any]:
    """Validate named runtime inputs against the saved workflow input contract."""
    supplied = value or {}
    if not isinstance(supplied, dict) or set(supplied) - set(definition.input_schema):
        raise ValueError("Run input contains fields not declared by this workflow.")
    result: dict[str, Any] = {}
    for name, field in definition.input_schema.items():
        if name in supplied:
            item = supplied[name]
        elif field.default is not None:
            item = field.default
        elif field.required:
            raise ValueError(f"Run input is missing required field '{name}'.")
        else:
            continue
        expected = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "object": dict,
            "array": list,
        }[field.type]
        if (
            not isinstance(item, expected)
            or isinstance(item, bool)
            and field.type in {"number", "integer"}
        ):
            raise ValueError(f"Run input field '{name}' must be {field.type}.")
        from agentflow.registry import validate_json_value

        validate_json_value(item)
        result[name] = item
    return result


def executable_step_count(definition: WorkflowDefinition) -> int:
    return sum(
        1 + len(step.then_steps) + len(step.else_steps)
        if isinstance(step, ConditionalDefinition)
        else 1
        for step in definition.steps
    )


def _references(value: Any):
    if isinstance(value, dict):
        if "$ref" in value:
            reference = value["$ref"]
            if isinstance(reference, str) and set(value) == {"$ref"}:
                yield {"step": reference, "path": "/value"}
                return
            if isinstance(reference, dict) and set(reference) == {"step", "path"}:
                if isinstance(reference["step"], str) and isinstance(reference["path"], str):
                    _validate_reference_path(reference["path"])
                    yield {"step": reference["step"], "path": reference["path"]}
                    return
            raise ValueError("step reference must contain a step and JSON Pointer path")
        if "$input" in value:
            reference = value["$input"]
            if isinstance(reference, str) and set(value) == {"$input"}:
                yield {"input": reference}
                return
            if isinstance(reference, dict) and set(reference) == {"name", "path"}:
                if isinstance(reference["name"], str) and isinstance(reference["path"], str):
                    _validate_reference_path(reference["path"])
                    yield {"input": reference["name"], "path": reference["path"]}
                    return
            raise ValueError(
                "workflow input reference must contain an input name and optional path"
            )
        for child in value.values():
            yield from _references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _references(child)


def _validate_reference_path(path: str):
    if path and (
        not path.startswith("/")
        or any(re.search(r"~(?![01])", part) for part in path[1:].split("/"))
    ):
        raise ValueError("reference path must be an escaped JSON Pointer")
