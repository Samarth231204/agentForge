"""Phase 1 controls and capability explanation."""

from __future__ import annotations

import streamlit as st


def render_sidebar() -> tuple[str, str, str, bool]:
    with st.sidebar:
        st.header("AgentForge · Phase 2")
        backend_url = st.text_input("Backend URL", value="http://localhost:8000")
        st.success("Available: research, email drafts, and GitHub repository tasks")
        with st.expander("GitHub repository access", expanded=False):
            repo_url = st.text_input("Repository URL", key="github_repo_url", placeholder="https://github.com/owner/repository.git")
            github_token = st.text_input("Fine-grained GitHub token", type="password", key="github_token")
            st.caption("Used only for this browser session and sent only when you run a GitHub task. It is never written to disk or shown in the activity log.")
        st.info("Repository commands run only in Docker. Email sending, browser actions, and persistent memory remain later phases.")
        reset = st.button("Reset session")
    return backend_url, repo_url, github_token, reset
