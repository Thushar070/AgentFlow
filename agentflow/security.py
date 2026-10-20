"""Shared request bounds and redaction helpers for untrusted data."""

import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

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
