"""Small helpers for Streamlit's per-browser-session state."""

from __future__ import annotations

import streamlit as st


DEFAULTS = {
    "events": [],
    "current_task": None,
    "result": None,
    "is_running": False,
    "github_repo_url": "",
    "github_token": "",
    # Persists across queries in this browser session so repeated GitHub
    # tasks continue committing to the same branch/PR instead of each
    # starting a fresh one. Cleared on repo change or an explicit reset.
    "github_session_id": "",
    "github_session_repo": "",
    # Generated once per browser session and reused for every task request so
    # a completed Gmail OAuth connection (stored server-side, keyed by this
    # id) stays associated with this browser across queries.
    "gmail_session_id": "",
}


def initialize_state() -> None:
    for key, value in DEFAULTS.items():
        st.session_state.setdefault(key, value)


def reset_state() -> None:
    for key, value in DEFAULTS.items():
        st.session_state[key] = value.copy() if isinstance(value, list) else value
