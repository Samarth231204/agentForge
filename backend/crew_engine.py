"""Run the selected Phase 1 workflow and report progress through EventEmitter."""

from __future__ import annotations

import re
import time
from typing import Any

from backend.config import Settings, get_settings
from backend.event_emitter import EventEmitter
from backend.intent_parser import Intent
from backend.templates.email_crew import DRAFT_NOTICE, create_email_crew
from backend.templates.github_crew import run_github_workflow
from backend.templates.search_flow import create_search_crew
from backend.tools.web_search_tool import SearchUnavailable, WebSearchTool


class CrewEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def run(self, intent: Intent, prompt: str, context: str, emitter: EventEmitter, *, repo_url: str = "", github_token: str = "", session_id: str = "") -> dict[str, Any]:
        if intent.intent == "unsupported":
            message = f"{intent.reason} In Phase 1, AgentForge can research public information or create an email draft."
            return {"intent": "unsupported", "content": message, "sources": []}
        if intent.intent == "draft_email":
            return self._run_email(prompt, context, emitter)
        if intent.intent == "github":
            return self._run_github(intent.task_summary, prompt, repo_url, github_token, session_id, emitter)
        return self._run_research(prompt, context, emitter)

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
        err_str = str(error).lower()
        return error.__class__.__name__ == "RateLimitError" or "rate_limit_exceeded" in err_str or "resource_exhausted" in err_str or "429" in err_str or "400" in err_str or "503" in err_str or "unavailable" in err_str

    @staticmethod
    def _retry_delay(error: Exception) -> float:
        import re
        err_str = str(error).lower()
        if match := re.search(r"(?:try again|retry) in\s+([0-9.]+)\s*s", err_str):
            return float(match.group(1)) + 1.0
        if "503" in err_str or "unavailable" in err_str:
            return 30.0
        return 5.0

    def _run_email(self, prompt: str, context: str, emitter: EventEmitter) -> dict[str, Any]:
        agents = ["Email Planner", "Email Writer", "Email Reviewer"]
        emitter.emit("workflow_started", {"workflow": "email_crew", "agents": agents})
        for agent, role in zip(agents, ("planning", "drafting", "reviewing")):
            emitter.emit("agent_started", {"agent": agent, "role": role})
        output = str(create_email_crew(prompt, context, self.settings).kickoff())
        if DRAFT_NOTICE not in output:
            output = f"{output.rstrip()}\n\n{DRAFT_NOTICE}"
        for agent in agents:
            emitter.emit("agent_completed", {"agent": agent, "summary": "Completed its email-drafting stage."})
        return {"intent": "draft_email", "content": output, "sources": []}

    def _run_research(self, prompt: str, context: str, emitter: EventEmitter) -> dict[str, Any]:
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
        output = str(create_search_crew(prompt, context, sources, self.settings).kickoff())
        emitter.emit("agent_completed", {"agent": "Research Synthesizer", "summary": "Created a source-grounded answer."})
        return {"intent": "research", "content": output, "sources": [item["url"] for item in sources]}
