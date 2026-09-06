"""FastAPI entry point for AgentForge Phase 1."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from queue import Empty
from threading import Thread
from typing import Callable, TypeVar

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.crew_engine import CrewEngine
from backend.event_emitter import EventEmitter
from backend.intent_parser import IntentParser

logger = logging.getLogger(__name__)


class TaskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    context: str = Field(default="", max_length=4000)
    repo_url: str = Field(default="", max_length=500)
    github_token: str = Field(default="", max_length=500)
    # Client-generated identifier for a GitHub work session. Repeated calls
    # with the same value continue committing to the same branch/PR; a fresh
    # value (or none) starts a new branch. Unrelated to the per-request task id.
    session_id: str = Field(default="", max_length=100)

    def normalized_prompt(self) -> str:
        return self.prompt.strip()


def _validate_prompt(request: TaskRequest) -> str:
    prompt = request.normalized_prompt()
    if not prompt:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="prompt cannot be blank")
    return prompt


T = TypeVar("T")


def _call_with_retry(fn: Callable[[], T], emitter: EventEmitter, step: str, max_retries: int = 2) -> T:
    """Run fn(), retrying it up to max_retries times if it fails with a
    transient provider error (rate limit / 429 / 503 / unavailable). A
    permanent failure (bad request, validation error, etc.) is never
    retried — it would just fail identically. A single retry proved
    insufficient in practice for booking's multi-stage pipeline, which can
    burn most of a free-tier per-minute token budget in one run and hit the
    same wall again immediately on the first retry."""
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == max_retries or not CrewEngine._is_rate_limited(exc):
                raise
            delay = CrewEngine._retry_delay(exc)
            logger.warning("AgentForge %s hit a transient provider error, retrying in %.1fs (attempt %d/%d): task_id=%s error=%s", step, delay, attempt + 1, max_retries, emitter.task_id, exc)
            time.sleep(delay)
    raise AssertionError("unreachable")  # loop always returns or raises above


def _run_task(prompt: str, context: str, repo_url: str, github_token: str, session_id: str, emitter: EventEmitter) -> None:
    """Execute one task and guarantee its stream has a terminal event."""
    try:
        has_repo_context = bool(repo_url and github_token)
        intent = _call_with_retry(lambda: IntentParser().parse(prompt, context, has_repo_context), emitter, "intent classification")
        emitter.emit("intent_detected", {"intent": intent.intent, "confidence": intent.confidence, "reason": intent.reason})
        result = _call_with_retry(lambda: CrewEngine().run(intent, prompt, context, emitter, repo_url=repo_url, github_token=github_token, session_id=session_id), emitter, "workflow")
        emitter.emit("result", result)
        emitter.emit("task_completed", {"status": "completed"})
    except Exception as exc:
        if CrewEngine._is_rate_limited(exc):
            logger.warning("AgentForge LLM rate limited: task_id=%s error=%s", emitter.task_id, exc)
            emitter.emit("error", {"code": "llm_rate_limited", "message": "The AI provider is temporarily rate-limited. Please retry shortly.", "retryable": True})
        else:
            logger.exception("AgentForge task failed: task_id=%s", emitter.task_id)
            emitter.emit("error", {"code": "task_failed", "message": "The task could not be completed. Please retry shortly.", "retryable": True})
        emitter.emit("task_completed", {"status": "failed"})
    finally:
        emitter.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings()  # Validate required configuration before accepting traffic.
    yield


app = FastAPI(title="AgentForge", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tasks/stream")
async def stream_task(request: TaskRequest) -> StreamingResponse:
    prompt = _validate_prompt(request)
    emitter = EventEmitter(str(uuid.uuid4()))
    # Queue confirmation synchronously so every accepted SSE connection has an
    # immediate, visible event before any provider or worker-thread work begins.
    emitter.emit("task_started", {"prompt": prompt[:500]})

    def run_task() -> None:
        _run_task(prompt, request.context.strip(), request.repo_url.strip(), request.github_token.strip(), request.session_id.strip(), emitter)

    Thread(target=run_task, name=f"agentforge-{emitter.task_id}", daemon=True).start()

    def events():
        while True:
            try:
                event = emitter.queue.get(timeout=15)
            except Empty:
                yield ": keep-alive\n\n"
                continue
            if event is None:
                break
            yield emitter.serialize(event)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/tasks")
def run_task(request: TaskRequest) -> dict[str, list[dict]]:
    """JSON transport for UI clients that cannot safely hold an SSE request open."""
    prompt = _validate_prompt(request)
    emitter = EventEmitter(str(uuid.uuid4()))
    emitter.emit("task_started", {"prompt": prompt[:500]})
    _run_task(prompt, request.context.strip(), request.repo_url.strip(), request.github_token.strip(), request.session_id.strip(), emitter)
    events: list[dict] = []
    while True:
        event = emitter.queue.get()
        if event is None:
            break
        events.append(event.model_dump(mode="json"))
    return {"events": events}
