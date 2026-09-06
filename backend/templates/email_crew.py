"""Draft-only email workflow: three sequential completions (Planner -> Writer -> Reviewer)."""

from __future__ import annotations

from backend.config import Settings
from backend.llm_fallback import build_llm_candidates, complete_with_fallback

DRAFT_NOTICE = "Draft only — AgentForge did not send or schedule this email."


def run_email_workflow(prompt: str, context: str, settings: Settings) -> str:
    candidates = build_llm_candidates(settings)

    def call(system: str, user: str) -> str:
        response = complete_with_fallback(
            candidates,
            temperature=0.3,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return response.choices[0].message.content or ""

    brief = call(
        "You produce precise email briefs. Extract audience, objective, tone, calls to action, and "
        "missing details without inventing facts. List missing information as placeholders.",
        f"Create a writing brief for this request: {prompt}\nAdditional context: {context or 'None'}",
    )
    draft = call(
        "You write clear, useful, professional email drafts from an approved brief.",
        f"Using this brief, write a subject line and email body. Use [placeholders] for unknown facts. "
        f"Do not claim this message was sent.\n\nBrief:\n{brief}",
    )
    review = call(
        "You catch ambiguity, unsupported claims, and accidental promises in a candidate email.",
        f"Review this candidate email against its brief. Return only the final text in this format: "
        f"Subject: ... then Body: ... then a final line: {DRAFT_NOTICE}\n\nBrief:\n{brief}\n\nDraft:\n{draft}",
    )
    return review
