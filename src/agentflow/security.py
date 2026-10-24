"""Shared request bounds and redaction helpers for untrusted data."""

import json
import re
import uuid
import asyncio
import hashlib
import time
from collections.abc import Mapping
from threading import Lock
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from agentflow.config import get_settings

SENSITIVE_KEY = re.compile(
    r"password|passwd|secret|token|authorization|cookie|api[_-]?key|credential|private[_-]?key",
    re.IGNORECASE,
)
TOKEN_VALUE = re.compile(
    r"(?i)\b(?:bearer\s+[A-Za-z0-9._~+/-]{8,}|sk-[A-Za-z0-9_-]{12,}|"
    r"gh[pousr]_[A-Za-z0-9_]{16,}|"
    r"AKIA[A-Z0-9]{16})"
)
ASSIGNMENT_SECRET = re.compile(
    r"(?i)(\b(?:password|passwd|secret|token|authorization|api[_-]?key|credential)\s*[:=]\s*)"
    r"([^\s,;&]+)"
)
REDACTED = "[REDACTED]"


def redact_text(value: str) -> str:
    return ASSIGNMENT_SECRET.sub(r"\1" + REDACTED, TOKEN_VALUE.sub(REDACTED, value))


def redact(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact recognizable credential fields and token patterns."""
    if key is not None and SENSITIVE_KEY.search(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {str(name): redact(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def canonical_hash(value: Any) -> str:
    import hashlib

    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def json_depth(value: Any, *, max_depth: int = 32) -> bool:
    """Return true when an already-parsed JSON value exceeds the depth bound."""
    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > max_depth:
            return True
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
    return False


class RequestLimitMiddleware:
    """Bound streamed request bodies before buffering them and attach a safe correlation id."""

    def __init__(self, app, *, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        supplied = headers.get(b"x-correlation-id", b"").decode("ascii", errors="ignore")
        correlation = (
            supplied if re.fullmatch(r"[A-Za-z0-9._-]{1,80}", supplied) else str(uuid.uuid4())
        )
        content_length = headers.get(b"content-length", b"").decode("ascii", errors="ignore")
        if content_length.isdigit() and int(content_length) > self.max_bytes:
            await self._reject(send, 413, "Request body is too large.", correlation)
            return

        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                await self._reject(send, 413, "Request body is too large.", correlation)
                return
            more_body = message.get("more_body", False)

        method = scope.get("method", "GET").upper()
        if method not in {"GET", "HEAD", "OPTIONS"}:
            content_type = headers.get(b"content-type", b"").lower()
            if content_type.startswith(b"application/json"):
                try:
                    parsed = json.loads(body)
                except (json.JSONDecodeError, RecursionError):
                    await self._reject(send, 400, "Malformed JSON request.", correlation)
                    return
                if json_depth(parsed):
                    await self._reject(send, 422, "JSON nesting limit exceeded.", correlation)
                    return

        sent = False

        async def replay_body():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        scope.setdefault("state", {})["correlation_id"] = correlation

        async def add_correlation(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-correlation-id", correlation.encode("ascii"))
                ]
            await send(message)

        await self.app(scope, replay_body, add_correlation)

    async def _reject(self, send, status: int, message: str, correlation: str):
        body = json.dumps({"detail": message, "correlation_id": correlation}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"x-correlation-id", correlation.encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class ApiRateLimitMiddleware:
    """Apply shared Redis quotas with a bounded process-local fallback."""

    _memory: dict[str, tuple[int, int]] = {}
    _lock = Lock()
    _increment_script = """
    for i, key in ipairs(KEYS) do
      local count = redis.call('INCR', key)
      if count == 1 then redis.call('EXPIRE', key, tonumber(ARGV[(i - 1) * 2 + 1])) end
      if count > tonumber(ARGV[(i - 1) * 2 + 2]) then return i end
    end
    return 0
    """

    def __init__(self, app):
        self.app = app
        redis_url = get_settings().redis_url
        self.redis = (
            Redis.from_url(
                redis_url.get_secret_value(),
                socket_connect_timeout=0.1,
                socket_timeout=0.1,
                health_check_interval=30,
            )
            if redis_url is not None
            else None
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        if not path.startswith("/api/") or method in {"OPTIONS", "HEAD"}:
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        address = client[0] if client else "unknown"
        address_key = hashlib.sha256(address.encode("utf-8")).hexdigest()[:24]
        if path == "/api/workflows/proposals" and method == "POST":
            windows = ((60, 10), (3600, 60))
            category = "ai"
        elif path == "/api/auth/login" and method == "POST":
            windows = ((60, 20), (900, 100))
            category = "login"
        else:
            windows = ((60, 300),)
            category = "api"

        now = int(time.time())
        keys = [
            f"agentflow:rl:v1:{category}:{address_key}:{seconds}:{now // seconds}"
            for seconds, _limit in windows
        ]
        limits = [item for pair in windows for item in pair]
        try:
            limited_window = await asyncio.to_thread(self._redis_increment, keys, limits)
        except RedisError:
            limited_window = self._memory_increment(keys, windows, now)

        if limited_window:
            retry_after = windows[limited_window - 1][0]
            body = json.dumps({"detail": "Request rate limit exceeded."}).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                        (b"retry-after", str(retry_after).encode("ascii")),
                        (b"cache-control", b"no-store"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)

    def _redis_increment(self, keys: list[str], limits: list[int]) -> int:
        if self.redis is None:
            raise RedisError("Redis rate limiting is not configured")
        return int(self.redis.eval(self._increment_script, len(keys), *keys, *limits))

    @classmethod
    def _memory_increment(cls, keys, windows, now: int) -> int:
        with cls._lock:
            if len(cls._memory) > 20_000:
                cls._memory = {
                    key: value for key, value in cls._memory.items() if value[1] > now
                }
                if len(cls._memory) > 20_000:
                    cls._memory.clear()
            for index, ((seconds, limit), key) in enumerate(zip(windows, keys), start=1):
                expiry = now - (now % seconds) + seconds
                count, existing_expiry = cls._memory.get(key, (0, expiry))
                if existing_expiry <= now:
                    count = 0
                count += 1
                cls._memory[key] = (count, expiry)
                if count > limit:
                    return index
        return 0


class SecurityHeadersMiddleware:
    """Add browser defense headers and prevent private API responses caching."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def add_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                        (b"cross-origin-resource-policy", b"same-origin"),
                    ]
                )
                path = scope.get("path", "")
                if path.startswith("/api/") or path == "/health":
                    headers.append((b"cache-control", b"no-store"))
                if scope.get("scheme") == "https":
                    headers.append(
                        (b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                    )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, add_headers)
