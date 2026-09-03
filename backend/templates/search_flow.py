"""Research workflow: deterministic retrieval followed by CrewAI synthesis."""

from __future__ import annotations

from crewai import Agent, Crew, LLM, Process, Task

from backend.config import Settings
from backend.crewai_compat import disable_unsupported_cache_breakpoints


def create_search_crew(prompt: str, context: str, sources: list[dict[str, str]], settings: Settings) -> Crew:
    disable_unsupported_cache_breakpoints()
    llm = LLM(model=f"groq/{settings.groq_model}", api_key=settings.groq_api_key, temperature=0.2)
    synthesizer = Agent(role="Research Synthesizer", goal="Answer accurately using only the supplied search results.", backstory="You turn short public-web search extracts into concise, transparent research summaries.", llm=llm, verbose=False)
    source_text = "\n\n".join(f"Title: {item['title']}\nURL: {item['url']}\nSnippet: {item['snippet']}" for item in sources)
    task = Task(description=f"Answer the request: {prompt}\nContext: {context or 'None'}\n\nUse only these search results:\n{source_text}\n\nDo not invent citations or facts. Clearly mark any recommendation or inference.", expected_output="A concise markdown answer grounded only in the supplied source snippets.", agent=synthesizer)
    return Crew(agents=[synthesizer], tasks=[task], process=Process.sequential, verbose=False)
