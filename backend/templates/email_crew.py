"""Draft-only email workflow: three sequential completions (Planner -> Writer -> Reviewer)."""

from __future__ import annotations

import re

from backend.config import Settings
from backend.llm_fallback import build_llm_candidates, complete_with_fallback

DRAFT_NOTICE = "Draft only — AgentForge did not send or schedule this email."

_EMAIL_ADDRESS_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

_SUBJECT_LINE_PATTERN = re.compile(r"(?im)^subject:\s*(.+)$")


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


def extract_recipient(prompt: str) -> str | None:
    """Best-effort recipient extraction: the first email address literally
    present in the user's request. If the user only named a person, not an
    address, this returns None and the caller must ask for one instead of
    guessing a domain."""
    match = _EMAIL_ADDRESS_PATTERN.search(prompt)
    return match.group(0) if match else None


def run_send_email_workflow(prompt: str, context: str, settings: Settings) -> tuple[str, str]:
    """Produce a subject/body ready to send immediately — no placeholders,
    no draft notice, no meta-commentary — unlike run_email_workflow above,
    whose whole point is to stay clearly marked as a draft."""
    candidates = build_llm_candidates(settings)

    def call(system: str, user: str) -> str:
        response = complete_with_fallback(
            candidates,
            temperature=0.3,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return response.choices[0].message.content or ""

    brief = call(
        "You produce precise email briefs. Extract audience, objective, tone, and calls to action "
        "without inventing facts.",
        f"Create a writing brief for this request: {prompt}\nAdditional context: {context or 'None'}",
    )
    final = call(
        "You write clear, professional emails ready to send immediately, with no placeholders and no "
        "meta-commentary about the email itself.",
        f"Using this brief, write a final subject line and email body ready to send as-is. Return only "
        f"in this exact format:\nSubject: <subject>\nBody: <body>\n\nBrief:\n{brief}",
    )
    subject_match = _SUBJECT_LINE_PATTERN.search(final)
    body_match = re.search(r"(?ims)^body:\s*(.+)", final)
    subject = subject_match.group(1).strip() if subject_match else "Message from AgentForge"
    body = body_match.group(1).strip() if body_match else final.strip()
    return subject, body
