"""Allowlisted deterministic actions and their typed input contracts."""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


Scalar = Annotated[str, StringConstraints(max_length=10000)] | int | float | bool


class SetValueInput(StrictInput):
    value: Scalar


class UppercaseInput(StrictInput):
    value: str = Field(max_length=10000)


class AppendInput(StrictInput):
    value: str = Field(max_length=10000)
    suffix: str = Field(max_length=1000)


def set_value(inputs: dict[str, Any]) -> dict[str, Any]:
    return {"value": inputs["value"]}


def uppercase(inputs: dict[str, Any]) -> dict[str, Any]:
    return {"value": inputs["value"].upper()}


def append_text(inputs: dict[str, Any]) -> dict[str, Any]:
    return {"value": inputs["value"] + inputs["suffix"]}


ACTION_REGISTRY = {
    "set_value": (SetValueInput, set_value, "Return a configured scalar value."),
    "uppercase": (UppercaseInput, uppercase, "Convert text to uppercase."),
    "append": (AppendInput, append_text, "Append a configured text suffix."),
}


def validate_step(step, prior_ids: set[str]) -> None:
    if step.action not in ACTION_REGISTRY:
        raise ValueError(f"unknown action: {step.action}")
    schema, _, _ = ACTION_REGISTRY[step.action]
    raw = dict(step.inputs)
    for key, value in raw.items():
        if isinstance(value, dict) and "$ref" in value:
            if value["$ref"] not in prior_ids:
                raise ValueError("step reference must point to an earlier step")
            raw[key] = _example_input(schema, key)
    schema.model_validate(raw)


def _example_input(schema: type[BaseModel], key: str):
    field = schema.model_fields.get(key)
    annotation = field.annotation if field else str
    if annotation is str:
        return "reference"
    return 1


def run_action(action: str, inputs: dict[str, Any]) -> dict[str, Any]:
    if action not in ACTION_REGISTRY:
        raise ValueError(f"unknown action: {action}")
    schema, handler, _ = ACTION_REGISTRY[action]
    validated = schema.model_validate(inputs)
    result = handler(validated.model_dump())
    return result


def action_catalog() -> list[dict[str, str]]:
    return [
        {"id": key, "description": description}
        for key, (_, _, description) in ACTION_REGISTRY.items()
    ]
