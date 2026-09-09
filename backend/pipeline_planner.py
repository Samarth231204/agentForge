"""Pipeline planner (Phase 10): given a compound request's detected intents
(Phase 6), the tool registry (Phase 7), and the blueprint taxonomy (Phase
8), one LLM call proposes a concrete, structured pipeline plan — which
steps, which blueprint pattern per step, which tools, and which steps
depend on which. This is composition, not execution: a PipelinePlan is
pure data (validated with Pydantic, exactly like intent_parser.Intent
already is), never generated code. Nothing in this module runs a plan —
that's the Phase 11 executor's job, working from what this module
produces.

Goes through the same build_llm_candidates()/complete_with_fallback()
resilience chain every other LLM-calling workflow in this project already
uses — no separate dedicated model for this, matching the roadmap's
explicit decision to reuse existing machinery rather than add a new one.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, ValidationError, field_validator

from backend.blueprints import DEFAULT_FANOUT_CAP, BlueprintName
from backend.intent_parser import Intent, IntentName
from backend.llm_fallback import build_llm_candidates, complete_with_fallback, is_rate_limited
from backend.tool_registry import get_tool_registry

if TYPE_CHECKING:
    from backend.config import Settings

logger = logging.getLogger(__name__)


class PipelineStep(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    blueprint: BlueprintName
    intent: IntentName
    tools: list[str] = Field(default_factory=list)
    instructions: str = Field(min_length=1, max_length=2000)
    # Names of other steps in the same plan that must complete before this
    # one starts. Empty means this step can start as soon as the run does.
    depends_on: list[str] = Field(default_factory=list)
    # Only meaningful when blueprint == "parallel_fanout". Enforced here via
    # a validator (not left to the executor to remember) so a plan can never
    # request more concurrent branches than the blueprint layer itself
    # allows, regardless of what a model proposes.
    fanout_count: int = 1

    @field_validator("fanout_count")
    @classmethod
    def _cap_fanout_count(cls, value: int) -> int:
        return min(max(value, 1), DEFAULT_FANOUT_CAP)

    @field_validator("tools")
    @classmethod
    def _drop_unknown_tools(cls, value: list[str]) -> list[str]:
        # A model could hallucinate a tool name that doesn't exist. Drop it
        # rather than let the executor choke on it later — same "fail soft
        # on a model mistake" posture as fanout capping above.
        known = {tool.name for tool in get_tool_registry()}
        return [name for name in value if name in known]


class PipelinePlan(BaseModel):
    steps: list[PipelineStep] = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=1000)

    @field_validator("steps")
    @classmethod
    def _depends_on_must_reference_real_steps(cls, steps: list[PipelineStep]) -> list[PipelineStep]:
        names = {step.name for step in steps}
        for step in steps:
            unknown = [dep for dep in step.depends_on if dep not in names]
            if unknown:
                raise ValueError(f"Step '{step.name}' depends on unknown step(s): {unknown}")
        return steps


def _build_system_prompt() -> str:
    tools_block = "\n".join(f"- {tool.name}: {tool.description}" for tool in get_tool_registry())
    return (
        "You design a concrete execution pipeline for a compound user request that needs more than "
        "one capability to satisfy. Return JSON only, with keys \"steps\" (an array) and \"summary\" "
        "(a one-sentence plain-language description of the overall plan for the user to review).\n\n"
        "Each step object has keys: name (a short unique identifier), blueprint, intent, tools "
        "(array of tool names this step needs), instructions (what this step should actually do, "
        "specific enough to act on), depends_on (array of step names that must finish first, empty "
        "if this step can start immediately), fanout_count (only meaningful for parallel_fanout — "
        "how many independent items to process concurrently, e.g. how many companies to visit; use 1 "
        f"for every other blueprint). fanout_count above {DEFAULT_FANOUT_CAP} will be capped automatically.\n\n"
        "Valid blueprint values:\n"
        "- single_agent_loop: one agent working through a task step by step with tools, for anything "
        "that isn't naturally parallel or a multi-stage write (e.g. one research task, one booking task).\n"
        "- sequential_stages: a chain of steps where each one builds on the last, for anything that "
        "benefits from a plan-then-write-then-review shape (e.g. drafting a message).\n"
        "- parallel_fanout: N independent branches of the same kind of work running at once, for "
        f"anything naturally repeated across multiple items (e.g. visiting several company sites) — "
        f"never more than {DEFAULT_FANOUT_CAP} at a time.\n\n"
        f"Available tools:\n{tools_block}\n\n"
        "Valid intent values (for each step's intent field): research, draft_email, send_email, "
        "github, booking, unsupported. Keep the plan as small as it can genuinely be — do not add "
        "steps beyond what the request actually needs."
    )


def plan_pipeline(prompt: str, intents: list[Intent], settings: "Settings") -> PipelinePlan | None:
    """Returns a validated PipelinePlan, or None if every candidate model
    failed to produce one — the caller (main.py, once Phase 11 wires this
    in) falls back to the existing "not available yet" message on None,
    exactly as it already does today for every compound request."""
    detected = ", ".join(f"{i.intent} ({i.task_summary})" for i in intents)
    user_text = f"Request: {prompt}\nDetected intents needed: {detected}"
    system_prompt = _build_system_prompt()
    candidates = build_llm_candidates(settings)

    for model, api_key in candidates:
        provider_failed = False
        for attempt in range(2):
            try:
                response = complete_with_fallback(
                    [(model, api_key)],
                    temperature=0.2,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text if attempt == 0 else user_text + "\nYour previous answer was invalid. Return valid JSON only."},
                    ],
                )
            except Exception as exc:
                if not is_rate_limited(exc):
                    raise
                provider_failed = True
                break
            try:
                content = response.choices[0].message.content or "{}"
                return PipelinePlan.model_validate(json.loads(content))
            except (json.JSONDecodeError, ValidationError, AttributeError, IndexError):
                continue
        if not provider_failed:
            break
    logger.warning("Pipeline planning failed to produce a valid plan across every candidate model.")
    return None
