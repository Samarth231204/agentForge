"""Temporary compatibility workarounds for CrewAI provider integrations."""

from __future__ import annotations

from typing import Any


def disable_unsupported_cache_breakpoints() -> None:
    """Prevent CrewAI from sending Anthropic-only metadata to Groq.

    CrewAI currently appends ``cache_breakpoint`` to messages for all providers.
    Groq rejects that field with HTTP 400. This is scoped to AgentForge's Groq
    workflows and can be removed once CrewAI strips it for non-Anthropic models.
    """
    import crewai.llms.cache as crewai_cache

    def unchanged(message: dict[str, Any]) -> dict[str, Any]:
        return message

    crewai_cache.mark_cache_breakpoint = unchanged
