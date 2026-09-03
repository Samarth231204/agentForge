"""A normalized DuckDuckGo search tool for AgentForge research tasks."""

from __future__ import annotations

from typing import Any

from crewai.tools import BaseTool
from ddgs import DDGS
from pydantic import BaseModel, Field


class SearchUnavailable(RuntimeError):
    """A provider failure that is safe to expose to the UI."""


class WebSearchInput(BaseModel):
    query: str = Field(description="A focused public-web search query.")
    max_results: int = Field(default=5, ge=1, le=5)


class WebSearchTool(BaseTool):
    name: str = "web_search"
    description: str = "Search the public web and return concise, normalized results."
    args_schema: type[BaseModel] = WebSearchInput

    def _run(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        return self.search(query, max_results)

    @staticmethod
    def search(query: str, max_results: int = 5) -> list[dict[str, str]]:
        cleaned_query = query.strip()
        if not cleaned_query:
            raise ValueError("Search query cannot be blank.")
        limit = max(1, min(int(max_results), 5))
        try:
            raw_results = DDGS().text(cleaned_query, max_results=limit)
        except Exception as exc:
            raise SearchUnavailable("Web search is temporarily unavailable.") from exc
        results: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for item in raw_results or []:
            normalized = WebSearchTool._normalize(item)
            if normalized is None or normalized["url"] in seen_urls:
                continue
            seen_urls.add(normalized["url"])
            results.append(normalized)
            if len(results) == limit:
                break
        return results

    @staticmethod
    def _normalize(item: dict[str, Any]) -> dict[str, str] | None:
        url = str(item.get("href") or item.get("url") or "").strip()
        if not url.startswith(("https://", "http://")):
            return None
        title = " ".join(str(item.get("title") or "Untitled result").split())
        snippet = " ".join(str(item.get("body") or item.get("snippet") or "").split())
        return {"title": title, "url": url, "snippet": snippet}
