"""Semantic agent memory via Mem0 (Phase 4).

One Mem0 client serves two logically-separate memory pools, distinguished
only by the user_id argument passed on every call — not two accounts, not a
second store:

- "prefs:<session_id>" — a session's own recalled preferences/history
  (personal recall).
- "lessons:global" — a single fixed id, not per-session, holding lessons
  the critic loop (backend/critic_agent.py) extracts from completed tasks.
  Fixed rather than per-session because a lesson should generalize across
  every user of this deployment, not stay siloed to whoever triggered it.

remember()/recall() never raise: this is a best-effort side channel, and a
Mem0 outage must never fail a task. NullMemoryManager (used whenever
MEM0_API_KEY is unset) makes that the default behavior with zero calls out
at all, following the same optional-blank-disables-it pattern as every
other external dependency in backend/config.py.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class MemoryManagerProtocol(Protocol):
    def remember(self, user_id: str, content: str, *, metadata: dict[str, Any] | None = None) -> None: ...
    def recall(self, user_id: str, query: str, *, limit: int = 3) -> list[str]: ...


class NullMemoryManager:
    """No-op used when MEM0_API_KEY is unset — recall always returns
    nothing, remember is silently discarded."""

    def remember(self, user_id: str, content: str, *, metadata: dict[str, Any] | None = None) -> None:
        return None

    def recall(self, user_id: str, query: str, *, limit: int = 3) -> list[str]:
        return []


class Mem0MemoryManager:
    def __init__(self, client: Any) -> None:
        self._client = client

    def remember(self, user_id: str, content: str, *, metadata: dict[str, Any] | None = None) -> None:
        try:
            self._client.add(content, user_id=user_id, metadata=metadata)
        except Exception:
            logger.warning("Mem0 remember() failed; continuing without it.", exc_info=True)

    def recall(self, user_id: str, query: str, *, limit: int = 3) -> list[str]:
        try:
            # v2 search takes user_id inside filters, not as a top-level
            # kwarg, and returns {"results": [...]}, not a bare list —
            # confirmed directly against the real API (v1's shape differs
            # and is deprecated).
            response = self._client.search(query, filters={"user_id": user_id}, limit=limit, version="v2")
            results = response.get("results", [])
        except Exception:
            logger.warning("Mem0 recall() failed; continuing without it.", exc_info=True)
            return []
        return [entry["memory"] for entry in results if entry.get("memory")]


@lru_cache(maxsize=1)
def get_memory_manager() -> MemoryManagerProtocol:
    """Process-wide singleton, mirroring token_store.get_token_store()'s
    pattern. Import of backend.config and mem0 is deferred into this
    function so importing memory_manager never requires either.
    """
    from backend.config import get_settings

    settings = get_settings()
    if not settings.mem0_api_key:
        return NullMemoryManager()

    from mem0 import MemoryClient

    try:
        # MemoryClient's constructor validates the key synchronously (a real
        # network call) and raises if it's invalid or unreachable — unlike
        # remember()/recall() below, that happens before there's a
        # Mem0MemoryManager instance to catch it, so it's handled here
        # instead. An invalid/unreachable key must degrade to no-op memory,
        # exactly like an unset key, not crash every task that touches it.
        client = MemoryClient(api_key=settings.mem0_api_key)
    except Exception:
        logger.warning("Mem0 client could not be initialized (invalid key or unreachable); memory/lessons disabled for this process.", exc_info=True)
        return NullMemoryManager()

    return Mem0MemoryManager(client)
