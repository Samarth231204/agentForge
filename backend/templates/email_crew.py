"""CrewAI workflow for producing reviewed, never-sent email drafts."""

from __future__ import annotations

from crewai import Agent, Crew, LLM, Process, Task

from backend.config import Settings
from backend.crewai_compat import disable_unsupported_cache_breakpoints

DRAFT_NOTICE = "Draft only — AgentForge did not send or schedule this email."


def create_email_crew(prompt: str, context: str, settings: Settings) -> Crew:
    disable_unsupported_cache_breakpoints()
    llm = LLM(model=f"groq/{settings.groq_model}", api_key=settings.groq_api_key, temperature=0.3)
    planner = Agent(role="Email Planner", goal="Extract audience, objective, tone, CTA, and missing details without inventing facts.", backstory="You produce precise email briefs.", llm=llm, verbose=False)
    writer = Agent(role="Email Writer", goal="Write concise, useful email drafts from an approved brief.", backstory="You write clear professional communication.", llm=llm, verbose=False)
    reviewer = Agent(role="Email Reviewer", goal="Return a polished, safe final draft that is explicitly never sent.", backstory="You catch ambiguity, unsupported claims, and accidental promises.", llm=llm, verbose=False)

    brief = Task(description=f"Create a writing brief for this request: {prompt}\nAdditional context: {context or 'None'}\nList missing information as placeholders.", expected_output="A concise email brief with audience, purpose, tone, CTA, and placeholders.", agent=planner)
    draft = Task(description="Using the planner's brief, write a subject line and email body. Use [placeholders] for unknown facts. Do not claim this message was sent.", expected_output="A subject line and complete email body.", agent=writer, context=[brief])
    review = Task(description=f"Review the candidate email. Return only the final text in this format: Subject: ... then Body: ... then a final line: {DRAFT_NOTICE}", expected_output="A polished draft with the mandatory draft-only notice.", agent=reviewer, context=[brief, draft])
    return Crew(agents=[planner, writer, reviewer], tasks=[brief, draft, review], process=Process.sequential, verbose=False)
