"""FastAPI entry point for AgentForge Phase 1."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from queue import Empty
from threading import Thread
from typing import Callable, TypeVar

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from backend.auth.google_oauth import exchange_code_for_tokens, get_auth_url
from backend.auth.token_store import get_token_store
from backend.config import get_settings
from backend.crew_engine import CrewEngine
from backend.critic_agent import run_critic
from backend.event_emitter import EventEmitter
from backend.history_store import get_history_store
from backend.intent_parser import IntentParser
from backend.memory_manager import get_memory_manager
from backend.pipeline_executor import ExecutionContext, execute_pipeline
from backend.pipeline_planner import PipelinePlan, plan_pipeline

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
    # Client-generated identifier for this browser's connected Gmail account
    # (see /auth/google below) — unrelated to session_id above, which is
    # GitHub-specific. Sending an email is only possible once this session id
    # has a token stored for it; otherwise email requests stay draft-only.
    gmail_session_id: str = Field(default="", max_length=100)
    # Client-generated identifier for this browser session's task history
    # (Phase 4). Unrelated to session_id/gmail_session_id above — used only
    # to look up/append entries in history_store, never for GitHub or Gmail.
    history_session_id: str = Field(default="", max_length=100)
    # A previously-proposed PipelinePlan (from POST /pipeline/plan), as the
    # user reviewed and approved it — Phase 12's review gate. When set,
    # this plan runs directly, skipping intent classification and planning
    # entirely. When unset and the request classifies as compound, a plan
    # is proposed but NOT executed (see _run_task) — nothing about a
    # multi-step, potentially side-effecting pipeline runs without this
    # having been explicitly approved first.
    approved_plan: dict | None = None

    def normalized_prompt(self) -> str:
        return self.prompt.strip()


def _validate_prompt(request: TaskRequest) -> str:
    prompt = request.normalized_prompt()
    if not prompt:
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


# Intents whose result text is safe to remember as a personal preference —
# narrowed deliberately (Phase 4): send_email/github/booking have real side
# effects (a sent email, a pushed commit, a browser action) where a stale
# injected memory silently changing behavior is a real correctness risk.
# Recall/remember for those is left as explicit future work, not this phase.
_MEMORY_ELIGIBLE_INTENTS = {"research", "draft_email"}


def _record_task_side_effects(prompt: str, result: dict, history_session_id: str, gmail_session_id: str, session_id: str, task_id: str) -> None:
    """Best-effort: task history, agent memory, and the critic's lesson
    extraction, run in a background thread after the user-facing response is
    already complete. None of this may ever affect the task it's reviewing —
    every step is independently wrapped so one failing (e.g. Mem0 or Redis
    down) never blocks the others."""
    intent = result.get("intent", "")
    content = result.get("content", "")

    if history_session_id:
        try:
            entry = {
                "task_id": task_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "intent": intent,
                "prompt": prompt[:500],
                "content": content[:2000],
                "sources": result.get("sources", []),
            }
            get_history_store().append(history_session_id, entry)
        except Exception:
            logger.warning("Recording task history failed: task_id=%s", task_id, exc_info=True)

    user_id = gmail_session_id or session_id or "anonymous"
    if intent in _MEMORY_ELIGIBLE_INTENTS:
        try:
            get_memory_manager().remember(f"prefs:{user_id}", f"User asked: {prompt[:500]}\nAgentForge answered: {content[:500]}")
        except Exception:
            logger.warning("Writing agent memory failed: task_id=%s", task_id, exc_info=True)

    try:
        settings = get_settings()
        lesson = run_critic(prompt, intent, content, settings)
        if lesson:
            get_memory_manager().remember("lessons:global", lesson)
    except Exception:
        logger.warning("Critic review failed: task_id=%s", task_id, exc_info=True)


def _run_task(prompt: str, context: str, repo_url: str, github_token: str, session_id: str, gmail_session_id: str, history_session_id: str, emitter: EventEmitter, approved_plan: dict | None = None) -> None:
    """Execute one task and guarantee its stream has a terminal event."""
    try:
        if approved_plan is not None:
            # Phase 12's review gate: the user already saw this exact plan
            # (via POST /pipeline/plan) and explicitly approved it — run it
            # directly, skipping intent classification and planning.
            settings = get_settings()
            try:
                plan = PipelinePlan.model_validate(approved_plan)
            except ValidationError:
                emitter.emit("error", {"code": "invalid_plan", "message": "The approved plan was invalid or out of date. Please generate a new plan and try again.", "retryable": False})
                emitter.emit("task_completed", {"status": "failed"})
                return
            emitter.emit("intent_detected", {"intent": "pipeline", "confidence": 1.0, "reason": "Running a previously reviewed and approved multi-step plan.", "compound": True, "all_intents": [step.intent for step in plan.steps]})
            emitter.emit("workflow_started", {"workflow": "dynamic_pipeline", "agents": [step.name for step in plan.steps]})
            ctx = ExecutionContext(repo_url=repo_url, github_token=github_token, session_id=session_id, gmail_session_id=gmail_session_id)
            content = execute_pipeline(plan, settings, ctx, emitter)
            result = {"intent": "pipeline", "content": content, "sources": []}
            emitter.emit("result", result)
            emitter.emit("task_completed", {"status": "completed"})
            Thread(
                target=_record_task_side_effects,
                args=(prompt, result, history_session_id, gmail_session_id, session_id, emitter.task_id),
                name=f"agentforge-side-effects-{emitter.task_id}",
                daemon=True,
            ).start()
            return

        has_repo_context = bool(repo_url and github_token)
        has_gmail_context = _is_gmail_connected(gmail_session_id)
        intents = _call_with_retry(lambda: IntentParser().parse_intents(prompt, context, has_repo_context, has_gmail_context), emitter, "intent classification")
        primary = intents[0]
        intent_event: dict = {"intent": primary.intent, "confidence": primary.confidence, "reason": primary.reason}
        if len(intents) > 1:
            intent_event["compound"] = True
            intent_event["all_intents"] = [i.intent for i in intents]
        emitter.emit("intent_detected", intent_event)
        if len(intents) > 1:
            # Propose a plan, but never execute it here — a multi-step
            # pipeline can send real emails, push real commits, or take
            # real browser actions, so nothing runs until the user has
            # reviewed this exact plan and approved it via POST /tasks
            # with approved_plan set (see above), which the frontend's
            # plan-review screen (Phase 12) drives.
            settings = get_settings()
            plan = _call_with_retry(lambda: plan_pipeline(prompt, intents, settings), emitter, "pipeline planning")
            if plan is not None:
                emitter.emit("workflow_started", {"workflow": "dynamic_pipeline_proposed", "agents": [step.name for step in plan.steps]})
                steps = ", ".join(i.intent for i in intents)
                result = {
                    "intent": "pipeline_proposed",
                    "content": (
                        f"This request needs multiple capabilities working together ({steps}). "
                        f"Here's a proposed plan: {plan.summary} Review it and approve it to run, or ask for changes first."
                    ),
                    "sources": [],
                    "plan": plan.model_dump(),
                }
            else:
                # The planner itself failed to produce a valid plan across
                # every candidate model — fall back to plainly reporting
                # what was detected rather than a confusing crash.
                steps = ", ".join(i.intent for i in intents)
                result = {
                    "intent": "unsupported",
                    "content": (
                        f"This request needs multiple different capabilities working together ({steps}), "
                        "but AgentForge could not build a reliable plan for it right now. Please try "
                        "rephrasing the request, or ask for one part of it at a time."
                    ),
                    "sources": [],
                }
        else:
            result = _call_with_retry(lambda: CrewEngine().run(primary, prompt, context, emitter, repo_url=repo_url, github_token=github_token, session_id=session_id, gmail_session_id=gmail_session_id), emitter, "workflow")
        emitter.emit("result", result)
        emitter.emit("task_completed", {"status": "completed"})
        Thread(
            target=_record_task_side_effects,
            args=(prompt, result, history_session_id, gmail_session_id, session_id, emitter.task_id),
            name=f"agentforge-side-effects-{emitter.task_id}",
            daemon=True,
        ).start()
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


@app.get("/auth/google")
def google_auth_start(state: str) -> RedirectResponse:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=503, detail="Gmail integration is not configured on this server.")
    return RedirectResponse(get_auth_url(state, settings))


def _is_gmail_connected(gmail_session_id: str) -> bool:
    """A token-store outage must read as "not connected" (a safe default —
    never falsely claim a connection exists), not crash the caller. Shared
    by the status route and _run_task's intent-classification hint below."""
    if not gmail_session_id:
        return False
    try:
        return get_token_store().has(gmail_session_id)
    except Exception:
        logger.warning("Checking Gmail connection status failed: gmail_session_id=%s", gmail_session_id, exc_info=True)
        return False


