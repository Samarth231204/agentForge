"""Self-improvement critic (Phase 4): one cheap LLM call reviewing a just-
completed task, extracting a single reusable lesson for future similar
tasks — not a multi-agent crew (CrewAI stays fully removed from this
project, per Phase 3's decision) and not a new resilience mechanism of its
own; it goes through the same build_llm_candidates()/complete_with_fallback()
machinery every other workflow already uses.

Called as a best-effort background step after a task's own result has
already been returned to the user (see main.py) — a critic failure must
never affect the task it's reviewing, so this never raises.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from backend.llm_fallback import build_llm_candidates, complete_with_fallback

if TYPE_CHECKING:
    from backend.config import Settings

logger = logging.getLogger(__name__)

_NO_LESSON = "NONE"

SYSTEM_PROMPT = (
    "You review one completed AI-agent task and extract a single, short, reusable lesson for "
    "future similar tasks — a concrete pitfall avoided, a preference observed, or a technique "
    f"that worked. If there is nothing worth remembering, respond with exactly: {_NO_LESSON}"
)


def run_critic(prompt: str, intent: str, result_content: str, settings: "Settings") -> str | None:
    """Returns a short lesson string, or None if there's nothing worth
    remembering or the review itself failed. Never raises."""
    candidates = build_llm_candidates(settings)
    try:
        response = complete_with_fallback(
            candidates,
            temperature=0.2,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Task type: {intent}\nRequest: {prompt}\nOutcome:\n{result_content[:1500]}"},
            ],
        )
        lesson = (response.choices[0].message.content or "").strip()
    except Exception:
        logger.warning("Critic review failed; skipping lesson.", exc_info=True)
        return None
    return None if lesson.upper() == _NO_LESSON else lesson
