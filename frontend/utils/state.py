"""Small helpers for Streamlit's per-browser-session state."""

from __future__ import annotations

import streamlit as st


DEFAULTS = {"events": [], "current_task": None, "result": None, "is_running": False, "github_repo_url": "", "github_token": ""}


def initialize_state() -> None:
    for key, value in DEFAULTS.items():
        st.session_state.setdefault(key, value)


def reset_state() -> None:
    for key, value in DEFAULTS.items():
        st.session_state[key] = value.copy() if isinstance(value, list) else value
