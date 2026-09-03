import pytest

pytest.importorskip("crewai", reason="requires the CrewAI runtime declared by Phase 1")

from backend.tools.web_search_tool import SearchUnavailable, WebSearchTool


def test_normalizes_deduplicates_and_limits_results(monkeypatch):
    class FakeDDGS:
        def text(self, _query, max_results):
            assert max_results == 5
            return [
                {"title": " First   title ", "href": "https://example.com/a", "body": " A   snippet "},
                {"title": "Duplicate", "href": "https://example.com/a", "body": "ignored"},
                {"title": "Missing URL"},
                {"title": "Second", "href": "https://example.com/b", "body": "Second snippet"},
            ]

    monkeypatch.setattr("backend.tools.web_search_tool.DDGS", FakeDDGS)
    assert WebSearchTool.search("  agent systems  ", 99) == [
        {"title": "First title", "url": "https://example.com/a", "snippet": "A snippet"},
        {"title": "Second", "url": "https://example.com/b", "snippet": "Second snippet"},
    ]


def test_rejects_blank_queries():
    with pytest.raises(ValueError, match="blank"):
        WebSearchTool.search("   ")


def test_hides_provider_failures(monkeypatch):
    class BrokenDDGS:
        def text(self, *_args, **_kwargs):
            raise RuntimeError("provider detail should not escape")

    monkeypatch.setattr("backend.tools.web_search_tool.DDGS", BrokenDDGS)
    with pytest.raises(SearchUnavailable, match="temporarily unavailable"):
        WebSearchTool.search("test")
