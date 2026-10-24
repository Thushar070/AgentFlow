"""Validated application settings sourced from the environment."""

import ipaddress
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url

from pydantic import AliasChoices, Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_environment: Literal["development", "test", "production"] = Field(
        default="development",
        validation_alias=AliasChoices("APP_ENV", "APP_ENVIRONMENT"),
    )
    database_url: str = (
        "postgresql+psycopg://agentflow:replace-with-a-random-password@127.0.0.1:5432/agentflow"
    )
    database_url_unpooled: str | None = None
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    db_pool_timeout_seconds: float = Field(default=10, ge=1, le=120)
    redis_url: SecretStr | None = None
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
    http_allowed_hosts: list[str] = Field(default_factory=list)
    http_allowed_ports: list[int] = Field(default_factory=lambda: [443])
    http_max_response_bytes: int = Field(default=262_144, ge=1024, le=1_048_576)
    freellmapi_base_url: str = "http://localhost:3001/v1"
    freellmapi_api_key: SecretStr | None = None
    # The gateway's `auto` selector honors its active fallback chain; never pin
    # the unified API key to one upstream provider/model.
    freellmapi_model: Literal["auto"] = "auto"
    freellmapi_timeout_seconds: float = Field(default=120, ge=1, le=300)
    ollama_base_url: str = "http://localhost:11434/v1"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("database_url")
    @classmethod
    def require_database_url(cls, value: str, info: ValidationInfo) -> str:
        if not value.strip():
            raise ValueError("DATABASE_URL must not be empty")
        if value.startswith("sqlite:"):
            if info.data.get("app_environment") != "test":
                raise ValueError("SQLite DATABASE_URL is allowed only in isolated test mode.")
            return value
        return cls._normalize_postgres_url(value)

    @field_validator("database_url_unpooled")
    @classmethod
    def validate_unpooled_database_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        if value.startswith("sqlite:"):
            raise ValueError("DATABASE_URL_UNPOOLED must use PostgreSQL.")
        return cls._normalize_postgres_url(value)

    @field_validator("api_host")
    @classmethod
    def require_loopback_api_host(cls, value: str) -> str:
        host = value.strip().lower().strip("[]")
        try:
            loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError("API_HOST must be localhost or a loopback IP address.")
        return host

    @field_validator("cors_origins")
    @classmethod
    def require_loopback_cors_origins(cls, value: list[str]) -> list[str]:
        origins: list[str] = []
        for origin in value:
            parsed = urlsplit(origin.strip())
            host = parsed.hostname or ""
            try:
                loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = False
            if (
                not loopback
                or parsed.scheme not in {"http", "https"}
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or origin != origin.strip()
            ):
                raise ValueError("CORS_ORIGINS may contain only exact localhost/loopback origins.")
            origins.append(origin)
        if not origins:
            raise ValueError("CORS_ORIGINS must include at least one local browser origin.")
        return sorted(set(origins))

    @staticmethod
    def _normalize_postgres_url(value: str) -> str:
        try:
            parsed = make_url(value)
        except Exception as exc:
            raise ValueError("Database URL must be a valid PostgreSQL connection URL.") from exc
        if parsed.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
            raise ValueError("Database URL must use PostgreSQL with the psycopg driver.")
        return parsed.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)

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

    @field_validator("http_allowed_hosts")
    @classmethod
    def normalize_http_hosts(cls, value: list[str]) -> list[str]:
        hosts = [item.strip().lower().rstrip(".") for item in value]
        if any(
            not item or item == "*" or "*" in item or "/" in item or ":" in item for item in hosts
        ):
            raise ValueError("HTTP_ALLOWED_HOSTS must contain bare hostnames without ports.")
        return sorted(set(hosts))

    @field_validator("http_allowed_ports")
    @classmethod
    def valid_http_ports(cls, value: list[int]) -> list[int]:
        if not value or any(not 1 <= port <= 65535 for port in value):
            raise ValueError("HTTP_ALLOWED_PORTS must contain valid port numbers.")
        return sorted(set(value))

    @model_validator(mode="after")
    def require_secure_production_cookie(self):
        if self.app_environment == "production" and not self.session_cookie_secure:
            raise ValueError("SESSION_COOKIE_SECURE must be true in production.")
        return self

    @field_validator("freellmapi_base_url")
    @classmethod
    def validate_freellmapi_base_url(cls, value: str) -> str:
        return cls._validate_local_openai_base_url(value, "FREELLMAPI_BASE_URL")

    @field_validator("ollama_base_url")
    @classmethod
    def validate_ollama_base_url(cls, value: str) -> str:
        return cls._validate_local_openai_base_url(value, "OLLAMA_BASE_URL")

    @staticmethod
    def _validate_local_openai_base_url(value: str, setting: str) -> str:
        parsed = urlsplit(value.strip().rstrip("/"))
        hostname = parsed.hostname
        try:
            is_loopback = (
                hostname == "localhost" or ipaddress.ip_address(hostname or "").is_loopback
            )
        except ValueError:
            is_loopback = False
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or not is_loopback
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path.rstrip("/") != "/v1"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                f"{setting} must be loopback with a /v1 path and no credentials."
            )
        return f"{parsed.scheme}://{parsed.netloc}/v1"

@lru_cache
def get_settings() -> Settings:
    return Settings()
