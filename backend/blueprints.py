"""Blueprint taxonomy (Phase 8): a small, fixed set of pipeline patterns
for the Phase 10 planner to choose from when composing a compound-request
pipeline. Each blueprint is a thin, config-driven wrapper around machinery
that already exists and is already tested — agent_loop.run_agent() and
llm_fallback's build_llm_candidates()/complete_with_fallback() — not a new
execution engine. Nothing here decides *when* to use which pattern; that's
the planner's (Phase 10) job. This module only knows how to run one, given
a config.

- single_agent_loop: one agent_loop.run_agent() call, unchanged.
- sequential_stages: chained plain completions, each stage seeing every
  prior stage's output — the same shape templates/email_crew.py's
  Planner->Writer->Reviewer has always used, generalized into a reusable
  primitive instead of being hardcoded per-workflow.
- parallel_fanout: N concurrent run_agent() calls, each given its own
  freshly-constructed tools (never shared across branches — see
  tool_factory below), capped at DEFAULT_FANOUT_CAP regardless of how many
  items are passed in, per the roadmap's server-side-enforced default.

State threading between different pipeline *steps* (e.g. feeding a
parallel_fanout's results into a later sequential_stages step) is
deliberately NOT this module's concern — that's the Phase 11 executor's
job, via the Phase 9 Redis pipeline-state store. Blueprints only know how
to run themselves once, given their own inputs.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from backend.agent_loop import ToolSpec, run_agent
from backend.llm_fallback import build_llm_candidates, complete_with_fallback

if TYPE_CHECKING:
    from backend.config import Settings

BlueprintName = Literal["single_agent_loop", "sequential_stages", "parallel_fanout"]

# Independent branches in a parallel_fanout are capped here regardless of
# how many items a caller (eventually the Phase 10 planner) requests —
# server-side enforcement was a deliberate design decision from the
# roadmap, not left to whatever an LLM proposes.
DEFAULT_FANOUT_CAP = 5


def run_single_agent_loop(
    system_prompt: str,
    user_prompt: str,
    tools: list[ToolSpec],
    settings: "Settings",
    *,
    temperature: float = 0.2,
    max_iterations: int = 25,
    label: str = "agent",
) -> str:
    """One tool-calling agent loop, exactly as booking already uses today —
    this blueprint is what booking_crew.py's run_booking_workflow() would
    reduce to if rewritten against this taxonomy."""
    return run_agent(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        tools=tools,
        candidates=build_llm_candidates(settings),
        temperature=temperature,
        max_iterations=max_iterations,
        label=label,
    )


@dataclass(frozen=True)
class Stage:
    """One step of a sequential_stages blueprint. build_user_prompt
    receives the pipeline's original prompt and every prior stage's output
    (in order, empty on the first stage) and returns this stage's user
    message — mirrors how email_crew.py's Writer stage sees the Planner's
    brief, and the Reviewer stage sees both."""

    name: str
    system_prompt: str
    build_user_prompt: Callable[[str, list[str]], str]


def run_sequential_stages(stages: list[Stage], prompt: str, settings: "Settings", *, temperature: float = 0.3) -> str:
    """Chained plain completions (no tool-calling) — the generalized shape
    of templates/email_crew.py's Planner->Writer->Reviewer. Returns the
    final stage's output; every prior stage's output is still visible to
    every later stage via build_user_prompt, exactly as the email crew's
    Reviewer sees both the brief and the draft."""
    candidates = build_llm_candidates(settings)
    outputs: list[str] = []
    for stage in stages:
        user_text = stage.build_user_prompt(prompt, outputs)
        response = complete_with_fallback(
            candidates,
            temperature=temperature,
            messages=[{"role": "system", "content": stage.system_prompt}, {"role": "user", "content": user_text}],
        )
        outputs.append(response.choices[0].message.content or "")
    return outputs[-1]


def run_parallel_fanout(
    system_prompt: str,
    items: list[str],
    build_user_prompt: Callable[[str], str],
    tool_factory: Callable[[], list[ToolSpec]],
    settings: "Settings",
    *,
    max_concurrency: int = DEFAULT_FANOUT_CAP,
    temperature: float = 0.2,
    max_iterations: int = 25,
    label: str = "agent",
) -> list[str]:
    """N independent agent_loop.run_agent() calls running concurrently, one
    per item, each with its own tool instances from tool_factory() — never
    a shared instance across branches. Safe because BrowserTool (and every
    ToolSpec-compatible tool) holds zero shared/module-level state; every
    field lives on the instance itself (confirmed directly against
    browser_tool.py before this blueprint was written).

    items beyond DEFAULT_FANOUT_CAP are silently dropped — the cap is
    enforced here, not left to whatever a caller (eventually the planner)
    requests. Returns each item's run_agent() result, in the same order as
    items; threading these into a later pipeline step is the executor's
    job (Phase 11), not this function's."""
    capped_items = items[:max_concurrency]
    candidates = build_llm_candidates(settings)

    def run_one(item: str) -> str:
        tools = tool_factory()
        user_prompt = build_user_prompt(item)
        return run_agent(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=tools,
            candidates=candidates,
            temperature=temperature,
            max_iterations=max_iterations,
            label=f"{label}[{item[:30]}]",
        )

    if not capped_items:
        return []
    with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        return list(executor.map(run_one, capped_items))
