"""Streamlit interface for AgentForge Phase 1."""

from __future__ import annotations

import uuid

import requests
import streamlit as st

from components.agent_graph import render_agent_graph
from components.chat_input import render_chat_input
from components.event_log import render_event_log
from components.sidebar import render_sidebar
from utils.sse_client import stream_task
from utils.state import initialize_state, reset_state

st.set_page_config(page_title="AgentForge", page_icon="⚒️", layout="wide")
initialize_state()
if not st.session_state.gmail_session_id:
    st.session_state.gmail_session_id = uuid.uuid4().hex

backend_url, repo_url, github_token, reset_requested, new_branch_requested = render_sidebar(st.session_state.gmail_session_id)
if reset_requested:
    reset_state()
    st.rerun()

st.title("⚒️ AgentForge")
st.write("A visible, multi-agent workspace for public-web research and email drafts.")
submitted, prompt, context = render_chat_input(st.session_state.is_running)

left, right = st.columns((1, 1))
graph_slot = left.empty()
log_slot = right.empty()
result_slot = st.empty()
connection_slot = st.empty()

if submitted:
    if not prompt.strip():
        st.warning("Enter a task before running AgentForge.")
    else:
        st.session_state.events = []
        st.session_state.result = None
        st.session_state.is_running = True
        # Start a fresh branch when asked, or when the repo changed since the
        # session id was created; otherwise keep committing to the same one.
        repo_url_stripped = repo_url.strip()
        if new_branch_requested or not st.session_state.github_session_id or st.session_state.github_session_repo != repo_url_stripped:
            st.session_state.github_session_id = uuid.uuid4().hex
            st.session_state.github_session_repo = repo_url_stripped
        connection_slot.info("Connecting to AgentForge…")
        try:
            for event in stream_task(backend_url, prompt.strip(), context.strip(), repo_url_stripped, github_token.strip(), st.session_state.github_session_id, st.session_state.gmail_session_id):
                connection_slot.empty()
                st.session_state.events.append(event)
                if event.get("type") == "result":
                    st.session_state.result = event.get("data")
                with graph_slot.container():
                    render_agent_graph(st.session_state.events)
                with log_slot.container():
                    render_event_log(st.session_state.events)
        except requests.RequestException as exc:
            st.error(f"Could not reach the backend at {backend_url}. Start it and try again. ({exc})")
        finally:
            st.session_state.is_running = False
            connection_slot.empty()

with graph_slot.container():
    render_agent_graph(st.session_state.events)
with log_slot.container():
    render_event_log(st.session_state.events)
with result_slot.container():
    if st.session_state.result:
        result = st.session_state.result
        st.subheader("Result")
        st.markdown(result.get("content", ""))
        if result.get("sources"):
            st.caption("Sources")
            for source in result["sources"]:
                st.markdown(f"- [{source}]({source})")
