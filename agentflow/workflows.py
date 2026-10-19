"""Versioned workflow contract and definition validation."""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

StepId = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,79}$")]


class StepDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: StepId
    action: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    inputs: dict[str, Any] = Field(default_factory=dict)


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(default=1, ge=1, le=1)
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    description: Annotated[
        str | None, StringConstraints(strip_whitespace=True, max_length=1000)
    ] = None
    steps: list[StepDefinition] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_step_ids_and_references(self):
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen:
                raise ValueError(f"duplicate step id: {step.id}")
            for value in step.inputs.values():
                if isinstance(value, dict) and "$ref" in value:
                    if set(value) != {"$ref"} or value["$ref"] not in seen:
                        raise ValueError(
                            "step references must point to an earlier step using only $ref"
                        )
            seen.add(step.id)
        return self


def validate_definition(value: dict[str, Any]) -> WorkflowDefinition:
    """Validate structure, allowlisted actions, action inputs, and references."""
    definition = WorkflowDefinition.model_validate(value)
    from agentflow.registry import validate_step

    prior_ids: set[str] = set()
    for step in definition.steps:
        validate_step(step, prior_ids)
        prior_ids.add(step.id)
    return definition
