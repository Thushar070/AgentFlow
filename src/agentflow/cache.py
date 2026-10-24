"""Small, fail-open Redis cache for stable API reference data."""

import json
import logging
import time
from collections.abc import Callable
from functools import lru_cache
from typing import TypeVar

from redis import Redis
from redis.exceptions import RedisError

from agentflow.config import get_settings

logger = logging.getLogger("agentflow.cache")
T = TypeVar("T")
CACHE_TTL_SECONDS = 300
_CACHE_RETRY_DELAY_SECONDS = 30
_cache_disabled_until = 0.0


@lru_cache(maxsize=1)
def _client() -> Redis | None:
    url = get_settings().redis_url
    if url is None:
        return None
    try:
        return Redis.from_url(
            url.get_secret_value(),
            decode_responses=True,
            socket_connect_timeout=0.1,
            socket_timeout=0.1,
            health_check_interval=30,
        )
    except (RedisError, ValueError):
        logger.warning("Redis URL is invalid; reference data will use its source")
        return None


def cached_json(key: str, loader: Callable[[], T]) -> T:
    """Return JSON-safe reference data from Redis, falling back to its source."""
    global _cache_disabled_until

    client = _client()
    if client is None or time.monotonic() < _cache_disabled_until:
        return loader()

    try:
        cached = client.get(key)
        if cached is not None:
            try:
                return json.loads(cached)
            except json.JSONDecodeError:
                logger.warning("Redis cache value for key %s was invalid JSON", key)
    except RedisError:
        logger.warning("Redis cache read failed for key %s; loading source data", key)
        _cache_disabled_until = time.monotonic() + _CACHE_RETRY_DELAY_SECONDS
        return loader()

    payload = loader()
    try:
        client.set(key, json.dumps(payload, separators=(",", ":")), ex=CACHE_TTL_SECONDS)
    except RedisError:
        logger.warning("Redis cache write failed for key %s", key)
        _cache_disabled_until = time.monotonic() + _CACHE_RETRY_DELAY_SECONDS
    except (TypeError, ValueError):
        logger.exception("Redis cache payload for key %s was not JSON serializable", key)
    return payload
