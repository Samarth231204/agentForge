"""Phase 1 controls and capability explanation."""

from __future__ import annotations

import streamlit as st

from utils.sse_client import check_gmail_connected


def render_sidebar(gmail_session_id: str) -> tuple[str, str, str, bool, bool]:
    with st.sidebar:
        st.header("AgentForge · Phase 3")
        backend_url = st.text_input("Backend URL", value="http://localhost:8000")
        gmail_connected = check_gmail_connected(backend_url, gmail_session_id)
        st.success("Available: research, email drafts/sending, GitHub repository tasks, and browser automation/bookings")
        with st.expander("GitHub repository access", expanded=False):
            repo_url = st.text_input("Repository URL", key="github_repo_url", placeholder="https://github.com/owner/repository.git")
            github_token = st.text_input("Fine-grained GitHub token", type="password", key="github_token")
            st.caption("Used only for this browser session and sent only when you run a GitHub task. It is never written to disk or shown in the activity log.")
            new_branch = st.checkbox("Start a new branch", value=False, help="By default, repeated GitHub requests keep committing to the same branch/PR. Check this before running to start a fresh branch instead.")
        with st.expander("Gmail account", expanded=False):
            if gmail_connected:
                st.success("Gmail connected — AgentForge can send email on your behalf.")
            else:
                auth_url = f"{backend_url.rstrip('/')}/auth/google?state={gmail_session_id}"
                st.markdown(f"[Connect Gmail]({auth_url})")
                st.caption("Opens Google's consent screen in a new tab. Only send access is requested, and nothing is stored beyond this browser session.")
        st.info("Repository commands run only in Docker. Browser actions and persistent memory remain later phases.")
        reset = st.button("Reset session")
    return backend_url, repo_url, github_token, reset, new_branch
