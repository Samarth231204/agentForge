"""Intra-pipeline step handoff state (Phase 9).

Where a multi-step pipeline's step outputs live between one blueprint step
(backend/blueprints.py) finishing and the next one starting, once the Phase
11 executor exists to run a full plan. This is deliberately NOT Mem0: Mem0
does fuzzy semantic recall, the wrong tool for "step 2 needs this exact
value step 1 produced" — this store is exact, structured key-value state,
the same shape backend/history_store.py and backend/auth/token_store.py
already use for their own per-session state.

The one deliberate difference from both of those: entries here have no TTL
and are not meant to survive past their own pipeline run. A run's state is
explicitly deleted (delete_run) once that run finishes, success or failure
— this is working state for one in-flight pipeline, not a durable record
like task history or a lasting credential like a Gmail token, so there is
nothing to keep around once the run that created it is over.
"""

from __future__ import annotations

import json
from functools import lru_cache
from threading import Lock
from typing import Any, Protocol


class PipelineStateStoreProtocol(Protocol):
    def set_step_result(self, run_id: str, step_name: str, result: Any) -> None: ...
    def get_step_result(self, run_id: str, step_name: str) -> Any | None: ...
    def get_all_results(self, run_id: str) -> dict[str, Any]: ...
    def delete_run(self, run_id: str) -> None: ...


class InMemoryPipelineStateStore:
    def __init__(self) -> None:
        self._state: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    def set_step_result(self, run_id: str, step_name: str, result: Any) -> None:
        with self._lock:
            self._state.setdefault(run_id, {})[step_name] = result

    def get_step_result(self, run_id: str, step_name: str) -> Any | None:
        with self._lock:
            return self._state.get(run_id, {}).get(step_name)

    def get_all_results(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._state.get(run_id, {}))

    def delete_run(self, run_id: str) -> None:
        with self._lock:
            self._state.pop(run_id, None)


class RedisPipelineStateStore:
    """One Redis hash per pipeline run — fields are step names, values are
    JSON-encoded step results (so a step can hand off a string, a list, or
    a dict, not just plain text)."""

    _KEY_PREFIX = "agentforge:pipeline_state:"

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    @classmethod
    def _key(cls, run_id: str) -> str:
        return f"{cls._KEY_PREFIX}{run_id}"

    def set_step_result(self, run_id: str, step_name: str, result: Any) -> None:
        self._redis.hset(self._key(run_id), step_name, json.dumps(result))

    def get_step_result(self, run_id: str, step_name: str) -> Any | None:
        raw = self._redis.hget(self._key(run_id), step_name)
        return None if raw is None else json.loads(raw)

    def get_all_results(self, run_id: str) -> dict[str, Any]:
        raw_fields = self._redis.hgetall(self._key(run_id))
        return {field: json.loads(value) for field, value in raw_fields.items()}

    def delete_run(self, run_id: str) -> None:
        self._redis.delete(self._key(run_id))


_memory_store = InMemoryPipelineStateStore()


@lru_cache(maxsize=1)
def get_pipeline_state_store() -> PipelineStateStoreProtocol:
    """Process-wide singleton, mirroring token_store.get_token_store()'s and
    history_store.get_history_store()'s pattern. Import of backend.config
    and redis is deferred into this function so importing pipeline_state
    never requires either — the in-memory store stays usable standalone
    with zero dependencies.
    """
    from backend.config import get_settings

    settings = get_settings()
    if not settings.redis_url:
        return _memory_store

    import redis

    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return RedisPipelineStateStore(client)
