from types import SimpleNamespace

from backend.crew_engine import CrewEngine
from backend.event_emitter import EventEmitter
from backend.intent_parser import Intent


class FakeMemoryManager:
    def __init__(self, personal=None, lessons=None):
        self.personal = personal or []
        self.lessons = lessons or []
        self.recall_calls: list[tuple[str, str]] = []

    def recall(self, user_id, query, *, limit=3):
        self.recall_calls.append((user_id, query))
        return self.lessons if user_id == "lessons:global" else self.personal

    def remember(self, user_id, content, *, metadata=None):
        pass


def _engine():
    engine = CrewEngine.__new__(CrewEngine)
    engine.settings = SimpleNamespace()
    return engine


def test_email_workflow_receives_recalled_memory_and_lessons_in_context(monkeypatch):
    captured = {}

    def fake_run_email_workflow(prompt, context, settings):
        captured["context"] = context
        return "Subject: Hi\n\nBody"

    monkeypatch.setattr("backend.crew_engine.run_email_workflow", fake_run_email_workflow)
    monkeypatch.setattr("backend.crew_engine.get_memory_manager", lambda: FakeMemoryManager(personal=["Prefers formal tone."], lessons=["Always confirm the recipient."]))

    engine = _engine()
    engine.run(Intent(intent="draft_email", confidence=1, reason="draft", task_summary="draft"), "Write an email", "Existing context", EventEmitter("task-1"), session_id="session-abc")

    assert "Existing context" in captured["context"]
    assert "Prefers formal tone." in captured["context"]
    assert "Always confirm the recipient." in captured["context"]


def test_research_workflow_receives_recalled_memory_in_context(monkeypatch):
    captured = {}

    def fake_search(_query):
        return [{"title": "t", "url": "https://example.com", "snippet": "s"}]

    def fake_run_search_synthesis(prompt, context, sources, settings):
        captured["context"] = context
        return "Answer"

    monkeypatch.setattr("backend.crew_engine.WebSearchTool.search", staticmethod(fake_search))
    monkeypatch.setattr("backend.crew_engine.run_search_synthesis", fake_run_search_synthesis)
    monkeypatch.setattr("backend.crew_engine.get_memory_manager", lambda: FakeMemoryManager(personal=["User works at Acme Corp."]))

    engine = _engine()
    engine.run(Intent(intent="research", confidence=1, reason="research", task_summary="research"), "Find something", "", EventEmitter("task-2"), gmail_session_id="gmail-session-1")

    assert "User works at Acme Corp." in captured["context"]


def test_memory_recall_is_keyed_by_gmail_session_id_when_present(monkeypatch):
    manager = FakeMemoryManager()
    monkeypatch.setattr("backend.crew_engine.run_email_workflow", lambda prompt, context, settings: "Subject: Hi\n\nBody")
    monkeypatch.setattr("backend.crew_engine.get_memory_manager", lambda: manager)

    engine = _engine()
    engine.run(
        Intent(intent="draft_email", confidence=1, reason="draft", task_summary="draft"),
        "Write an email",
        "",
        EventEmitter("task-3"),
        session_id="github-session",
        gmail_session_id="gmail-session",
    )

    assert manager.recall_calls[0] == ("prefs:gmail-session", "Write an email")


def test_no_memory_recall_leaves_context_unchanged(monkeypatch):
    captured = {}

    def fake_run_email_workflow(prompt, context, settings):
        captured["context"] = context
        return "Subject: Hi\n\nBody"

    monkeypatch.setattr("backend.crew_engine.run_email_workflow", fake_run_email_workflow)
    monkeypatch.setattr("backend.crew_engine.get_memory_manager", lambda: FakeMemoryManager())

    engine = _engine()
    engine.run(Intent(intent="draft_email", confidence=1, reason="draft", task_summary="draft"), "Write an email", "Original context", EventEmitter("task-4"))

    assert captured["context"] == "Original context"
