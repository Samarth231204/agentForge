from types import SimpleNamespace

from backend.templates.email_crew import DRAFT_NOTICE


def test_email_engine_returns_draft_only_notice(monkeypatch):
    from backend.crew_engine import CrewEngine
    from backend.event_emitter import EventEmitter
    from backend.intent_parser import Intent

    monkeypatch.setattr("backend.crew_engine.run_email_workflow", lambda *_args: "Subject: Launch update\n\nHello team")
    engine = CrewEngine.__new__(CrewEngine)
    engine.settings = SimpleNamespace()
    result = engine.run(Intent(intent="draft_email", confidence=1, reason="draft", task_summary="draft"), "Write an email", "", EventEmitter("task-1"))
    assert "Subject:" in result["content"]
    assert DRAFT_NOTICE in result["content"]


def test_email_workflow_chains_planner_writer_reviewer_sequentially(monkeypatch):
    from backend.templates import email_crew

    calls = []

    def fake_completion(*, model, api_key, temperature, messages):
        calls.append(messages)
        stage = len(calls)
        content = {1: "BRIEF-CONTENT", 2: "DRAFT-CONTENT", 3: "REVIEW-CONTENT"}[stage]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", fake_completion)
    settings = SimpleNamespace(groq_api_key="not-a-real-key", groq_model="openai/gpt-oss-120b", openrouter_api_key="", openrouter_model="")

    result = email_crew.run_email_workflow("Write an email", "", settings)

    assert len(calls) == 3
    assert "Write an email" in calls[0][1]["content"]  # planner sees the raw request
    assert "BRIEF-CONTENT" in calls[1][1]["content"]  # writer sees the planner's brief
    assert "BRIEF-CONTENT" in calls[2][1]["content"] and "DRAFT-CONTENT" in calls[2][1]["content"]  # reviewer sees both
    assert result == "REVIEW-CONTENT"
