"""Gmail OAuth2 flow: connect once per browser session, refresh silently after.

Any Google account that completes this flow gets its own token — nothing
here is scoped to a single developer or user. Whether an arbitrary Google
account can actually reach the consent screen at all depends on the OAuth
consent screen's publish status in Google Cloud Console (Testing = only
pre-added test users; Production + verified = anyone), which is a Google
Cloud Console setting, not anything controlled by this code.
"""

from __future__ import annotations

from typing import Any

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from backend.config import Settings

# Least-privilege: send-only. Never request read/modify access to a
# connected user's mailbox for a feature that only ever sends on their behalf.
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def _client_config(settings: Settings) -> dict[str, Any]:
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uris": [settings.google_redirect_uri],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def get_auth_url(state: str, settings: Settings) -> str:
    """`state` is the caller's session id — Google round-trips it back to
    the callback unchanged, which is how the resulting token gets
    associated with the right browser session without any other state."""
    # PKCE is off: it needs the same code_verifier at both authorization-url
    # time and token-exchange time, but those happen in two separate Flow
    # instances (across two separate requests) with nothing wiring a
    # verifier between them. Not needed anyway — this is a confidential
    # client authenticating with a client_secret, which is what PKCE exists
    # to substitute for on clients that can't hold a secret.
    flow = Flow.from_client_config(_client_config(settings), scopes=SCOPES, autogenerate_code_verifier=False)
    flow.redirect_uri = settings.google_redirect_uri
    auth_url, _ = flow.authorization_url(
        access_type="offline",  # required to receive a refresh_token at all
        include_granted_scopes="true",
        state=state,
        prompt="consent",  # forces a refresh_token even on a repeat authorization
    )
    return auth_url


def exchange_code_for_tokens(code: str, settings: Settings) -> dict[str, Any]:
    flow = Flow.from_client_config(_client_config(settings), scopes=SCOPES, autogenerate_code_verifier=False)
    flow.redirect_uri = settings.google_redirect_uri
    flow.fetch_token(code=code)
    return credentials_to_dict(flow.credentials)


def credentials_to_dict(creds: Credentials) -> dict[str, Any]:
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or SCOPES),
    }


def credentials_from_dict(data: dict[str, Any]) -> Credentials:
    return Credentials(
        token=data["token"],
        refresh_token=data.get("refresh_token"),
        token_uri=data["token_uri"],
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        scopes=data.get("scopes", SCOPES),
    )
