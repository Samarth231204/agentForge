from types import SimpleNamespace

import pytest

from backend.auth.token_store import TokenStore
from backend.tools.email_tool import GmailUnavailable, send_email


def _fake_credentials():
    return {
        "token": "access-token",
        "refresh_token": "refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "scopes": ["https://www.googleapis.com/auth/gmail.send"],
    }


def test_raises_when_session_has_no_connected_gmail_account():
    store = TokenStore()
    with pytest.raises(GmailUnavailable, match="not connected"):
        send_email(session_id="missing", to="a@example.com", subject="Hi", body="Body", token_store=store)


def test_sends_via_gmail_api_and_persists_refreshed_credentials(monkeypatch):
    store = TokenStore()
    store.save("session-1", _fake_credentials())

    sent = {}

    class FakeMessages:
        def send(self, userId, body):
            sent["userId"] = userId
            sent["raw"] = body["raw"]
            return SimpleNamespace(execute=lambda: {"id": "msg-1"})

    class FakeUsers:
        def messages(self):
            return FakeMessages()

    class FakeService:
        def users(self):
            return FakeUsers()

    monkeypatch.setattr("backend.tools.email_tool.build", lambda *a, **k: FakeService())

    result = send_email(session_id="session-1", to="team@example.com", subject="Launch", body="Hello", token_store=store)

    assert "team@example.com" in result
    assert "Launch" in result
    assert sent["userId"] == "me"
    # Credentials are re-saved after send (covers silent token refresh).
    assert store.has("session-1")


def test_wraps_gmail_api_errors_as_gmail_unavailable(monkeypatch):
    from googleapiclient.errors import HttpError

    store = TokenStore()
    store.save("session-1", _fake_credentials())

    class FakeMessages:
        def send(self, userId, body):
            response = SimpleNamespace(status=403, reason="Forbidden")
            raise HttpError(response, b"{}")

    class FakeUsers:
        def messages(self):
            return FakeMessages()

    class FakeService:
        def users(self):
            return FakeUsers()

    monkeypatch.setattr("backend.tools.email_tool.build", lambda *a, **k: FakeService())

    with pytest.raises(GmailUnavailable):
        send_email(session_id="session-1", to="team@example.com", subject="Launch", body="Hello", token_store=store)
