"""Headless-browser tool backed by Playwright, shared across one task's agents."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


class BrowserUnavailable(RuntimeError):
    """Raised when Playwright or the target page cannot complete an action."""


NAME = "browser"
DESCRIPTION = (
    "Control a real headless web browser to navigate public websites, click "
    "elements, fill form fields, and read text directly off the rendered page. "
    "Call navigate before any other action, and use get_text to read the page's "
    "actual content — that is how you verify what happened, not by assuming. "
    "Never enter payment card details, passwords, or other account credentials "
    "into any field — stop and report if a task requires them. EVERY call must "
    "include action, url, selector, and text; set every field not needed by "
    "that action to an empty string."
)
# Every field is required in the schema, rather than optional with a default,
# because Groq's tool-call validator rejects a call missing any
# schema-declared property, even one the schema itself marks optional.
SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["navigate", "click", "fill", "get_text"], "description": "The browser operation to perform."},
        "url": {"type": "string", "description": "URL for the navigate action, or an empty string."},
        "selector": {"type": "string", "description": "CSS selector for click/fill/get_text, or an empty string."},
        "text": {"type": "string", "description": "Text to type for the fill action, or an empty string."},
    },
    "required": ["action", "url", "selector", "text"],
}


class BrowserTool:
    """One Chromium page shared across every action for a single task.

    A fresh browser per call (as a naive implementation might do) would lose
    all navigation state between actions, making a multi-step flow like
    navigate -> fill -> click impossible. Instead one page is created lazily
    on first use and kept open until close() is called at the end of the task.

    Playwright's sync API additionally requires every call (start, page
    actions, stop) to happen on the exact same OS thread that started it, so
    all Playwright work is dispatched through a single dedicated worker
    thread (ThreadPoolExecutor(max_workers=1)) regardless of which thread
    calls run().
    """

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._executor: ThreadPoolExecutor | None = None

    def run(self, action: str = "", url: str = "", selector: str = "", text: str = "") -> str:
        return self._on_worker_thread(self._execute, action, url, selector, text)

    def _on_worker_thread(self, fn: Any, *args: Any) -> Any:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agentforge-browser")
        return self._executor.submit(fn, *args).result()

    def _execute(self, action: str, url: str, selector: str, text: str) -> str:
        page = self._ensure_page()
        try:
            if action == "navigate":
                target = self._safe_url(url)
                page.goto(target, wait_until="domcontentloaded", timeout=30000)
                return f"Navigated to {target}. Title: {page.title()}"
            if action == "click":
                page.click(self._safe_selector(selector), timeout=4000)
                return f"Clicked: {selector}"
            if action == "fill":
                page.fill(self._safe_selector(selector), text, timeout=4000)
                return f"Filled '{selector}'."
            if action == "get_text":
                # A short timeout: a selector that never appears is far more
                # likely than a slow page, and a wrong guess should fail fast
                # so the agent can try a different selector within its
                # iteration budget instead of burning it on 10s waits.
                content = page.inner_text(selector or "body", timeout=4000)
                collapsed = " ".join(content.split())
                return collapsed[:3000] + ("...[truncated]" if len(collapsed) > 3000 else "")
            raise ValueError(f"Unknown browser action: {action}")
        except PlaywrightTimeoutError as exc:
            raise BrowserUnavailable(f"No element matched selector '{selector}' for '{action}' within 4s — it's likely wrong or doesn't exist on this page. Try get_text on a broader selector (e.g. 'body' or a parent element) to see the page's actual structure.") from exc
        except PlaywrightError as exc:
            raise BrowserUnavailable(f"The browser could not complete '{action}': {exc}") from exc

    def _ensure_page(self) -> Any:
        if self._page is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._page = self._browser.new_page()
        return self._page

    def close(self) -> None:
        """Tear down the browser at the end of a task. Safe to call even if
        no page was ever opened, and safe to call more than once."""
        if self._executor is None:
            return
        self._on_worker_thread(self._teardown)
        self._executor.shutdown(wait=True)
        self._executor = None

    def _teardown(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self._page = None

    @staticmethod
    def _safe_url(url: str) -> str:
        if not re.match(r"^https?://", url.strip(), re.IGNORECASE):
            raise ValueError("Only http:// and https:// URLs may be navigated to.")
        return url.strip()

    @staticmethod
    def _safe_selector(selector: str) -> str:
        if not selector.strip():
            raise ValueError("A CSS selector is required for this action.")
        return selector.strip()
