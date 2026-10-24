"""Bounded outbound HTTP requests pinned to vetted, allowlisted destinations."""

import ipaddress
import json
import socket
from urllib.parse import urlsplit

import httpcore
import httpx

from agentflow.config import get_settings
from agentflow.registry import ActionErrorCategory, ActionFailure, validate_json_value
from agentflow.security import redact

SAFE_RESPONSE_HEADERS = {"content-type", "etag", "last-modified", "cache-control"}


class _PinnedBackend(httpcore.SyncBackend):
    """Resolve once before the request and connect only to the vetted address."""

    def __init__(self, addresses: dict[str, str]):
        self.addresses = addresses

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        address = self.addresses.get(host.lower())
        if address is None:
            raise httpcore.ConnectError("Destination was not validated")
        return super().connect_tcp(address, port, timeout, local_address, socket_options)


class _PinnedTransport(httpx.HTTPTransport):
    def __init__(self, addresses: dict[str, str]):
        super().__init__(
            verify=True,
            trust_env=False,
            retries=0,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        )
        self._pool.close()
        self._pool = httpcore.ConnectionPool(
            max_connections=1,
            max_keepalive_connections=0,
            retries=0,
            network_backend=_PinnedBackend(addresses),
        )


def _destination(url: str) -> tuple[str, int, str, bool]:
    settings = get_settings()
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ActionFailure(ActionErrorCategory.INVALID_INPUT, "Request URL is malformed.") from exc
    if (
        parsed.scheme not in {"https", "http"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or any(char.isspace() for char in url)
    ):
        raise ActionFailure(
            ActionErrorCategory.INVALID_INPUT, "Request URL is malformed or unsafe."
        )
    if host not in settings.http_allowed_hosts:
        raise ActionFailure(
            ActionErrorCategory.DESTINATION_REJECTED,
            "Request destination is not in the administrator-configured host allowlist.",
        )
    if port not in settings.http_allowed_ports:
        raise ActionFailure(
            ActionErrorCategory.DESTINATION_REJECTED, "Request destination port is not allowed."
        )
    local_test = settings.app_environment == "test" and host in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not local_test:
        raise ActionFailure(
            ActionErrorCategory.DESTINATION_REJECTED, "HTTPS is required for outbound requests."
        )
    try:
        literal = ipaddress.ip_address(host)
        resolved = [str(literal)]
    except ValueError:
        try:
            resolved = sorted(
                {
                    item[4][0].split("%", 1)[0]
                    for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
                }
            )
        except OSError as exc:
            raise ActionFailure(
                ActionErrorCategory.EXECUTION_FAILED, "Request destination could not be resolved."
            ) from exc
    if not resolved:
        raise ActionFailure(
            ActionErrorCategory.EXECUTION_FAILED, "Request destination could not be resolved."
        )
    for address in resolved:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ActionFailure(
                ActionErrorCategory.DESTINATION_REJECTED, "Request destination resolved unsafely."
            ) from exc
        if local_test and ip.is_loopback:
            continue
        if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_link_local:
            raise ActionFailure(
                ActionErrorCategory.DESTINATION_REJECTED,
                "Request destination resolved to a restricted network address.",
            )
    # Pin the request to the vetted address so a second DNS lookup cannot rebind it.
    return host, port, resolved[0], local_test


def _perform(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
    max_bytes: int,
):
    host, port, address, _local_test = _destination(url)
    transport = _PinnedTransport({host: address})
    try:
        with httpx.Client(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 3.0)),
            max_redirects=0,
        ) as client:
            with client.stream(method, url, headers=headers, content=body) as response:
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ActionFailure(
                            ActionErrorCategory.SIZE_LIMIT,
                            "Response exceeded the configured size limit.",
                        )
                    chunks.append(chunk)
                raw = b"".join(chunks)
                response_headers = {
                    key.lower(): value
                    for key, value in response.headers.items()
                    if key.lower() in SAFE_RESPONSE_HEADERS
                }
                content_type = (
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                )
                if content_type == "application/json" or content_type.endswith("+json"):
                    try:
                        payload = json.loads(raw)
                        validate_json_value(payload)
                    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
                        raise ActionFailure(
                            ActionErrorCategory.INVALID_JSON,
                            "Response contained malformed or oversized JSON.",
                        ) from exc
                    payload = redact(payload)
                else:
                    payload = redact(raw.decode("utf-8", errors="replace"))
                return response, {
                    "status_code": response.status_code,
                    "headers": response_headers,
                    "body": payload,
                }
    except ActionFailure:
        raise
    except httpx.TimeoutException as exc:
        raise ActionFailure(
            ActionErrorCategory.HTTP_TIMEOUT, "Outbound request timed out."
        ) from exc
    except httpx.HTTPError as exc:
        raise ActionFailure(
            ActionErrorCategory.HTTP_CONNECTION_ERROR, "Outbound request failed."
        ) from exc


def request_json(inputs: dict) -> dict:
    settings = get_settings()
    url = str(httpx.URL(inputs["url"]).copy_merge_params(inputs["query"]))
    body = None
    headers = {key: value for key, value in inputs["headers"].items()}
    if inputs["body"] is not None:
        body = json.dumps(inputs["body"], ensure_ascii=False, separators=(",", ":")).encode()
        headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept", "application/json, text/plain;q=0.9, */*;q=0.1")
    response, output = _perform(
        url,
        method=inputs["method"],
        headers=headers,
        body=body,
        timeout=inputs["timeout_seconds"],
        max_bytes=settings.http_max_response_bytes,
    )
    if response.status_code not in inputs["expected_status"]:
        raise ActionFailure(
            ActionErrorCategory.HTTP_STATUS_ERROR,
            "Outbound request returned an unexpected status code.",
        )
    return output


def send_webhook(inputs: dict) -> dict:
    settings = get_settings()
    body = json.dumps(inputs["payload"], ensure_ascii=False, separators=(",", ":")).encode()
    response, output = _perform(
        inputs["url"],
        method="POST",
        headers={
            "Content-Type": inputs["content_type"],
            "Accept": "application/json, text/plain;q=0.9",
        },
        body=body,
        timeout=inputs["timeout_seconds"],
        max_bytes=settings.http_max_response_bytes,
    )
    if not 200 <= response.status_code < 300:
        raise ActionFailure(
            ActionErrorCategory.HTTP_STATUS_ERROR,
            "Webhook receiver returned an unsuccessful status code.",
        )
    return output
