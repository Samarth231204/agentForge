"""Pipeline executor (Phase 11): runs a validated PipelinePlan (Phase 10)
against the real blueprints (Phase 8) and real tools (Phase 7's registry,
constructed with real per-session credentials here), threading each step's
output to its dependents through the Phase 9 Redis pipeline-state store,
and reporting progress through the existing EventEmitter exactly like
every other workflow already does.

Steps run in dependency "waves": every step whose dependencies are already
satisfied runs concurrently with its wave-mates (a real ThreadPoolExecutor,
not just sequential steps relabeled), then the next wave starts once the
current one finishes. A step's own failure is recorded as that step's
result text and does not abort the rest of the pipeline — dependents see
the failure honestly as their prior-step context, matching this project's
established "degrade, don't crash" posture elsewhere (Redis/Mem0 outages,
provider rate limits, etc.).
"""

from __future__ import annotations

import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from backend.agent_loop import ToolSpec
from backend.auth.token_store import get_token_store
from backend.blueprints import Stage, run_parallel_fanout, run_sequential_stages, run_single_agent_loop
from backend.event_emitter import EventEmitter
from backend.llm_fallback import build_llm_candidates, complete_with_fallback
from backend.pipeline_planner import PipelinePlan, PipelineStep
from backend.pipeline_state import get_pipeline_state_store
from backend.tool_registry import get_tool_by_name
from backend.tools.browser_tool import BrowserTool
from backend.tools.email_tool import GmailUnavailable, send_email
from backend.tools.github_tool import GithubTool, SandboxUnavailable
from backend.tools.web_search_tool import WebSearchTool

if TYPE_CHECKING:
    from backend.config import Settings

logger = logging.getLogger(__name__)

_SINGLE_AGENT_SYSTEM_PROMPT = (
    "You are one step of a larger multi-step plan. Complete the specific instructions given to you, "
    "using the tools available and any prior-step output provided, then call final_answer with your "
    "result. Be concrete and specific — later steps depend on your output being usable as-is, not "
    "vague. Never enter payment details, passwords, or other account credentials into any tool."
)


@dataclass(frozen=True)
class ExecutionContext:
    """Per-session credentials needed to construct github/email tools —
    kept separate from the tool registry (Phase 7) on purpose, since a
    static registry has no business holding live session state."""

    repo_url: str = ""
    github_token: str = ""
    session_id: str = ""
    gmail_session_id: str = ""


def _build_tool_specs(tool_names: list[str], ctx: ExecutionContext, settings: "Settings") -> tuple[list[ToolSpec], list[Callable[[], None]]]:
    """Constructs real ToolSpecs for a step's requested tools, plus any
    cleanup callables that must run afterward (browser.close(), currently
    the only one). A tool that can't be constructed for this session
    (github without a connected repo, email without connected Gmail) is
    silently skipped — the step still runs with whatever tools it did get,
    same "degrade, don't crash" posture as everywhere else in this
    project, rather than failing the whole step over one missing tool."""
    specs: list[ToolSpec] = []
    cleanups: list[Callable[[], None]] = []
    for name in tool_names:
        metadata = get_tool_by_name(name)
        if metadata is None:
            continue
        if name == "browser":
            browser = BrowserTool()
            specs.append(ToolSpec(name=metadata.name, description=metadata.description, parameters=metadata.schema, execute=browser.run))
            cleanups.append(browser.close)
        elif name == "web_search":
            specs.append(ToolSpec(name=metadata.name, description=metadata.description, parameters=metadata.schema, execute=lambda query="": WebSearchTool.as_text(WebSearchTool.search(query))))
        elif name == "email":
            if not ctx.gmail_session_id:
                continue

            def _send_email(to: str = "", subject: str = "", body: str = "", _gmail_session_id: str = ctx.gmail_session_id) -> str:
                try:
                    return send_email(session_id=_gmail_session_id, to=to, subject=subject, body=body, token_store=get_token_store())
                except GmailUnavailable as exc:
                    return f"Could not send email: {exc}"

            specs.append(ToolSpec(name=metadata.name, description=metadata.description, parameters=metadata.schema, execute=_send_email))
        elif name == "github":
            if not (ctx.repo_url and ctx.github_token and ctx.session_id):
                continue
            try:
                github = GithubTool(repo_url=ctx.repo_url, token=ctx.github_token, session_id=ctx.session_id, image=settings.sandbox_image)
            except (ValueError, SandboxUnavailable):
                continue

            def _run_github(action: str = "", file_path: str = "", content: str = "", command: str = "", branch_name: str = "", title: str = "", body: str = "", _tool: GithubTool = github) -> str:
                return _tool._run(action=action, file_path=file_path, content=content, command=command, branch_name=branch_name, title=title, body=body)

            specs.append(ToolSpec(name=metadata.name, description=metadata.description, parameters=metadata.schema, execute=_run_github))
    return specs, cleanups


