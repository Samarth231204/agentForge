"""Phase 1 controls and capability explanation."""

from __future__ import annotations

from html import escape

import streamlit as st

from utils.sse_client import check_gmail_connected, check_task_history


def _badge(label: str, ok: bool | None) -> str:
    if ok is None:
        dot = "af-dot-pulse"
    elif ok:
        dot = "af-dot-ok"
    else:
        dot = "af-dot-off"
    return f'<span class="af-badge"><span class="af-dot {dot}"></span>{escape(label)}</span>'


def render_sidebar(gmail_session_id: str, history_session_id: str) -> tuple[str, str, str, bool, bool]:
    with st.sidebar:
        st.markdown(
            '<div style="display:flex;align-items:center;gap:8px;margin-bottom:2px;">'
            '<span style="font-size:1.3rem;">✦</span>'
            '<span style="font-weight:700;font-size:1.05rem;color:var(--text);">AgentForge</span>'
            '</div>'
            '<p style="color:var(--muted);font-size:0.78rem;margin-top:0;letter-spacing:0.04em;">PHASE 12 · MULTI-AGENT CONSOLE</p>',
            unsafe_allow_html=True,
        )
        backend_url = st.text_input("Backend URL", value="http://localhost:8000")
        gmail_connected = check_gmail_connected(backend_url, gmail_session_id)

        st.markdown(_badge("Research", True) + " " + _badge("Email", True), unsafe_allow_html=True)
        st.markdown(_badge("GitHub tasks", True) + " " + _badge("Browser automation", True), unsafe_allow_html=True)
        st.write("")

        with st.expander("GitHub repository access", expanded=False):
            repo_url = st.text_input("Repository URL", key="github_repo_url", placeholder="https://github.com/owner/repository.git")
            github_token = st.text_input("Fine-grained GitHub token", type="password", key="github_token")
            st.caption("Used only for this browser session and sent only when you run a GitHub task. It is never written to disk or shown in the activity log.")
            new_branch = st.checkbox("Start a new branch", value=False, help="By default, repeated GitHub requests keep committing to the same branch/PR. Check this before running to start a fresh branch instead.")

        with st.expander("Gmail account", expanded=False):
            if gmail_connected:
                st.markdown(_badge("Gmail connected", True), unsafe_allow_html=True)
                st.caption("AgentForge can send email on your behalf.")
            else:
                st.markdown(_badge("Gmail not connected", False), unsafe_allow_html=True)
                auth_url = f"{backend_url.rstrip('/')}/auth/google?state={gmail_session_id}"
                st.markdown(f"[Connect Gmail]({auth_url})")
                st.caption("Opens Google's consent screen in a new tab. Only send access is requested, and nothing is stored beyond this browser session.")

        with st.expander("Past sessions", expanded=False):
            tasks = check_task_history(backend_url, history_session_id)
            if not tasks:
                st.caption("No past tasks in this session yet.")
            else:
                for entry in reversed(tasks):
                    st.caption(f"{entry.get('timestamp', '')} · {entry.get('intent', '')}")
                    st.markdown(entry.get("prompt", ""))
                    st.divider()

        st.info("Repository commands run only in Docker. Research and email drafting/sending now recall relevant memory from past sessions; other workflows remain unaffected.")
        reset = st.button("Reset session")
    return backend_url, repo_url, github_token, reset, new_branch
