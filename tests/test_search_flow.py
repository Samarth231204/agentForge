from types import SimpleNamespace

from backend.templates import search_flow


def test_run_search_synthesis_grounds_the_prompt_in_supplied_sources(monkeypatch):
    captured = {}

    def fake_completion(*, model, api_key, temperature, messages):
        captured["messages"] = messages
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="SYNTHESIZED-ANSWER"))])

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", fake_completion)
    settings = SimpleNamespace(groq_api_key="not-a-real-key", groq_model="openai/gpt-oss-120b", openrouter_api_key="", openrouter_models=())
    sources = [{"title": "Example", "url": "https://example.com", "snippet": "An example snippet."}]

    result = search_flow.run_search_synthesis("What is on example.com?", "some context", sources, settings)

    user_message = captured["messages"][1]["content"]
    assert "What is on example.com?" in user_message
    assert "https://example.com" in user_message
    assert "An example snippet." in user_message
    assert result == "SYNTHESIZED-ANSWER"
