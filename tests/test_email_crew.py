from types import SimpleNamespace

import pytest

pytest.importorskip("crewai", reason="requires the CrewAI runtime declared by Phase 1")

from backend.templates.email_crew import DRAFT_NOTICE


def test_email_engine_returns_draft_only_notice(monkeypatch):
    from backend.crew_engine import CrewEngine
    from backend.event_emitter import EventEmitter
    from backend.intent_parser import Intent

    class FakeCrew:
        def kickoff(self):
            return "Subject: Launch update\n\nHello team"

    monkeypatch.setattr("backend.crew_engine.create_email_crew", lambda *_args: FakeCrew())
    engine = CrewEngine.__new__(CrewEngine)
    engine.settings = SimpleNamespace()
    result = engine.run(Intent(intent="draft_email", confidence=1, reason="draft", task_summary="draft"), "Write an email", "", EventEmitter("task-1"))
    assert "Subject:" in result["content"]
    assert DRAFT_NOTICE in result["content"]


def test_email_crew_has_three_sequential_agents(monkeypatch):
    from backend.templates import email_crew

    captured = {}

    class FakeLLM:
        def __init__(self, **_kwargs):
            pass

    class FakeAgent:
        def __init__(self, **kwargs):
            self.role = kwargs["role"]

    class FakeTask:
        def __init__(self, **_kwargs):
            pass

    class FakeCrew:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(email_crew, "LLM", FakeLLM)
    monkeypatch.setattr(email_crew, "Agent", FakeAgent)
    monkeypatch.setattr(email_crew, "Task", FakeTask)
    monkeypatch.setattr(email_crew, "Crew", FakeCrew)
    email_crew.create_email_crew("Write an email", "", SimpleNamespace(groq_api_key="not-a-real-key", groq_model="openai/gpt-oss-20b"))
    assert [agent.role for agent in captured["agents"]] == ["Email Planner", "Email Writer", "Email Reviewer"]
