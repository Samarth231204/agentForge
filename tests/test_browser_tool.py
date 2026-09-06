import pytest

from backend.tools.browser_tool import SCHEMA, BrowserTool, BrowserUnavailable


def test_tool_schema_requires_explicit_empty_values_for_groq_native_tools():
    # Groq's native tool parser validates every declared property regardless
    # of the JSON schema's `required` list, so a field that's merely optional
    # can still be rejected as "missing" when the model omits it for an
    # action that doesn't need it. Every field must be required in the
    # schema, with instructions to pass "" when irrelevant.
    assert SCHEMA["required"] == ["action", "url", "selector", "text"]


class FakePage:
    def __init__(self):
        self.goto_calls = []
        self.title_value = "Example Domain"

    def goto(self, url, **_kwargs):
        self.goto_calls.append(url)

    def title(self):
        return self.title_value

    def click(self, selector, **_kwargs):
        self.clicked = selector

    def fill(self, selector, text, **_kwargs):
        self.filled = (selector, text)

    def inner_text(self, selector, **_kwargs):
        return "  lots   of   whitespace   " + ("x" * 5000)


class FakeBrowser:
    def __init__(self, page):
        self._page = page
        self.closed = False

    def new_page(self):
        return self._page

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, browser):
        self._browser = browser

    def launch(self, headless=True):
        return self._browser


class FakePlaywrightContext:
    def __init__(self, browser):
        self.chromium = FakeChromium(browser)
        self.stopped = False

    def stop(self):
        self.stopped = True


def _install_fake_playwright(monkeypatch):
    page = FakePage()
    browser = FakeBrowser(page)
    context = FakePlaywrightContext(browser)
    monkeypatch.setattr("backend.tools.browser_tool.sync_playwright", lambda: SimpleStart(context))
    return page, browser, context


class SimpleStart:
    def __init__(self, context):
        self._context = context

    def start(self):
        return self._context


def test_navigate_rejects_non_http_schemes(monkeypatch):
    _install_fake_playwright(monkeypatch)
    tool = BrowserTool()
    with pytest.raises(ValueError, match="http"):
        tool.run("navigate", url="file:///etc/passwd")


def test_click_and_fill_require_a_selector(monkeypatch):
    _install_fake_playwright(monkeypatch)
    tool = BrowserTool()
    with pytest.raises(ValueError, match="selector"):
        tool.run("click", selector="")


def test_page_state_persists_across_calls(monkeypatch):
    page, browser, _context = _install_fake_playwright(monkeypatch)
    tool = BrowserTool()
    tool.run("navigate", url="https://example.com")
    tool.run("click", selector="#submit")
    # The same FakePage instance must have handled both calls.
    assert page.goto_calls == ["https://example.com"]
    assert page.clicked == "#submit"


def test_get_text_is_collapsed_and_capped(monkeypatch):
    _install_fake_playwright(monkeypatch)
    tool = BrowserTool()
    result = tool.run("get_text", selector="body")
    assert "  " not in result
    assert len(result) <= 3000 + len("...[truncated]")
    assert result.endswith("...[truncated]")


def test_close_tears_down_browser_and_playwright(monkeypatch):
    _page, browser, context = _install_fake_playwright(monkeypatch)
    tool = BrowserTool()
    tool.run("navigate", url="https://example.com")
    tool.close()
    assert browser.closed is True
    assert context.stopped is True


def test_close_is_safe_when_nothing_was_ever_opened():
    BrowserTool().close()
