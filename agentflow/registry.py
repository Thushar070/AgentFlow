"""Allowlisted actions with explicit contracts and execution metadata."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from agentflow.config import get_settings


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Scalar = Annotated[str, StringConstraints(max_length=10_000)] | int | float | bool


class SetValueInput(StrictModel):
    value: Scalar


class UppercaseInput(StrictModel):
    value: str = Field(max_length=10_000)


class AppendInput(StrictModel):
    value: str = Field(max_length=10_000)
    suffix: str = Field(max_length=1_000)


class ScalarOutput(StrictModel):
    value: Scalar


class TextOutput(StrictModel):
    value: str = Field(max_length=11_000)


class ActionErrorCategory(StrEnum):
    UNKNOWN_ACTION = "unknown_action"
    INVALID_INPUT = "invalid_input"
    INVALID_OUTPUT = "invalid_output"
    PERMISSION_DENIED = "permission_denied"
    EXECUTION_FAILED = "execution_failed"


class TimeoutPolicy(StrEnum):
    """Registered action timeout behavior."""

    PROCESS_INTERRUPTIBLE = "separate_process_hard_timeout"


class ActionFailure(ValueError):
    def __init__(self, category: ActionErrorCategory, safe_message: str):
        super().__init__(safe_message)
        self.category = category
        self.safe_message = safe_message


@dataclass(frozen=True)
class ActionSpec:
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    handler: Any
    description: str
    required_permissions: tuple[str, ...] = ()
    timeout_policy: TimeoutPolicy = TimeoutPolicy.PROCESS_INTERRUPTIBLE
    timeout_seconds: float = 5.0
    max_attempts: int = 3
    retryable_categories: tuple[str, ...] = ("execution_failed", "action_timeout")
    backoff_base_seconds: float = 0.5
    idempotent_replay: bool = True


def set_value(inputs: dict[str, Any]) -> dict[str, Any]:
    return {"value": inputs["value"]}


def uppercase(inputs: dict[str, Any]) -> dict[str, Any]:
    return {"value": inputs["value"].upper()}


def append_text(inputs: dict[str, Any]) -> dict[str, Any]:
    return {"value": inputs["value"] + inputs["suffix"]}


ACTION_REGISTRY: dict[str, ActionSpec] = {
    "set_value": ActionSpec(
        SetValueInput, ScalarOutput, set_value, "Return a configured scalar value."
    ),
    "uppercase": ActionSpec(UppercaseInput, TextOutput, uppercase, "Convert text to uppercase."),
    "append": ActionSpec(AppendInput, TextOutput, append_text, "Append a configured text suffix."),
}


def action_timeout(action: str) -> float:
    spec = action_spec(action)
    overrides = get_settings().action_timeouts
    unknown = set(overrides) - set(ACTION_REGISTRY)
    if unknown:
        raise ValueError(
            f"ACTION_TIMEOUTS contains unregistered actions: {', '.join(sorted(unknown))}"
        )
    return overrides.get(action, spec.timeout_seconds)


def action_spec(action: str) -> ActionSpec:
    try:
        return ACTION_REGISTRY[action]
    except KeyError as exc:
        raise ActionFailure(
            ActionErrorCategory.UNKNOWN_ACTION, f"unknown action: {action}"
        ) from exc


def validate_step(step, prior_ids: set[str]) -> None:
    spec = action_spec(step.action)
    raw = dict(step.inputs)
    for key, value in raw.items():
        if isinstance(value, dict) and "$ref" in value:
            if value["$ref"] not in prior_ids:
                raise ValueError("step reference must point to an earlier step")
            raw[key] = _example_input(spec.input_schema, key)
    spec.input_schema.model_validate(raw)


def _example_input(schema: type[BaseModel], key: str):
    field = schema.model_fields.get(key)
    annotation = field.annotation if field else str
    if annotation is str:
        return "reference"
    return 1


def run_action(
    action: str,
    inputs: dict[str, Any],
    *,
    permissions: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    spec = action_spec(action)
    missing = set(spec.required_permissions) - permissions
    if missing:
        raise ActionFailure(
            ActionErrorCategory.PERMISSION_DENIED,
            "Execution context does not grant the action's required permission.",
        )
    try:
        validated_input = spec.input_schema.model_validate(inputs)
    except ValidationError as exc:
        raise ActionFailure(
            ActionErrorCategory.INVALID_INPUT,
            "Action inputs do not match the registered action schema.",
        ) from exc
    try:
        result = spec.handler(validated_input.model_dump())
    except Exception as exc:
        raise ActionFailure(
            ActionErrorCategory.EXECUTION_FAILED, "Action execution failed."
        ) from exc
    try:
        validated_output = spec.output_schema.model_validate(result)
    except ValidationError as exc:
        raise ActionFailure(
            ActionErrorCategory.INVALID_OUTPUT,
            "Action output does not match the registered output schema.",
        ) from exc
    return validated_output.model_dump(mode="json")


def action_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": key,
            "description": spec.description,
            "input_schema": spec.input_schema.model_json_schema(),
            "output_schema": spec.output_schema.model_json_schema(),
            "required_permissions": list(spec.required_permissions),
            "timeout_policy": spec.timeout_policy.value,
            "timeout_seconds": action_timeout(key),
            "max_attempts": spec.max_attempts,
            "retryable_categories": list(spec.retryable_categories),
            "backoff_base_seconds": spec.backoff_base_seconds,
            "idempotent_replay": spec.idempotent_replay,
        }
        for key, spec in ACTION_REGISTRY.items()
    ]
