"""Validated application settings sourced from the environment."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./agentflow.db"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8001, ge=1, le=65535)
    cors_origins: list[str] = ["http://localhost:3000"]
    action_timeouts: dict[str, float] = Field(default_factory=dict)
    approval_required_actions: list[str] = Field(default_factory=list)
    session_cookie_secure: bool = True
    session_ttl_hours: int = Field(default=12, ge=1, le=168)
    max_request_body_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)
    max_runs_per_hour: int = Field(default=100, ge=1, le=10_000)
    approval_ttl_hours: int = Field(default=24, ge=1, le=168)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("database_url")
    @classmethod
    def require_database_url(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("DATABASE_URL must not be empty")
        return value

    @field_validator("action_timeouts")
    @classmethod
    def validate_action_timeouts(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not 0 < timeout <= 300 for timeout in value.values()):
            raise ValueError("ACTION_TIMEOUTS values must be greater than 0 and at most 300")
        if any(not name or len(name) > 80 for name in value):
            raise ValueError("ACTION_TIMEOUTS keys must be action names up to 80 characters")
        return value

    @field_validator("approval_required_actions")
    @classmethod
    def normalize_approval_actions(cls, value: list[str]) -> list[str]:
        if any(not name or len(name) > 80 for name in value):
            raise ValueError("APPROVAL_REQUIRED_ACTIONS must contain action names up to 80 chars")
        return sorted(set(value))


@lru_cache
def get_settings() -> Settings:
    return Settings()
