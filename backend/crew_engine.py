"""Run the selected Phase 1 workflow and report progress through EventEmitter."""

from __future__ import annotations

import re
import time
from typing import Any

from backend.auth.token_store import get_token_store
from backend.config import Settings, get_settings
from backend.event_emitter import EventEmitter
from backend.intent_parser import Intent
from backend.memory_manager import get_memory_manager
from backend.templates.booking_crew import build_booking_tools, run_booking_workflow
from backend.templates.email_crew import DRAFT_NOTICE, extract_recipient, run_email_workflow, run_send_email_workflow
from backend.templates.github_crew import run_github_workflow
from backend.templates.search_flow import run_search_synthesis
from backend.tools.email_tool import GmailUnavailable, send_email
from backend.tools.web_search_tool import SearchUnavailable, WebSearchTool


class CrewEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def run(self, intent: Intent, prompt: str, context: str, emitter: EventEmitter, *, repo_url: str = "", github_token: str = "", session_id: str = "", gmail_session_id: str = "") -> dict[str, Any]:
        if intent.intent == "unsupported":
            message = f"{intent.reason} AgentForge can research public information, draft an email, work on a GitHub repository, or run a browser-automation/booking task."
            return {"intent": "unsupported", "content": message, "sources": []}
        if intent.intent == "draft_email":
            return self._run_email(prompt, context, session_id, gmail_session_id, emitter)
        if intent.intent == "send_email":
            return self._run_send_email(prompt, context, gmail_session_id, emitter)
        if intent.intent == "github":
            return self._run_github(intent.task_summary, prompt, repo_url, github_token, session_id, emitter)
        if intent.intent == "booking":
            return self._run_booking(prompt, context, emitter)
        return self._run_research(prompt, context, session_id, gmail_session_id, emitter)

    @staticmethod
    def _recall_context(prompt: str, context: str, session_id: str, gmail_session_id: str) -> str:
        """Append remembered personal preferences and critic-extracted
        lessons onto an existing context string — memory recall reuses this
        existing prompt input rather than adding a new one. Best-effort: a
        Mem0 outage never raises, it just contributes nothing (see
        NullMemoryManager / Mem0MemoryManager's own exception handling)."""
        user_id = gmail_session_id or session_id or "anonymous"
        memory = get_memory_manager()
        personal = memory.recall(f"prefs:{user_id}", prompt, limit=3)
        lessons = memory.recall("lessons:global", prompt, limit=2)
        extra_lines = []
        if personal:
            extra_lines.append(f"Remembered from past sessions: {'; '.join(personal)}")
        if lessons:
            extra_lines.append(f"Lessons learned: {'; '.join(lessons)}")
        if not extra_lines:
            return context
        return f"{context}\n{chr(10).join(extra_lines)}".strip()

    def _run_github(self, task_summary: str, prompt: str, repo_url: str, github_token: str, session_id: str, emitter: EventEmitter) -> dict[str, Any]:
        if not repo_url or not github_token:
            return {"intent": "github", "content": "Add a GitHub HTTPS repository URL and fine-grained token in the sidebar before running a repository task.", "sources": []}
        agents = ["Code Reader", "Software Engineer", "QA Engineer", "PR Manager"]
        emitter.emit("workflow_started", {"workflow": "github_crew", "agents": agents})
        for agent, role in zip(agents, ("repository inspection", "implementation", "testing", "pull request management")):
            emitter.emit("agent_started", {"agent": agent, "role": role})
        # A caller-supplied session_id lets multiple queries share one branch
        # (see run_github_workflow); fall back to the task id for callers
        # (tests, direct API use) that don't track a session of their own.
        github_session_id = session_id or emitter.task_id
        output = run_github_workflow(prompt, repo_url, github_token, github_session_id, self.settings)
        for agent in agents:
            emitter.emit("agent_completed", {"agent": agent, "summary": "Completed its repository-workflow stage."})
        return {"intent": "github", "content": output, "sources": []}

    @staticmethod
    def _is_rate_limited(error: Exception) -> bool:
        """Recognize a transient, retry-worthy provider error. Deliberately
        excludes plain "400" — a malformed-request error is permanent and
        retrying it verbatim would just fail identically."""
        err_str = str(error).lower()
        return error.__class__.__name__ == "RateLimitError" or "rate_limit_exceeded" in err_str or "resource_exhausted" in err_str or "429" in err_str or "503" in err_str or "unavailable" in err_str

    @staticmethod
    def _retry_delay(error: Exception) -> float:
        """A 5-20s safety window around the provider's own reported delay, so a
        malformed or extreme hint from the provider can't produce a near-zero
        or unreasonably long wait."""
        err_str = str(error).lower()
        if match := re.search(r"(?:try again|retry) in\s+([0-9.]+)\s*s", err_str):
            return max(5.0, min(20.0, float(match.group(1)) + 1.0))
        if "503" in err_str or "unavailable" in err_str:
            return 30.0
        return 5.0

    def _run_email(self, prompt: str, context: str, session_id: str, gmail_session_id: str, emitter: EventEmitter) -> dict[str, Any]:
        agents = ["Email Planner", "Email Writer", "Email Reviewer"]
        emitter.emit("workflow_started", {"workflow": "email_crew", "agents": agents})
        for agent, role in zip(agents, ("planning", "drafting", "reviewing")):
            emitter.emit("agent_started", {"agent": agent, "role": role})
        context = self._recall_context(prompt, context, session_id, gmail_session_id)
        output = run_email_workflow(prompt, context, self.settings)
        if DRAFT_NOTICE not in output:
            output = f"{output.rstrip()}\n\n{DRAFT_NOTICE}"
        for agent in agents:
            emitter.emit("agent_completed", {"agent": agent, "summary": "Completed its email-drafting stage."})
        return {"intent": "draft_email", "content": output, "sources": []}

    def _run_send_email(self, prompt: str, context: str, gmail_session_id: str, emitter: EventEmitter) -> dict[str, Any]:
        agents = ["Email Planner", "Email Writer", "Gmail Sender"]
        emitter.emit("workflow_started", {"workflow": "send_email", "agents": agents})
        if not gmail_session_id:
            emitter.emit("error", {"code": "gmail_not_connected", "message": "Connect a Gmail account in the sidebar before sending an email.", "retryable": False})
            return {"intent": "send_email", "content": "Connect a Gmail account in the sidebar before sending an email.", "sources": []}
        recipient = extract_recipient(prompt) or extract_recipient(context)
        if not recipient:
            emitter.emit("error", {"code": "missing_recipient", "message": "No recipient email address was found in the request.", "retryable": False})
            return {"intent": "send_email", "content": "I could not find a recipient email address in this request. Include the address you want to send to.", "sources": []}
        for agent, role in zip(agents[:2], ("planning", "drafting")):
            emitter.emit("agent_started", {"agent": agent, "role": role})
        subject, body = run_send_email_workflow(prompt, context, self.settings)
        for agent in agents[:2]:
            emitter.emit("agent_completed", {"agent": agent, "summary": "Completed its email-drafting stage."})
        emitter.emit("agent_started", {"agent": "Gmail Sender", "role": "sending via the connected Gmail account"})
        try:
            result = send_email(session_id=gmail_session_id, to=recipient, subject=subject, body=body, token_store=get_token_store())
        except GmailUnavailable as exc:
            emitter.emit("error", {"code": "gmail_send_failed", "message": str(exc), "retryable": True})
            return {"intent": "send_email", "content": str(exc), "sources": []}
        emitter.emit("agent_completed", {"agent": "Gmail Sender", "summary": result})
        return {"intent": "send_email", "content": result, "sources": []}

    def _run_booking(self, prompt: str, context: str, emitter: EventEmitter) -> dict[str, Any]:
        agents = ["Booking Agent"]
        emitter.emit("workflow_started", {"workflow": "booking_crew", "agents": agents})
        emitter.emit("agent_started", {"agent": "Booking Agent", "role": "finding the page, interacting with it, and verifying the outcome"})
        browser, browser_tool, search_tool = build_booking_tools()
        try:
            output = run_booking_workflow(prompt, context, self.settings, browser_tool, search_tool)
        finally:
            # The shared BrowserTool's Chromium instance must always be torn
            # down, even if the agent errors partway through the task.
            browser.close()
        emitter.emit("agent_completed", {"agent": "Booking Agent", "summary": "Completed the browser-automation task."})
        return {"intent": "booking", "content": output, "sources": []}

    def _run_research(self, prompt: str, context: str, session_id: str, gmail_session_id: str, emitter: EventEmitter) -> dict[str, Any]:
        emitter.emit("workflow_started", {"workflow": "search_flow", "agents": ["Researcher", "Research Synthesizer"]})
        emitter.emit("agent_started", {"agent": "Researcher", "role": "web research"})
        emitter.emit("tool_started", {"tool": "web_search", "input_summary": prompt[:500]})
        try:
            sources = WebSearchTool.search(prompt)
        except SearchUnavailable:
            emitter.emit("error", {"code": "search_unavailable", "message": "Web search is temporarily unavailable. Please retry shortly.", "retryable": True})
            return {"intent": "research", "content": "Web search is temporarily unavailable. Please retry shortly.", "sources": []}
        emitter.emit("tool_completed", {"tool": "web_search", "summary": f"Found {len(sources)} usable sources."})
        emitter.emit("agent_completed", {"agent": "Researcher", "summary": f"Collected {len(sources)} sources."})
        if not sources:
            return {"intent": "research", "content": "I could not find reliable public-web results for that request.", "sources": []}
        emitter.emit("agent_started", {"agent": "Research Synthesizer", "role": "source-grounded synthesis"})
        context = self._recall_context(prompt, context, session_id, gmail_session_id)
        output = run_search_synthesis(prompt, context, sources, self.settings)
        emitter.emit("agent_completed", {"agent": "Research Synthesizer", "summary": "Created a source-grounded answer."})
        return {"intent": "research", "content": output, "sources": [item["url"] for item in sources]}
