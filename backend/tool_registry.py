"""Introspectable registry of every tool AgentForge exposes (Phase 7),
metadata only — for the pipeline planner (Phase 10) to read when deciding
which tools a compound-request pipeline step needs.

Deliberately metadata-only, not construction: browser and web_search need
no per-session state and can be instantiated freely, but github and email
both require per-session credentials (a repo URL/token, a connected Gmail
account) that this registry has no business holding. Each tool's actual
instantiation still happens exactly where it always has —
backend/templates/booking_crew.py for browser/web_search,
backend/crew_engine.py for email, backend/templates/github_crew.py for
github — this module only describes what exists, it never builds anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.tools import browser_tool, email_tool, github_tool, web_search_tool


@dataclass(frozen=True)
class ToolMetadata:
    name: str
    description: str
    schema: dict[str, Any]


TOOL_REGISTRY: tuple[ToolMetadata, ...] = (
    ToolMetadata(name=browser_tool.NAME, description=browser_tool.DESCRIPTION, schema=browser_tool.SCHEMA),
    ToolMetadata(name=web_search_tool.NAME, description=web_search_tool.DESCRIPTION, schema=web_search_tool.SCHEMA),
    ToolMetadata(name=github_tool.NAME, description=github_tool.DESCRIPTION, schema=github_tool.SCHEMA),
    ToolMetadata(name=email_tool.NAME, description=email_tool.DESCRIPTION, schema=email_tool.SCHEMA),
)


def get_tool_registry() -> tuple[ToolMetadata, ...]:
    return TOOL_REGISTRY


def get_tool_names() -> tuple[str, ...]:
    return tuple(tool.name for tool in TOOL_REGISTRY)


def get_tool_by_name(name: str) -> ToolMetadata | None:
    return next((tool for tool in TOOL_REGISTRY if tool.name == name), None)
