"""Research workflow: deterministic retrieval followed by a direct LLM synthesis call."""

from __future__ import annotations

from backend.config import Settings
from backend.llm_fallback import build_llm_candidates, complete_with_fallback


def run_search_synthesis(prompt: str, context: str, sources: list[dict[str, str]], settings: Settings) -> str:
    source_text = "\n\n".join(f"Title: {item['title']}\nURL: {item['url']}\nSnippet: {item['snippet']}" for item in sources)
    system_prompt = (
        "You turn short public-web search extracts into concise, transparent research summaries. "
        "Give the best grounded answer you can from these snippets, even if none of them states the "
        "answer word-for-word — extract and combine what's actually relevant instead of demanding an "
        "exact literal match. Only refuse to answer if the snippets are genuinely unrelated to the "
        "request; if the match is partial or approximate, answer with that content and say so "
        "explicitly (e.g. 'the closest information available is ...') rather than declining outright. "
        "Do not invent citations or facts, and clearly mark any recommendation or inference."
    )
    user_prompt = f"Answer the request: {prompt}\nContext: {context or 'None'}\n\nUse only these search results:\n{source_text}"
    response = complete_with_fallback(
        build_llm_candidates(settings),
        temperature=0.2,
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
    )
    return response.choices[0].message.content or ""