def _make_tool_factory(tool_names: list[str], ctx: ExecutionContext, settings: "Settings") -> tuple[Callable[[], list[ToolSpec]], list[Callable[[], None]]]:
    """A zero-arg factory suitable for blueprints.run_parallel_fanout —
    called once per concurrent branch, each time constructing genuinely
    fresh tool instances (never a shared one across branches). Every
    branch's cleanups accumulate into one shared list the caller closes
    once every branch has finished."""
    cleanups: list[Callable[[], None]] = []

    def factory() -> list[ToolSpec]:
        specs, branch_cleanups = _build_tool_specs(tool_names, ctx, settings)
        cleanups.extend(branch_cleanups)
        return specs

    return factory, cleanups


def _format_prior_results(depends_on: list[str], run_id: str, state_store) -> str:
    if not depends_on:
        return ""
    parts = []
    for name in depends_on:
        value = state_store.get_step_result(run_id, name)
        if value is None:
            continue
        text = value if isinstance(value, str) else json.dumps(value)
        parts.append(f"Output of prior step '{name}':\n{text}")
    return "\n\n".join(parts)


def _extract_fanout_items(prior_text: str, count: int, settings: "Settings") -> list[str]:
    """One lightweight LLM call splitting a prior step's free-text output
    into up to `count` distinct, independently-processable items (e.g. "3
    companies with their URLs" -> 3 short item strings) — parallel_fanout
    needs concrete items to branch over, and a plan only ever specifies
    *how many*, not the items themselves (those only exist once a
    dependency has actually run). Falls back to a single generic item
    (never raises) if extraction itself fails."""
    if not prior_text.strip():
        return ["(no prior step output available)"] * max(1, count)
    try:
        candidates = build_llm_candidates(settings)
        response = complete_with_fallback(
            candidates,
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Extract up to {count} distinct items from the given text, each one to be processed "
                        "independently and separately in parallel. Return JSON only: {\"items\": [\"...\"]}. Each "
                        "item should be short and self-contained — e.g. a company name plus its URL if present."
                    ),
                },
                {"role": "user", "content": prior_text},
            ],
        )
        data = json.loads(response.choices[0].message.content or "{}")
        items = data.get("items")
        if isinstance(items, list) and items:
            return [str(item) for item in items][:count]
    except Exception:
        logger.warning("Fan-out item extraction failed; falling back to a single generic item.", exc_info=True)
    return [prior_text[:300]]


def _default_sequential_stages() -> list[Stage]:
    """A generic Plan -> Draft -> Review chain — the same shape
    templates/email_crew.py's Planner->Writer->Reviewer already uses,
    applied generically since a PipelineStep only carries one instructions
    field, not per-stage detail."""
    return [
        Stage(name="Plan", system_prompt="You produce a brief, concrete plan for the task described.", build_user_prompt=lambda prompt, prior: prompt),
        Stage(name="Draft", system_prompt="You produce a complete draft based on the plan.", build_user_prompt=lambda prompt, prior: f"Task: {prompt}\n\nPlan: {prior[0]}"),
        Stage(name="Review", system_prompt="You review and finalize the draft, fixing any issues, and return only the final result.", build_user_prompt=lambda prompt, prior: f"Task: {prompt}\n\nDraft: {prior[1]}"),
    ]


