"""Browser-driven bookings and general web interaction: a single custom
tool-calling agent loop (no CrewAI) that navigates, fills forms, and verifies
the outcome in one continuous conversation.

This used to be three sequential stages (Navigator -> Form Filler ->
Confirmer), each restarting with its own system prompt and full tool-schema
overhead. That tripled the token cost of every task for no functional
benefit — the three roles never needed separate conversations, only a
shared browser — and was the single biggest contributor to hitting Groq's
free-tier per-minute token limit. One agent handling all three phases in one
conversation cuts that overhead by roughly a third.

Verification is deliberately text-based rather than a screenshot: nothing
downstream ever looks at an image, so a saved PNG can't actually confirm
anything — reading the DOM with get_text and reasoning over what it says is
the only real evidence available to a text-only LLM.

Tool construction (build_booking_tools) is kept separate from execution
(run_booking_workflow) so the caller can obtain the BrowserTool reference
— and therefore guarantee it gets closed — even if the workflow itself
raises partway through (see CrewEngine._run_booking).
"""

from __future__ import annotations

from backend.agent_loop import ToolSpec, run_agent
from backend.config import Settings
from backend.llm_fallback import build_llm_candidates
from backend.tools import browser_tool as browser_tool_module
from backend.tools import web_search_tool as web_search_tool_module
from backend.tools.browser_tool import BrowserTool
from backend.tools.web_search_tool import WebSearchTool

SAFETY_RULE = (
    "Never enter payment card numbers, CVV codes, bank details, or account "
    "passwords into any field, and never complete a purchase or a login for a "
    "personal account. If the task cannot be completed without one of these, "
    "stop and clearly report that instead of proceeding."
)

SYSTEM_PROMPT = (
    "You handle browser automation tasks end to end: find the right page, interact with it "
    "(fill forms, click, search within the site), and verify the outcome — all before giving "
    "your final answer.\n\n"
    "1. Find the page: if the request names a URL, navigate to it directly; otherwise search "
    "the public web for the specific page first.\n"
    "2. Interact: read the page's structure with get_text before filling anything, translate "
    "the request into precise field-by-field form input, and never submit anything irreversible.\n"
    "3. Verify: never claim success without quoting text you actually read from the page. Call "
    "get_text after any action that might change the page (a click, a form submission) and "
    "re-read as many times as needed until the page's own wording clearly confirms or denies "
    "the outcome — never guess.\n\n"
    f"{SAFETY_RULE}"
)


def build_booking_tools() -> tuple[BrowserTool, ToolSpec, ToolSpec]:
    browser = BrowserTool()
    browser_tool = ToolSpec(
        name=browser_tool_module.NAME,
        description=browser_tool_module.DESCRIPTION,
        parameters=browser_tool_module.SCHEMA,
        execute=browser.run,
    )
    search_tool = ToolSpec(
        name=web_search_tool_module.NAME,
        description=web_search_tool_module.DESCRIPTION,
        parameters=web_search_tool_module.SCHEMA,
        execute=lambda query="": WebSearchTool.as_text(WebSearchTool.search(query)),
    )
    return browser, browser_tool, search_tool


def run_booking_workflow(user_request: str, context: str, settings: Settings, browser_tool: ToolSpec, search_tool: ToolSpec) -> str:
    user_prompt = (
        f"Request: {user_request}\nAdditional context: {context or 'None'}\n\n"
        "Find the correct page, do what the request asks on it, and verify the outcome by "
        "reading the page's own text. Report a plain-language verdict (succeeded / could not "
        "be completed), quoting the exact page text that grounds it."
    )
    return run_agent(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        tools=[search_tool, browser_tool],
        candidates=build_llm_candidates(settings),
        label="Booking Agent",
    )
