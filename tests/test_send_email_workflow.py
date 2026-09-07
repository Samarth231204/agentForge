from types import SimpleNamespace

from backend.crew_engine import CrewEngine
from backend.event_emitter import EventEmitter
from backend.intent_parser import Intent


def _engine():
    engine = CrewEngine.__new__(CrewEngine)
    engine.settings = SimpleNamespace()
    return engine


def test_send_email_requires_a_connected_gmail_session():
    engine = _engine()
    intent = Intent(intent="send_email", confidence=1, reason="send", task_summary="send")
    result = engine.run(intent, "Send email to team@example.com saying hi", "", EventEmitter("task-1"), gmail_session_id="")
    assert "Connect a Gmail account" in result["content"]


def test_send_email_requires_a_recipient_address(monkeypatch):
    engine = _engine()
    intent = Intent(intent="send_email", confidence=1, reason="send", task_summary="send")
    result = engine.run(intent, "Send an email saying hi", "", EventEmitter("task-1"), gmail_session_id="session-1")
    assert "recipient" in result["content"].lower()


def test_send_email_sends_via_gmail_when_connected(monkeypatch):
    monkeypatch.setattr("backend.crew_engine.run_send_email_workflow", lambda *_a: ("Hello", "Body text"))
    monkeypatch.setattr("backend.crew_engine.send_email", lambda **kwargs: f"Email sent to {kwargs['to']} with subject '{kwargs['subject']}'.")

    engine = _engine()
    intent = Intent(intent="send_email", confidence=1, reason="send", task_summary="send")
    result = engine.run(intent, "Send email to team@example.com saying hi", "", EventEmitter("task-1"), gmail_session_id="session-1")

    assert result["intent"] == "send_email"
    assert "team@example.com" in result["content"]


def test_send_email_surfaces_gmail_unavailable_as_a_retryable_error(monkeypatch):
    from backend.tools.email_tool import GmailUnavailable

    monkeypatch.setattr("backend.crew_engine.run_send_email_workflow", lambda *_a: ("Hello", "Body text"))

    def fake_send(**_kwargs):
        raise GmailUnavailable("Gmail could not send the message: quota exceeded")

    monkeypatch.setattr("backend.crew_engine.send_email", fake_send)

    engine = _engine()
    intent = Intent(intent="send_email", confidence=1, reason="send", task_summary="send")
    result = engine.run(intent, "Send email to team@example.com saying hi", "", EventEmitter("task-1"), gmail_session_id="session-1")

    assert "quota exceeded" in result["content"]