def _run_step(step: PipelineStep, settings: "Settings", ctx: ExecutionContext, run_id: str, state_store, emitter: EventEmitter | None) -> None:
    if emitter:
        emitter.emit("agent_started", {"agent": step.name, "role": step.instructions[:200]})
    prior_context = _format_prior_results(step.depends_on, run_id, state_store)
    task_text = f"{step.instructions}\n\n{prior_context}".strip() if prior_context else step.instructions

    try:
        if step.blueprint == "single_agent_loop":
            tools, cleanups = _build_tool_specs(step.tools, ctx, settings)
            try:
                result: object = run_single_agent_loop(_SINGLE_AGENT_SYSTEM_PROMPT, task_text, tools, settings, label=step.name)
            finally:
                for cleanup in cleanups:
                    cleanup()
        elif step.blueprint == "sequential_stages":
            result = run_sequential_stages(_default_sequential_stages(), task_text, settings)
        elif step.blueprint == "parallel_fanout":
            items = _extract_fanout_items(prior_context, step.fanout_count, settings)
            factory, cleanups = _make_tool_factory(step.tools, ctx, settings)
            try:
                result = run_parallel_fanout(
                    system_prompt=_SINGLE_AGENT_SYSTEM_PROMPT,
                    items=items,
                    build_user_prompt=lambda item: f"{step.instructions}\n\nFocus specifically on: {item}",
                    tool_factory=factory,
                    settings=settings,
                    max_concurrency=step.fanout_count,
                    label=step.name,
                )
            finally:
                for cleanup in cleanups:
                    cleanup()
        else:
            result = f"Unknown blueprint: {step.blueprint}"
    except Exception as exc:
        logger.warning("Pipeline step '%s' failed: %s", step.name, exc, exc_info=True)
        result = f"This step failed: {exc}"

    state_store.set_step_result(run_id, step.name, result)
    if emitter:
        summary = result if isinstance(result, str) else f"Produced {len(result)} result(s)."
        emitter.emit("agent_completed", {"agent": step.name, "summary": str(summary)[:300]})


def _run_waves(plan: PipelinePlan, settings: "Settings", ctx: ExecutionContext, run_id: str, state_store, emitter: EventEmitter | None) -> None:
    remaining = {step.name: step for step in plan.steps}
    completed: set[str] = set()

    while remaining:
        ready = [step for step in remaining.values() if set(step.depends_on).issubset(completed)]
        if not ready:
            # Every remaining step has an unmet dependency — shouldn't
            # happen given PipelinePlan's own depends_on-must-reference-a-
            # real-step validator, but guard rather than loop forever.
            logger.warning("Pipeline run %s stalled with unresolvable dependencies among: %s", run_id, list(remaining))
            break
        with ThreadPoolExecutor(max_workers=len(ready)) as executor:
            list(executor.map(lambda step: _run_step(step, settings, ctx, run_id, state_store, emitter), ready))
        for step in ready:
            completed.add(step.name)
            del remaining[step.name]


def _summarize_plan_results(plan: PipelinePlan, run_id: str, state_store) -> str:
    lines = [plan.summary, ""]
    for step in plan.steps:
        result = state_store.get_step_result(run_id, step.name)
        lines.append(f"**{step.name}**")
        if isinstance(result, list):
            lines.extend(f"- {item}" for item in result)
        else:
            lines.append(str(result))
        lines.append("")
    return "\n".join(lines).strip()


def execute_pipeline(plan: PipelinePlan, settings: "Settings", ctx: ExecutionContext, emitter: EventEmitter | None = None) -> str:
    """Runs every step of a validated plan to completion (dependency-order
    waves, independent steps concurrent within a wave) and returns a
    human-readable report of what every step produced. The run's Redis
    pipeline state is always cleaned up afterward, success or failure —
    it's scratch space for this one run, not a durable record."""
    run_id = str(uuid.uuid4())
    state_store = get_pipeline_state_store()
    try:
        _run_waves(plan, settings, ctx, run_id, state_store, emitter)
        return _summarize_plan_results(plan, run_id, state_store)
    finally:
        state_store.delete_run(run_id)
