"""Typed, allowlisted workflow actions and their public catalog contracts."""

import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from email.utils import parseaddr
from enum import StrEnum
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from agentflow.config import get_settings


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


JsonValue = Any
ScalarText = Annotated[str, StringConstraints(max_length=100_000)]
StepName = Annotated[str, StringConstraints(min_length=1, max_length=120)]
COMPARE_OPERATORS = ("eq", "ne", "gt", "gte", "lt", "lte", "is_empty", "is_not_empty")
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
MAX_JSON_DEPTH = 32
MAX_JSON_BYTES = 512 * 1024


def validate_json_value(value: Any, *, max_depth: int = MAX_JSON_DEPTH) -> Any:
    """Reject non-JSON values and deeply nested/oversized structures."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Value must contain valid JSON data.") from exc
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError("JSON value exceeds the configured size limit.")

    def depth(item: Any, level: int = 0) -> None:
        if level > max_depth:
            raise ValueError("JSON nesting exceeds the configured depth limit.")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > 256:
                    raise ValueError("JSON object keys must be strings up to 256 characters.")
                depth(child, level + 1)
        elif isinstance(item, list):
            for child in item:
                depth(child, level + 1)

    depth(value)
    return value


class SetValueInput(StrictModel):
    value: JsonValue

    @field_validator("value")
    @classmethod
    def valid_value(cls, value):
        validated = validate_json_value(value)
        if len(json.dumps(validated, ensure_ascii=False, separators=(",", ":")).encode()) > 10_000:
            raise ValueError("Configured value exceeds 10000 bytes.")
        return validated


class TextInput(StrictModel):
    value: ScalarText


class AppendInput(StrictModel):
    value: ScalarText
    suffix: Annotated[str, StringConstraints(max_length=10_000)]


class ReplaceTextInput(StrictModel):
    value: ScalarText
    search: Annotated[str, StringConstraints(max_length=10_000)]
    replacement: Annotated[str, StringConstraints(max_length=10_000)]


class ConcatenateInput(StrictModel):
    values: list[Annotated[str, StringConstraints(max_length=10_000)]] = Field(max_length=100)
    separator: Annotated[str, StringConstraints(max_length=1000)] = ""


class ParseJsonInput(StrictModel):
    value: Annotated[str, StringConstraints(max_length=MAX_JSON_BYTES)]


class GetJsonFieldInput(StrictModel):
    value: JsonValue
    path: Annotated[str, StringConstraints(min_length=1, max_length=1000)]

    @field_validator("value")
    @classmethod
    def valid_value(cls, value):
        return validate_json_value(value)

    @field_validator("path")
    @classmethod
    def valid_path(cls, value):
        if value.startswith("/"):
            parts = value[1:].split("/")
            if any(re.search(r"~(?![01])", part) for part in parts):
                raise ValueError("Path uses an invalid JSON Pointer escape.")
            return value
        if not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+|\[\d+\])*", value):
            raise ValueError("Path must use dot keys, array indexes, or JSON Pointer syntax.")
        return value


class ValidateEmailInput(StrictModel):
    value: Annotated[str, StringConstraints(max_length=320)]


class ValidateUrlInput(StrictModel):
    value: Annotated[str, StringConstraints(max_length=2048)]
    allowed_schemes: list[Literal["https", "http"]] = Field(default_factory=lambda: ["https"])

    @field_validator("allowed_schemes")
    @classmethod
    def nonempty_schemes(cls, value):
        if not value or len(set(value)) != len(value):
            raise ValueError("At least one unique URL scheme is required.")
        return value


class CompareValuesInput(StrictModel):
    left: JsonValue
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "is_empty", "is_not_empty"]
    right: JsonValue

    @model_validator(mode="after")
    def validate_operands(self):
        validate_json_value(self.left)
        validate_json_value(self.right)
        return self


class FilterRecordsInput(StrictModel):
    records: list[dict[str, JsonValue]] = Field(max_length=1000)
    field: Annotated[str, StringConstraints(min_length=1, max_length=1000)]
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "is_empty", "is_not_empty"]
    value: JsonValue = None

    @field_validator("records")
    @classmethod
    def valid_records(cls, records):
        return validate_json_value(records)


class CleanRecordsInput(StrictModel):
    records: list[dict[str, JsonValue]] = Field(max_length=1000)
    required_fields: list[Annotated[str, StringConstraints(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=50
    )
    trim_fields: list[Annotated[str, StringConstraints(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=50
    )
    active_only: bool = True
    amount_field: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = None

    @field_validator("records")
    @classmethod
    def valid_records(cls, records):
        return validate_json_value(records)

    @field_validator("required_fields", "trim_fields")
    @classmethod
    def valid_paths(cls, paths):
        return [GetJsonFieldInput.valid_path(path) for path in paths]

    @field_validator("amount_field")
    @classmethod
    def valid_amount_path(cls, path):
        return GetJsonFieldInput.valid_path(path) if path else path


class CalculateTotalInput(StrictModel):
    values: list[int | float | str] = Field(max_length=1000)

    @field_validator("values", mode="before")
    @classmethod
    def valid_numbers(cls, values):
        if not isinstance(values, list):
            raise ValueError("Values must be an array of finite numbers.")
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ValueError("Values must be finite numbers or decimal strings.")
            try:
                number = Decimal(str(value))
            except InvalidOperation as exc:
                raise ValueError("Values must be finite numbers or decimal strings.") from exc
            if not number.is_finite():
                raise ValueError("Values must be finite numbers or decimal strings.")
        return values


class HttpRequestInput(StrictModel):
    url: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    query: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=50)
    body: JsonValue = None
    headers: dict[str, str] = Field(default_factory=dict, max_length=10)
    timeout_seconds: float = Field(default=5, ge=0.1, le=15)
    expected_status: list[int] = Field(default_factory=lambda: [200], min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_http_fields(self):
        validate_json_value(self.body)
        permitted = {"accept", "content-type", "if-none-match"}
        if any(key.lower() not in permitted for key in self.headers):
            raise ValueError("Only Accept, Content-Type, and If-None-Match headers are supported.")
        if any(not 100 <= code <= 599 for code in self.expected_status):
            raise ValueError("Expected status codes must be valid HTTP status codes.")
        return self


class WebhookInput(StrictModel):
    url: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    payload: JsonValue
    content_type: Literal["application/json"] = "application/json"
    timeout_seconds: float = Field(default=5, ge=0.1, le=15)

    @field_validator("payload")
    @classmethod
    def valid_payload(cls, value):
        return validate_json_value(value)


class RequireApprovalInput(StrictModel):
    value: JsonValue

    @field_validator("value")
    @classmethod
    def valid_value(cls, value):
        return validate_json_value(value)


class CreateResultInput(StrictModel):
    fields: dict[StepName, JsonValue] = Field(min_length=1, max_length=100)

    @field_validator("fields")
    @classmethod
    def valid_fields(cls, value):
        return validate_json_value(value)


class ValueOutput(StrictModel):
    value: JsonValue

    @field_validator("value")
    @classmethod
    def valid_value(cls, value):
        return validate_json_value(value)


class ObjectOutput(StrictModel):
    value: dict[str, JsonValue]

    @field_validator("value")
    @classmethod
    def valid_value(cls, value):
        return validate_json_value(value)


class StringListOutput(StrictModel):
    value: list[str] = Field(max_length=100)


class ActionErrorCategory(StrEnum):
    UNKNOWN_ACTION = "unknown_action"
    INVALID_INPUT = "invalid_input"
    INVALID_OUTPUT = "invalid_output"
    PERMISSION_DENIED = "permission_denied"
    EXECUTION_FAILED = "execution_failed"
    FIELD_NOT_FOUND = "field_not_found"
    INVALID_JSON = "invalid_json"
    DESTINATION_REJECTED = "destination_rejected"
    HTTP_TIMEOUT = "http_timeout"
    HTTP_CONNECTION_ERROR = "http_connection_error"
    HTTP_STATUS_ERROR = "http_status_error"
    SIZE_LIMIT = "size_limit"
    INVALID_COMPARISON = "invalid_comparison"


class TimeoutPolicy(StrEnum):
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
    name: str = ""
    category: str = "general"
    required_permissions: tuple[str, ...] = ()
    approval_required: bool = False
    timeout_policy: TimeoutPolicy = TimeoutPolicy.PROCESS_INTERRUPTIBLE
    timeout_seconds: float = 5.0
    max_attempts: int = 3
    retryable_categories: tuple[str, ...] = ("execution_failed", "action_timeout")
    backoff_base_seconds: float = 0.5
    idempotent_replay: bool = True
    version: str = "1.0"


def _return_value(inputs):
    return {"value": inputs["value"]}


def uppercase(inputs):
    return {"value": inputs["value"].upper()}


def append_text(inputs):
    return {"value": inputs["value"] + inputs["suffix"]}


def replace_text(inputs):
    search = inputs["search"]
    result = (
        inputs["value"] if not search else inputs["value"].replace(search, inputs["replacement"])
    )
    return {"value": result}


def concatenate_text(inputs):
    output = inputs["separator"].join(inputs["values"])
    if len(output) > 100_000:
        raise ValueError("Concatenated text exceeds the output limit.")
    return {"value": output}


def parse_json(inputs):
    try:
        value = json.loads(
            inputs["value"], parse_constant=lambda _item: (_ for _ in ()).throw(ValueError())
        )
        validate_json_value(value)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ActionFailure(
            ActionErrorCategory.INVALID_JSON, "Text is not valid bounded JSON."
        ) from exc
    return {"value": value}


def _path_parts(path: str) -> list[str]:
    if path.startswith("/"):
        return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]
    return re.findall(r"[^.\[\]]+|\[\d+\]", path) and [
        item[1:-1] if item.startswith("[") else item
        for item in re.findall(r"[^.\[\]]+|\[\d+\]", path)
    ]


def get_json_field(inputs):
    value = inputs["value"]
    for part in _path_parts(inputs["path"]):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise ActionFailure(
                ActionErrorCategory.FIELD_NOT_FOUND, "Requested field was not found."
            )
    return {"value": value}


def validate_email(inputs):
    value = inputs["value"]
    parsed_name, parsed = parseaddr(value)
    valid = bool(
        value
        and not parsed_name
        and parsed == value
        and len(value) <= 254
        and re.fullmatch(r"[^\s@<>]+@[^\s@<>]+", value)
        and "." in value.rsplit("@", 1)[1]
        and all(
            part and len(part) <= 63 and not part.startswith("-") and not part.endswith("-")
            for part in value.rsplit("@", 1)[1].split(".")
        )
    )
    return {
        "value": {
            "valid": valid,
            "message": "Valid email syntax."
            if valid
            else "Enter an email address in name@domain format.",
        }
    }


def validate_url(inputs):
    value = inputs["value"].strip()
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme.lower() in inputs["allowed_schemes"]
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not any(char.isspace() for char in value)
            and parsed.port in (None, 80, 443)
        )
    except ValueError:
        valid = False
    return {
        "value": {
            "valid": valid,
            "message": "Valid URL syntax."
            if valid
            else "URL is malformed or uses a disallowed scheme.",
        }
    }


def _is_empty(value):
    return value is None or value == "" or value == [] or value == {}


def _compare(left, operator, right):
    if operator == "is_empty":
        return _is_empty(left)
    if operator == "is_not_empty":
        return not _is_empty(left)
    if type(left) is not type(right):
        raise ActionFailure(
            ActionErrorCategory.INVALID_COMPARISON,
            "Comparison operands must have the same JSON type.",
        )
    if operator in {"gt", "gte", "lt", "lte"} and (
        isinstance(left, bool) or not isinstance(left, (str, int, float))
    ):
        raise ActionFailure(
            ActionErrorCategory.INVALID_COMPARISON,
            "Ordered comparisons support only strings and numbers.",
        )
    if operator == "eq":
        return left == right
    if operator == "ne":
        return left != right
    if operator == "gt":
        return left > right
    if operator == "gte":
        return left >= right
    if operator == "lt":
        return left < right
    return left <= right


def compare_values(inputs):
    return {
        "value": {
            "result": _compare(inputs["left"], inputs["operator"], inputs["right"]),
            "operator": inputs["operator"],
        }
    }


def filter_records(inputs):
    output = []
    for row in inputs["records"]:
        try:
            field = get_json_field({"value": row, "path": inputs["field"]})["value"]
        except ValueError:
            continue  # Missing fields do not match any filter.
        if _compare(field, inputs["operator"], inputs["value"]):
            output.append(row)
    validate_json_value(output)
    return {"value": output}


def clean_records(inputs):
    cleaned = []
    amounts = []
    removed = 0
    for original in inputs["records"]:
        row = json.loads(json.dumps(original, ensure_ascii=False, allow_nan=False))
        try:
            for path in inputs["required_fields"]:
                get_json_field({"value": row, "path": path})
            if inputs["active_only"] and row.get("active") is not True:
                removed += 1
                continue
            for path in inputs["trim_fields"]:
                item = get_json_field({"value": row, "path": path})["value"]
                if isinstance(item, str):
                    _set_json_field(row, path, item.strip())
            if inputs["amount_field"]:
                amount = get_json_field({"value": row, "path": inputs["amount_field"]})["value"]
                if isinstance(amount, bool) or not isinstance(amount, (int, float)):
                    removed += 1
                    continue
                if not Decimal(str(amount)).is_finite():
                    removed += 1
                    continue
                amounts.append(amount)
        except (ActionFailure, ValueError):
            removed += 1
            continue
        cleaned.append(row)
    result = {"records": cleaned, "amounts": amounts, "removed_count": removed}
    validate_json_value(result)
    return {"value": result}


def _set_json_field(value: dict, path: str, new_value) -> None:
    parts = _path_parts(path)
    current = value
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    last = parts[-1]
    if isinstance(current, list):
        current[int(last)] = new_value
    else:
        current[last] = new_value


def calculate_total(inputs):
    total = sum((Decimal(str(value)) for value in inputs["values"]), Decimal(0))
    as_float = float(total)
    if not math.isfinite(as_float):
        raise ValueError("Total exceeds the supported numeric range.")
    return {"value": as_float}


def _http_request(inputs):
    from agentflow.action_http import request_json

    return {"value": request_json(inputs)}


def _send_webhook(inputs):
    from agentflow.action_http import send_webhook

    return {"value": send_webhook(inputs)}


def require_approval(inputs):
    return {"value": inputs["value"]}


def create_result(inputs):
    return {"value": inputs["fields"]}


def _register(input_schema, output_schema, handler, name, category, description, **kwargs):
    return ActionSpec(
        input_schema,
        output_schema,
        handler,
        description,
        name=name,
        category=category,
        **kwargs,
    )


ACTION_REGISTRY: dict[str, ActionSpec] = {
    "set_value": _register(
        SetValueInput,
        ValueOutput,
        _return_value,
        "Set a value",
        "data",
        "Return a configured JSON value.",
    ),
    "uppercase": _register(
        TextInput,
        TextInput,
        uppercase,
        "Convert to uppercase",
        "text",
        "Convert text to uppercase.",
    ),
    "append": _register(
        AppendInput, TextInput, append_text, "Append text", "text", "Append a suffix to text."
    ),
    "trim_text": _register(
        TextInput,
        TextInput,
        lambda i: {"value": i["value"].strip()},
        "Trim text",
        "text",
        "Remove leading and trailing whitespace.",
    ),
    "replace_text": _register(
        ReplaceTextInput,
        TextInput,
        replace_text,
        "Replace text",
        "text",
        "Replace literal substrings; an empty search string leaves the text unchanged.",
    ),
    "concatenate_text": _register(
        ConcatenateInput,
        TextInput,
        concatenate_text,
        "Concatenate text",
        "text",
        "Join a bounded list of strings using a separator.",
    ),
    "parse_json": _register(
        ParseJsonInput,
        ValueOutput,
        parse_json,
        "Parse JSON",
        "data",
        "Parse bounded JSON text into structured data.",
    ),
    "get_json_field": _register(
        GetJsonFieldInput,
        ValueOutput,
        get_json_field,
        "Get JSON field",
        "data",
        "Extract a nested object key or array item using a JSON Pointer or simple path.",
    ),
    "validate_email": _register(
        ValidateEmailInput,
        ValueOutput,
        validate_email,
        "Validate email syntax",
        "validation",
        "Check email syntax only; mailbox existence is not verified.",
    ),
    "validate_url": _register(
        ValidateUrlInput,
        ValueOutput,
        validate_url,
        "Validate URL syntax",
        "validation",
        "Validate URL syntax and allowed scheme without making a network request.",
    ),
    "compare_values": _register(
        CompareValuesInput,
        ValueOutput,
        compare_values,
        "Compare values",
        "logic",
        "Compare same-typed JSON values using an explicit operator.",
    ),
    "filter_records": _register(
        FilterRecordsInput,
        ValueOutput,
        filter_records,
        "Filter records",
        "data",
        "Filter a bounded list of object records; missing fields do not match.",
    ),
    "clean_records": _register(
        CleanRecordsInput,
        ValueOutput,
        clean_records,
        "Clean records",
        "data",
        (
            "Drop incomplete or inactive records, trim selected text fields, "
            "and extract finite amounts."
        ),
    ),
    "calculate_total": _register(
        CalculateTotalInput,
        ValueOutput,
        calculate_total,
        "Calculate total",
        "math",
        "Sum finite numeric values using decimal arithmetic before JSON number conversion.",
    ),
    "http_request": _register(
        HttpRequestInput,
        ValueOutput,
        _http_request,
        "Make HTTP request",
        "integration",
        "Call an administrator-allowlisted HTTPS destination with bounded response data.",
        timeout_seconds=15,
        max_attempts=1,
        idempotent_replay=False,
    ),
    "send_webhook": _register(
        WebhookInput,
        ValueOutput,
        _send_webhook,
        "Send webhook",
        "integration",
        "Send a bounded JSON webhook to an administrator-allowlisted HTTPS destination.",
        approval_required=True,
        timeout_seconds=15,
        max_attempts=1,
        idempotent_replay=False,
    ),
    "require_approval": _register(
        RequireApprovalInput,
        ValueOutput,
        require_approval,
        "Require approval",
        "control",
        "Pause this workflow until an authorized approver approves the value.",
        approval_required=True,
    ),
    "create_result": _register(
        CreateResultInput,
        ObjectOutput,
        create_result,
        "Create result",
        "output",
        "Return named structured fields as the workflow's final result.",
    ),
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


def action_requires_approval(action: str) -> bool:
    spec = action_spec(action)
    configured = set(get_settings().approval_required_actions)
    unknown = configured - set(ACTION_REGISTRY)
    if unknown:
        raise ValueError(
            f"APPROVAL_REQUIRED_ACTIONS contains unregistered actions: {', '.join(sorted(unknown))}"
        )
    return spec.approval_required or action in configured


def validate_step(step, prior_ids: set[str]) -> None:
    spec = action_spec(step.action)

    def substitute(value, key):
        if isinstance(value, dict):
            if "$ref" in value:
                ref = value["$ref"]
                ref_step = ref.get("step") if isinstance(ref, dict) else ref
                if ref_step not in prior_ids:
                    raise ValueError("step reference must point to an earlier step")
                return _example_input(spec.input_schema, key)
            if "$input" in value:
                return _example_input(spec.input_schema, key)
            return {name: substitute(item, key) for name, item in value.items()}
        if isinstance(value, list):
            return [substitute(item, key) for item in value]
        return value

    raw = {key: substitute(value, key) for key, value in step.inputs.items()}
    spec.input_schema.model_validate(raw)


def _example_input(schema: type[BaseModel], key: str):
    # References are validated at runtime against resolved values. During
    # definition validation, use a small representative value of the field's
    # declared shape so structured actions remain composable.
    if key == "records":
        return [{"active": True, "value": 1}]
    if key == "values":
        return [1]
    if key == "fields":
        return {"result": "reference"}
    field = schema.model_fields.get(key)
    annotation = field.annotation if field else str
    if annotation is str:
        return "reference"
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    if annotation is bool:
        return True
    if annotation is list[str] or "list" in str(annotation).lower():
        return ["reference"]
    return {}


def run_action(
    action: str, inputs: dict[str, Any], *, permissions: frozenset[str] = frozenset()
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
    except ActionFailure:
        raise
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
            "name": spec.name,
            "category": spec.category,
            "description": spec.description,
            "version": spec.version,
            "input_schema": spec.input_schema.model_json_schema(),
            "output_schema": spec.output_schema.model_json_schema(),
            "required_permissions": list(spec.required_permissions),
            "approval_required": action_requires_approval(key),
            "timeout_policy": spec.timeout_policy.value,
            "timeout_seconds": action_timeout(key),
            "max_attempts": spec.max_attempts,
            "retryable_categories": list(spec.retryable_categories),
            "backoff_base_seconds": spec.backoff_base_seconds,
            "idempotent_replay": spec.idempotent_replay,
        }
        for key, spec in ACTION_REGISTRY.items()
    ]
