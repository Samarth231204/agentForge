"""Store for per-session Gmail OAuth credentials.

Two interchangeable backends behind the same save/get/has/delete contract,
selected by whether REDIS_URL is configured (see config.py):

- TokenStore: in-memory, thread-safe. Nothing survives a backend restart,
  and there is no cross-instance sharing. This was the whole store before
  Redis support existed, and remains the zero-dependency default for local
  dev when REDIS_URL is unset.
- RedisTokenStore: same contract, backed by Redis with a TTL that slides
  forward on every read, so an actively-used session's token doesn't expire
  mid-use, but an abandoned one is erased automatically without any explicit
  cleanup — and it survives a backend restart, unlike TokenStore.
"""

from __future__ import annotations

import json
from functools import lru_cache
from threading import Lock
from typing import Any, Protocol


class TokenStoreProtocol(Protocol):
    def save(self, session_id: str, credentials: dict[str, Any]) -> None: ...
    def get(self, session_id: str) -> dict[str, Any] | None: ...
    def has(self, session_id: str) -> bool: ...
    def delete(self, session_id: str) -> None: ...


class TokenStore:
    def __init__(self) -> None:
        self._tokens: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    def save(self, session_id: str, credentials: dict[str, Any]) -> None:
        with self._lock:
            self._tokens[session_id] = credentials

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._tokens.get(session_id)

    def has(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._tokens

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._tokens.pop(session_id, None)


class RedisTokenStore:
    _KEY_PREFIX = "agentforge:gmail_token:"

    def __init__(self, redis_client: Any, ttl_seconds: int) -> None:
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds

    @classmethod
    def _key(cls, session_id: str) -> str:
        return f"{cls._KEY_PREFIX}{session_id}"

    def save(self, session_id: str, credentials: dict[str, Any]) -> None:
        self._redis.set(self._key(session_id), json.dumps(credentials), ex=self._ttl_seconds)

    def get(self, session_id: str) -> dict[str, Any] | None:
        raw = self._redis.get(self._key(session_id))
        if raw is None:
            return None
        # Sliding expiration: an active session keeps renewing its own TTL on
        # every read, so it only ever expires after a period of no use.
        self._redis.expire(self._key(session_id), self._ttl_seconds)
        return json.loads(raw)

    def has(self, session_id: str) -> bool:
        return bool(self._redis.exists(self._key(session_id)))

    def delete(self, session_id: str) -> None:
        self._redis.delete(self._key(session_id))


_memory_store = TokenStore()


@lru_cache(maxsize=1)
def get_token_store() -> TokenStoreProtocol:
    """Process-wide singleton, mirroring config.get_settings()'s pattern.

    Import of backend.config and redis is deferred into this function (rather
    than module-level) so importing token_store never requires either — the
    in-memory TokenStore stays usable standalone with zero dependencies.
    """
    from backend.config import get_settings

    settings = get_settings()
    if not settings.redis_url:
        return _memory_store

    import redis

    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return RedisTokenStore(client, settings.gmail_token_ttl_seconds)
