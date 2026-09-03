"""Queue-based, SSE-safe progress events for a single AgentForge task."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from queue import Queue
from threading import Lock
from typing import Any

from pydantic import BaseModel, Field


class AgentEvent(BaseModel):
    task_id: str
    type: str
    timestamp: datetime
    sequence: int = Field(ge=1)
    data: dict[str, Any]


class EventEmitter:
    """Thread-safe producer used by a worker while FastAPI drains its queue."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.queue: Queue[AgentEvent | None] = Queue()
        self._sequence = 0
        self._lock = Lock()
        self._closed = False

    def emit(self, event_type: str, data: dict[str, Any] | None = None) -> AgentEvent:
        with self._lock:
            if self._closed:
                raise RuntimeError("Cannot emit an event after the stream is closed.")
            self._sequence += 1
            event = AgentEvent(
                task_id=self.task_id,
                type=event_type,
                timestamp=datetime.now(UTC),
                sequence=self._sequence,
                data=data or {},
            )
            self.queue.put(event)
            return event

    @staticmethod
    def serialize(event: AgentEvent) -> str:
        """Serialize exactly one SSE message; Pydantic handles ISO timestamps."""
        return f"data: {json.dumps(event.model_dump(mode='json'), ensure_ascii=False)}\\n\\n"

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self.queue.put(None)
