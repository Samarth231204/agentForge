from types import SimpleNamespace

from backend.critic_agent import run_critic

_SETTINGS = SimpleNamespace(groq_api_key="not-a-real-key", groq_model="openai/gpt-oss-120b", openrouter_api_key="", openrouter_models=())


def _fake_completion(content):
    def fake(*, model, api_key, temperature, messages):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    return fake


def test_returns_the_lesson_text_when_the_model_finds_one(monkeypatch):
    monkeypatch.setattr("backend.llm_fallback.litellm.completion", _fake_completion("Always confirm the recipient before sending."))
    lesson = run_critic("send an email", "send_email", "Email sent to bob@example.com", _SETTINGS)
    assert lesson == "Always confirm the recipient before sending."


def test_returns_none_when_the_model_reports_no_lesson(monkeypatch):
    monkeypatch.setattr("backend.llm_fallback.litellm.completion", _fake_completion("NONE"))
    lesson = run_critic("research something", "research", "Some result", _SETTINGS)
    assert lesson is None


def test_returns_none_when_the_model_reports_no_lesson_case_insensitively(monkeypatch):
    monkeypatch.setattr("backend.llm_fallback.litellm.completion", _fake_completion("none"))
    lesson = run_critic("research something", "research", "Some result", _SETTINGS)
    assert lesson is None


def test_returns_none_when_the_completion_call_raises(monkeypatch):
    def fake(*, model, api_key, temperature, messages):
        raise RuntimeError("boom")

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", fake)
    lesson = run_critic("book a table", "booking", "Could not complete", _SETTINGS)
    assert lesson is None
