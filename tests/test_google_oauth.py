from types import SimpleNamespace

from backend.auth.google_oauth import credentials_from_dict, credentials_to_dict, get_auth_url


def _settings():
    return SimpleNamespace(
        google_client_id="client-id",
        google_client_secret="client-secret",
        google_redirect_uri="http://localhost:8000/auth/google/callback",
    )


def test_get_auth_url_round_trips_state_and_points_at_google(monkeypatch):
    url = get_auth_url("session-123", _settings())
    assert url.startswith("https://accounts.google.com/")
    assert "state=session-123" in url
    assert "client_id=client-id" in url


def test_credentials_dict_round_trip_preserves_fields():
    creds = credentials_from_dict(
        {
            "token": "access-token",
            "refresh_token": "refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "scopes": ["https://www.googleapis.com/auth/gmail.send"],
        }
    )
    data = credentials_to_dict(creds)
    assert data["token"] == "access-token"
    assert data["refresh_token"] == "refresh-token"
    assert data["scopes"] == ["https://www.googleapis.com/auth/gmail.send"]
