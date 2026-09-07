"""Translate a user request into one of AgentForge Phase 1's safe intents."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

import litellm
from pydantic import BaseModel, Field, ValidationError

from backend.config import Settings, get_settings
from backend.llm_fallback import build_llm_candidates, is_rate_limited

IntentName = Literal["research", "draft_email", "send_email", "github", "booking", "unsupported"]


class Intent(BaseModel):
    intent: IntentName
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)
    task_summary: str = Field(min_length=1, max_length=500)
    constraints: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = (
    "You classify requests for an AgentForge agent application. Return JSON only, with keys "
    "intent, confidence, reason, task_summary, constraints. Valid intent values: research, "
    "draft_email, send_email, github, booking, unsupported. research is for finding, comparing, explaining, or summarizing "
    "public information. draft_email is for writing or revising an email; no email can be sent "
    "in this phase. send_email is for actually sending an email through a connected Gmail account "
    "(only ever choose this if the user clearly wants the email sent, not merely drafted). "
    "github is for repository changes, tests, branches, or pull requests. "
    "booking is for browser automation on public websites: reservations, checking availability, "
    "filling out a public form, or navigating a specific site and interacting with it (clicking, "
    "searching within the site, reading a resulting page) — even if the underlying goal looks like "
    "finding information. If the request names or clearly implies visiting a specific website and "
    "doing something on it, choose booking over research. research is only for an open-ended web "
    "search with no specific site to visit. Never choose booking for a login or a payment. "
    "unsupported is for logins, payments, or any action outside research, email drafting, GitHub, and booking."
)


class IntentParser:
    """Classifies requests via Groq (through LiteLLM's JSON mode)."""

    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self._settings = settings
        # `client` is injectable for tests: any callable with litellm.completion's
        # signature (model=..., messages=..., ...) -> an object exposing
        # .choices[0].message.content, which is what litellm.completion itself returns.
        self._client = client or litellm.completion

    def parse(self, prompt: str, context: str = "", has_repo_context: bool = False, has_gmail_context: bool = False) -> Intent:
        forced = self._forced_intent(prompt, has_repo_context, has_gmail_context)
        if forced is not None:
            return forced
        settings = self._settings or get_settings()
        repo_note = (
            "\\nNote: The user has a specific GitHub repository connected for this session "
            "(a repo URL and token are configured). If this request could plausibly mean creating, "
            "writing, editing, testing, or otherwise changing a file or code, classify it as github "
            "even if it never says so explicitly." if has_repo_context else ""
        )
        gmail_note = (
            "\\nNote: The user has connected a Gmail account for this session. If the request clearly "
            "asks to send (not merely draft) an email, classify it as send_email." if has_gmail_context else ""
        )
        user_text = f"Request: {prompt}\\nContext: {context or '(none)'}{repo_note}{gmail_note}"
        # Two independent failure modes, handled differently: a malformed
        # JSON reply (successful call, bad content) gets a corrective retry
        # on the *same* candidate, up to 2 attempts — unrelated to which
        # model answered. A provider-level exception (rate limit, etc.)
        # instead advances to the next candidate model for a fresh attempt,
        # since retrying the same exhausted model would just fail identically.
        for model, api_key in build_llm_candidates(settings):
            provider_failed = False
            for attempt in range(2):
                try:
                    message = self._client(
                        model=model,
                        api_key=api_key,
                        temperature=0,
                        response_format={"type": "json_object"},
                        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_text if attempt == 0 else user_text + "\\nYour previous answer was invalid. Return valid JSON only."}],
                    )
                except Exception as exc:
                    if not is_rate_limited(exc):
                        raise
                    provider_failed = True
                    break
                try:
                    content = message.choices[0].message.content or "{}"
                    return Intent.model_validate(json.loads(content))
                except (json.JSONDecodeError, ValidationError, AttributeError, IndexError):
                    continue
            if not provider_failed:
                # This candidate answered but never produced valid JSON —
                # trying a different model is unlikely to fix a content
                # problem, so stop here rather than cascading through the
                # rest of the candidate list.
                break
        return Intent(intent="unsupported", confidence=0.0, reason="I could not reliably classify this request. Try rephrasing it as research or an email draft.", task_summary="Unclassified request")

    _FILE_EXTENSION_PATTERN = re.compile(r"\b[\w-]+\.(html?|css|js|jsx|ts|tsx|py|json|ya?ml|md|txt|java|go|rb|c|cpp|h|sql|sh)\b")
    # A navigation verb followed by anything other than a generic English
    # filler word is an unambiguous signal for browser automation — whether
    # the target is a literal URL ("go to https://...") or a site named by
    # brand ("open YouTube", "open Wikipedia") — even though the underlying
    # goal often reads like a research question on the surface.
    _NAV_VERB_PATTERN = re.compile(r"\b(?:go to|navigate to|open|visit)\s+([a-z0-9][\w.:/-]*)")
    _GENERIC_NAV_TARGETS = {"a", "an", "the", "up", "this", "that", "it", "new", "my", "your", "some", "another", "file", "files"}

    # Phrases where the user unambiguously wants the email actually sent, not
    # just drafted. Checked before the draft-shaped phrases below since
    # "send an email to bob@x.com" would otherwise also match "email to".
    _SEND_EMAIL_PATTERNS = ("send email", "send an email", "send a mail", "send this email", "schedule email")

    @classmethod
    def _forced_intent(cls, prompt: str, has_repo_context: bool = False, has_gmail_context: bool = False) -> Intent | None:
        # Collapse whitespace before matching fixed phrases below — otherwise
        # an incidental double space (e.g. "send  this email") breaks a
        # literal substring match like "send this email" and falls through
        # to a weaker pattern ("email to") that forces draft_email regardless
        # of Gmail connection state.
        text = re.sub(r"\s+", " ", prompt.lower())
        if any(word in text for word in ("github", "pull request", "clone repo", "repository", "repo ", "repo.")):
            return Intent(intent="github", confidence=0.98, reason="This request requires AgentForge's GitHub repository workflow.", task_summary="GitHub repository task")
        if any(word in text for word in ("log in", "log into", "payment", "credit card", "pay for")):
            return Intent(intent="unsupported", confidence=1.0, reason="Logins and payments are not supported for safety reasons.", task_summary="Login/payment request")
        # A GitHub repo is connected for this session (sidebar has a repo URL
        # and token) and the prompt names a file or an edit-shaped verb —
        # e.g. "create welcome.html and write a page in it" has no GitHub
        # keyword at all, but is unambiguously a repository task given that
        # context. Checked before the booking nav-verb pattern below so
        # "open main.py and add a comment" doesn't get misread as opening a
        # website just because "main.py" isn't a generic filler word.
        if has_repo_context and (
            cls._FILE_EXTENSION_PATTERN.search(text)
            or any(phrase in text for phrase in ("create a file", "write a file", "new file", "add a file", "edit the file", "modify the file", "delete the file", "update the file"))
        ):
            return Intent(intent="github", confidence=0.9, reason="A GitHub repository is connected for this session and this request describes a file or code change.", task_summary="GitHub repository task")
        nav_match = cls._NAV_VERB_PATTERN.search(text)
        if nav_match and nav_match.group(1).strip(".,!?") not in cls._GENERIC_NAV_TARGETS:
            return Intent(intent="booking", confidence=0.95, reason="This request names a specific site or page to open and interact with.", task_summary="Browser automation / booking task")
        if any(word in text for word in ("browser", "book a", "booking", "reservation", "reserve a", "check availability")):
            return Intent(intent="booking", confidence=0.95, reason="This request requires AgentForge's browser automation workflow.", task_summary="Browser automation / booking task")
        if any(phrase in text for phrase in cls._SEND_EMAIL_PATTERNS):
            if has_gmail_context:
                return Intent(intent="send_email", confidence=0.95, reason="A Gmail account is connected for this session and this request asks to send an email.", task_summary="Send an email")
            return Intent(intent="draft_email", confidence=0.9, reason="No Gmail account is connected for this session, so AgentForge can only draft this email. Connect Gmail in the sidebar to send it.", task_summary="Create an email draft")
        if any(word in text for word in ("write an email", "draft an email", "email to")):
            return Intent(intent="draft_email", confidence=0.95, reason="This request asks to draft an email.", task_summary="Create an email draft")
        return None
