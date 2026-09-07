"""Store for per-session task history (Phase 4).

Two interchangeable backends behind the same append/list contract, selected
by whether REDIS_URL is configured (see config.py) — the exact same choice
already made for Gmail tokens in backend/auth/token_store.py:

- InMemoryHistoryStore: in-memory, thread-safe. Nothing survives a backend
  restart, and there is no cross-instance sharing. Zero-dependency default
  for local dev when REDIS_URL is unset.
- RedisHistoryStore: same contract, backed by a Redis list. Unlike Gmail
  tokens, entries have no TTL — task history is inert historical text with
  no expiry reason, and the whole point of this store is that it survives
  across sessions, which a TTL would silently defeat. Growth is bounded
  instead by capping each session's list at the most recent
  MAX_ENTRIES_PER_SESSION entries.
"""

from __future__ import annotations

import json
from functools import lru_cache
from threading import Lock
from typing import Any, Protocol

MAX_ENTRIES_PER_SESSION = 50


class HistoryStoreProtocol(Protocol):
    def append(self, session_id: str, entry: dict[str, Any]) -> None: ...
    def list(self, session_id: str) -> list[dict[str, Any]]: ...


class InMemoryHistoryStore:
    def __init__(self) -> None:
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._lock = Lock()

    def append(self, session_id: str, entry: dict[str, Any]) -> None:
        with self._lock:
            entries = self._history.setdefault(session_id, [])
            entries.append(entry)
            del entries[:-MAX_ENTRIES_PER_SESSION]

    def list(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history.get(session_id, []))


class RedisHistoryStore:
    _KEY_PREFIX = "agentforge:task_history:"

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    @classmethod
    def _key(cls, session_id: str) -> str:
        return f"{cls._KEY_PREFIX}{session_id}"

    def append(self, session_id: str, entry: dict[str, Any]) -> None:
        key = self._key(session_id)
        self._redis.rpush(key, json.dumps(entry))
        # Keep only the most recent MAX_ENTRIES_PER_SESSION — LTRIM keeps the
        # inclusive range [start, end], so the negative index is "N from the end".
        self._redis.ltrim(key, -MAX_ENTRIES_PER_SESSION, -1)

    def list(self, session_id: str) -> list[dict[str, Any]]:
        raw_entries = self._redis.lrange(self._key(session_id), 0, -1)
        return [json.loads(raw) for raw in raw_entries]


_memory_store = InMemoryHistoryStore()


@lru_cache(maxsize=1)
def get_history_store() -> HistoryStoreProtocol:
    """Process-wide singleton, mirroring token_store.get_token_store()'s
    pattern. Import of backend.config and redis is deferred into this
    function so importing history_store never requires either — the
    in-memory store stays usable standalone with zero dependencies.
    """
    from backend.config import get_settings

    settings = get_settings()
    if not settings.redis_url:
        return _memory_store

    import redis

    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return RedisHistoryStore(client)
