"""Gmail send tool: sends real email via the Gmail API using a session's
connected OAuth token. Never sends anything without a token present for that
session — that's the difference between this and Phase 1's always-draft-only
email workflow."""

from __future__ import annotations

import base64
from email.mime.text import MIMEText

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from backend.auth.google_oauth import credentials_from_dict, credentials_to_dict
from backend.auth.token_store import TokenStore


class GmailUnavailable(RuntimeError):
    """Raised when Gmail isn't connected for this session, or the send itself fails."""


NAME = "email"
DESCRIPTION = (
    "Send an email via the connected Gmail account. Requires a Gmail account already connected "
    "for this session — never available otherwise. Sends immediately and for real; there is no "
    "draft-only mode for this tool. Provide the recipient address, subject line, and full body text."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "to": {"type": "string", "description": "Recipient email address."},
        "subject": {"type": "string", "description": "Email subject line."},
        "body": {"type": "string", "description": "Full email body text."},
    },
    "required": ["to", "subject", "body"],
}


def send_email(*, session_id: str, to: str, subject: str, body: str, token_store: TokenStore) -> str:
    stored = token_store.get(session_id)
    if not stored:
        raise GmailUnavailable("Gmail is not connected for this session. Connect your Google account first.")

    creds = credentials_from_dict(stored)
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    try:
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
    except HttpError as exc:
        raise GmailUnavailable(f"Gmail could not send the message: {exc}") from exc
    finally:
        # The client library may have silently refreshed an expired access
        # token during the call above; persist it so the next send in this
        # session doesn't need to refresh again immediately.
        token_store.save(session_id, credentials_to_dict(creds))

    return f"Email sent to {to} with subject '{subject}'."
