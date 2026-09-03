"""Translate a user request into one of AgentForge Phase 1's safe intents."""

from __future__ import annotations

import json
import os
from typing import Any, Literal

from groq import Groq
from pydantic import BaseModel, Field, ValidationError

from backend.config import Settings, get_settings

IntentName = Literal["research", "draft_email", "github", "unsupported"]


class Intent(BaseModel):
    intent: IntentName
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)
    task_summary: str = Field(min_length=1, max_length=500)
    constraints: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = (
    "You classify requests for a Phase 1 agent application. Return JSON only, with keys "
    "intent, confidence, reason, task_summary, constraints. Valid intent values: research, "
    "draft_email, github, unsupported. research is for finding, comparing, explaining, or summarizing "
    "public information. draft_email is for writing or revising an email; no email can be sent "
    "in this phase. github is for repository changes, tests, branches, or pull requests. "
    "unsupported is for browser automation, bookings, "
    "logins, payments, or any action outside research and email drafting."
)


class IntentParser:
    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            settings = self._settings or get_settings()
            self._client = Groq(api_key=settings.groq_api_key)
        return self._client

    def parse(self, prompt: str, context: str = "") -> Intent:
        forced = self._forced_intent(prompt)
        if forced is not None:
            return forced
        user_text = f"Request: {prompt}\\nContext: {context or '(none)'}"
        for attempt in range(2):
            try:
                message = self.client.chat.completions.create(
                    model=self._model_name(), temperature=0,
                    response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_text if attempt == 0 else user_text + "\\nYour previous answer was invalid. Return valid JSON only."}],
                )
                content = message.choices[0].message.content or "{}"
                return Intent.model_validate(json.loads(content))
            except (json.JSONDecodeError, ValidationError, AttributeError, IndexError):
                continue
        return Intent(intent="unsupported", confidence=0.0, reason="I could not reliably classify this request. Try rephrasing it as research or an email draft.", task_summary="Unclassified request")

    def _model_name(self) -> str:
        if self._settings is not None:
            return self._settings.groq_model
        return os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

    @staticmethod
    def _forced_intent(prompt: str) -> Intent | None:
        text = prompt.lower()
        if any(word in text for word in ("github", "pull request", "clone repo", "repository", "repo ", "repo.")):
            return Intent(intent="github", confidence=0.98, reason="This request requires AgentForge's GitHub repository workflow.", task_summary="GitHub repository task")
        if any(word in text for word in ("browser", "book a", "booking", "log in", "payment")):
            return Intent(intent="unsupported", confidence=1.0, reason="This action requires a later AgentForge phase and is not available yet.", task_summary="Future-phase request")
        if any(word in text for word in ("send email", "schedule email", "write an email", "draft an email", "email to")):
            return Intent(intent="draft_email", confidence=0.95, reason="Phase 1 can create an email draft but cannot send or schedule it.", task_summary="Create an email draft")
        return None
