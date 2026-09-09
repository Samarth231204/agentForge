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
    # Generated once per browser session (Phase 4), same shape as
    # gmail_session_id — used only as a lookup key for this session's task
    # history stored server-side (backend/history_store.py).
    "history_session_id": "",
    # Phase 12: a proposed multi-step PipelinePlan awaiting the user's
    # review (revise or approve) before anything actually runs. None means
    # there is nothing pending. pending_plan_prompt/context are the exact
    # request the plan was proposed for, kept alongside the plan so
    # "Generate" runs precisely what was reviewed, not whatever happens to
    # be in the prompt box at the moment the button is clicked.
    "pending_plan": None,
    "pending_plan_prompt": "",
    "pending_plan_context": "",
}


def initialize_state() -> None:
    for key, value in DEFAULTS.items():
        st.session_state.setdefault(key, value)


def reset_state() -> None:
    for key, value in DEFAULTS.items():
        st.session_state[key] = value.copy() if isinstance(value, list) else value
