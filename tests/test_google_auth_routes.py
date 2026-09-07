from fastapi.testclient import TestClient


def _client(monkeypatch, *, client_id="client-id", client_secret="client-secret"):
    monkeypatch.setenv("GROQ_API_KEY_1", "test-key")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", client_id)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", client_secret)

    import backend.main as main
    from backend.config import get_settings

    get_settings.cache_clear()
    return TestClient(main.app), main


def test_auth_start_redirects_to_google_with_state(monkeypatch):
    client, _main = _client(monkeypatch)
    response = client.get("/auth/google", params={"state": "session-123"}, follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "accounts.google.com" in response.headers["location"]
    assert "state=session-123" in response.headers["location"]


def test_auth_start_is_unavailable_without_google_credentials(monkeypatch):
    client, _main = _client(monkeypatch, client_id="", client_secret="")
    response = client.get("/auth/google", params={"state": "session-123"})
    assert response.status_code == 503


def test_auth_status_reflects_token_store(monkeypatch):
    client, main = _client(monkeypatch)
    from backend.auth.token_store import get_token_store

    assert client.get("/auth/google/status", params={"state": "session-1"}).json() == {"connected": False}
    get_token_store().save("session-1", {"token": "x"})
    assert client.get("/auth/google/status", params={"state": "session-1"}).json() == {"connected": True}
    get_token_store().delete("session-1")


def test_auth_callback_saves_credentials_on_success(monkeypatch):
    client, main = _client(monkeypatch)
    from backend.auth.token_store import get_token_store

    monkeypatch.setattr(main, "exchange_code_for_tokens", lambda code, settings: {"token": "abc", "refresh_token": "r"})
    response = client.get("/auth/google/callback", params={"code": "auth-code", "state": "session-2"})
    assert response.status_code == 200
    assert "connected" in response.text.lower()
    assert get_token_store().has("session-2")
    get_token_store().delete("session-2")


def test_auth_callback_reports_failure_without_saving(monkeypatch):
    client, main = _client(monkeypatch)
    from backend.auth.token_store import get_token_store

    def fail(*_args):
        raise RuntimeError("invalid_grant")

    monkeypatch.setattr(main, "exchange_code_for_tokens", fail)
    response = client.get("/auth/google/callback", params={"code": "bad-code", "state": "session-3"})
    assert response.status_code == 400
    assert not get_token_store().has("session-3")


class _ExplodingTokenStore:
    def has(self, *_a, **_k):
        raise RuntimeError("Redis is down")

    def save(self, *_a, **_k):
        raise RuntimeError("Redis is down")


def test_auth_status_degrades_to_not_connected_when_the_store_is_unreachable(monkeypatch):
    client, main = _client(monkeypatch)
    monkeypatch.setattr(main, "get_token_store", lambda: _ExplodingTokenStore())

    response = client.get("/auth/google/status", params={"state": "session-4"})
    assert response.status_code == 200
    assert response.json() == {"connected": False}


def test_auth_callback_reports_a_clear_error_when_saving_fails(monkeypatch):
    client, main = _client(monkeypatch)
    monkeypatch.setattr(main, "exchange_code_for_tokens", lambda code, settings: {"token": "abc"})
    monkeypatch.setattr(main, "get_token_store", lambda: _ExplodingTokenStore())

    response = client.get("/auth/google/callback", params={"code": "auth-code", "state": "session-5"})
    assert response.status_code == 503
    assert "could not save" in response.text.lower()