@app.get("/auth/google/status")
def google_auth_status(state: str) -> dict[str, bool]:
    return {"connected": _is_gmail_connected(state)}


@app.get("/auth/google/callback")
def google_auth_callback(code: str, state: str) -> HTMLResponse:
    settings = get_settings()
    try:
        credentials = exchange_code_for_tokens(code, settings)
    except Exception:
        logger.exception("Gmail OAuth callback failed: state=%s", state)
        return HTMLResponse("<h3>Could not connect Gmail. Please close this tab and try again.</h3>", status_code=400)
    try:
        get_token_store().save(state, credentials)
    except Exception:
        logger.exception("Saving the Gmail token failed: state=%s", state)
        return HTMLResponse("<h3>Google approved the connection, but AgentForge could not save it. Please try again.</h3>", status_code=503)
    return HTMLResponse("<h3>Gmail connected. You can close this tab and return to AgentForge.</h3>")


@app.get("/history")
def get_history(state: str) -> dict[str, list[dict]]:
    # History is a best-effort side channel, same posture as the background
    # write in _record_task_side_effects — a Redis outage on the read side
    # must degrade to "no history available" too, not a 500.
    try:
        return {"tasks": get_history_store().list(state)}
    except Exception:
        logger.warning("Fetching task history failed: state=%s", state, exc_info=True)
        return {"tasks": []}


class PipelinePlanRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    context: str = Field(default="", max_length=4000)
    repo_url: str = Field(default="", max_length=500)
    github_token: str = Field(default="", max_length=500)
    gmail_session_id: str = Field(default="", max_length=100)
    # When the user asks for a change to a plan they already saw, the
    # frontend sends both of these back so the planner can produce a
    # revised plan with that feedback in view, rather than starting over
    # with no memory of what was already proposed.
    revision_instruction: str = Field(default="", max_length=1000)
    prior_plan: dict | None = None


@app.post("/pipeline/plan")
def propose_pipeline_plan(request: PipelinePlanRequest) -> dict:
    """Phase 12: proposes a plan for review, without executing anything.
    Returns {"compound": false, "intent": <single intent>} for an ordinary
    request — the frontend should just run it normally via POST /tasks in
    that case, no plan-review screen needed. Returns
    {"compound": true, "plan": {...} | None} for a multi-step request —
    None means the planner itself failed and there is nothing to review."""
    prompt = request.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="prompt cannot be blank")
    has_repo_context = bool(request.repo_url and request.github_token)
    has_gmail_context = _is_gmail_connected(request.gmail_session_id)
    intents = IntentParser().parse_intents(prompt, request.context.strip(), has_repo_context, has_gmail_context)
    if len(intents) <= 1:
        return {"compound": False, "intent": intents[0].intent, "plan": None}

    settings = get_settings()
    planning_prompt = prompt
    if request.revision_instruction.strip():
        prior_summary = (request.prior_plan or {}).get("summary", "")
        planning_prompt = (
            f"{prompt}\n\nThe user already reviewed a proposed plan (summary: {prior_summary}) and asked for "
            f"this change before approving it: {request.revision_instruction.strip()}"
        )
    plan = plan_pipeline(planning_prompt, intents, settings)
    return {"compound": True, "intent": None, "plan": plan.model_dump() if plan is not None else None}


@app.post("/tasks/stream")
async def stream_task(request: TaskRequest) -> StreamingResponse:
    prompt = _validate_prompt(request)
    emitter = EventEmitter(str(uuid.uuid4()))
    # Queue confirmation synchronously so every accepted SSE connection has an
    # immediate, visible event before any provider or worker-thread work begins.
    emitter.emit("task_started", {"prompt": prompt[:500]})

    def run_task() -> None:
        _run_task(prompt, request.context.strip(), request.repo_url.strip(), request.github_token.strip(), request.session_id.strip(), request.gmail_session_id.strip(), request.history_session_id.strip(), emitter, request.approved_plan)

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
    _run_task(prompt, request.context.strip(), request.repo_url.strip(), request.github_token.strip(), request.session_id.strip(), request.gmail_session_id.strip(), request.history_session_id.strip(), emitter, request.approved_plan)
    events: list[dict] = []
    while True:
        event = emitter.queue.get()
        if event is None:
            break
        events.append(event.model_dump(mode="json"))
    return {"events": events}
