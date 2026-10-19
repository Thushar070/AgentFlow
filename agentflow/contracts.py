"""Stable API response contracts for persisted workflow data."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class WorkflowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime
    current_version_number: int
    definition: dict[str, Any]


class WorkflowVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workflow_id: int
    version_number: int
    definition: dict[str, Any]
    created_at: datetime


class StepAttemptResponse(BaseModel):
    attempt_number: int
    status: str
    output: dict[str, Any] | None
    error: str | None
    error_category: str | None
    started_at: datetime
    finished_at: datetime | None


class StepRunResponse(BaseModel):
    id: str
    action: str
    status: str
    inputs: dict[str, Any]
    output: dict[str, Any] | None
    error: str | None
    error_category: str | None
    started_at: datetime | None
    finished_at: datetime | None
    attempts: list[StepAttemptResponse]


class RunResponse(BaseModel):
    id: int
    workflow_id: int
    workflow_version_id: int
    workflow_version_number: int
    status: str
    input: dict[str, Any] | None
    output: dict[str, Any] | None
    error: str | None
    error_category: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None
    summary: "RunSummary"
    steps: list[StepRunResponse]


class RunSummary(BaseModel):
    total_steps: int
    succeeded_steps: int
    failed_steps: int
    not_started_steps: int
