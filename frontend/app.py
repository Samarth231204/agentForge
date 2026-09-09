"""Streamlit interface for AgentForge Phase 1."""

from __future__ import annotations

import uuid

import requests
import streamlit as st

from components.agent_graph import render_agent_graph
from components.chat_input import render_chat_input
from components.event_log import render_event_log
from components.plan_review import render_plan_review
from components.sidebar import render_sidebar
from utils.sse_client import propose_pipeline_plan, stream_task
from utils.state import initialize_state, reset_state

st.set_page_config(page_title="AgentForge", page_icon="⚒️", layout="wide")
initialize_state()
if not st.session_state.gmail_session_id:
    st.session_state.gmail_session_id = uuid.uuid4().hex
if not st.session_state.history_session_id:
    st.session_state.history_session_id = uuid.uuid4().hex

backend_url, repo_url, github_token, reset_requested, new_branch_requested = render_sidebar(st.session_state.gmail_session_id, st.session_state.history_session_id)
if reset_requested:
    reset_state()
    st.rerun()

st.title("⚒️ AgentForge")
st.write("A visible, multi-agent workspace for public-web research, email, GitHub, booking, and multi-step pipelines.")
submitted, prompt, context = render_chat_input(st.session_state.is_running or bool(st.session_state.pending_plan))

left, right = st.columns((1, 1))
graph_slot = left.empty()
log_slot = right.empty()
result_slot = st.empty()
connection_slot = st.empty()
plan_slot = st.empty()


def _run_task(run_prompt: str, run_context: str, approved_plan: dict | None) -> None:
    """The single execution path for both an ordinary request and an
    approved multi-step plan — same event stream, same rendering, nothing
    new for either case once this actually runs."""
    st.session_state.events = []
    st.session_state.result = None
    st.session_state.is_running = True
    repo_url_stripped = repo_url.strip()
    if new_branch_requested or not st.session_state.github_session_id or st.session_state.github_session_repo != repo_url_stripped:
        st.session_state.github_session_id = uuid.uuid4().hex
        st.session_state.github_session_repo = repo_url_stripped
    connection_slot.info("Connecting to AgentForge…")
    try:
        for event in stream_task(
            backend_url,
            run_prompt.strip(),
            run_context.strip(),
            repo_url_stripped,
            github_token.strip(),
            st.session_state.github_session_id,
            st.session_state.gmail_session_id,
            st.session_state.history_session_id,
            approved_plan=approved_plan,
        ):
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


if submitted and not st.session_state.pending_plan:
    if not prompt.strip():
        st.warning("Enter a task before running AgentForge.")
    else:
        # Phase 12: check whether this needs a multi-step plan before
        # running anything for real — a compound request only ever gets
        # proposed here, never executed, until the user reviews and
        # approves it below.
        try:
            proposal = propose_pipeline_plan(backend_url, prompt.strip(), context.strip(), repo_url.strip(), github_token.strip(), st.session_state.gmail_session_id)
        except requests.RequestException as exc:
            st.error(f"Could not reach the backend at {backend_url}. Start it and try again. ({exc})")
            proposal = None
        if proposal is not None:
            if proposal.get("compound"):
                if proposal.get("plan"):
                    st.session_state.pending_plan = proposal["plan"]
                    st.session_state.pending_plan_prompt = prompt.strip()
                    st.session_state.pending_plan_context = context.strip()
                    st.rerun()
                else:
                    st.error(
                        "This request needs multiple capabilities working together, but AgentForge could not "
                        "build a reliable plan for it right now. Try rephrasing, or ask for one part of it at a time."
                    )
            else:
                _run_task(prompt, context, approved_plan=None)

if st.session_state.pending_plan:
    with plan_slot.container():
        revise_clicked, revision_text, generate_clicked, discard_clicked = render_plan_review(st.session_state.pending_plan)
    if revise_clicked:
        try:
            proposal = propose_pipeline_plan(
                backend_url,
                st.session_state.pending_plan_prompt,
                st.session_state.pending_plan_context,
                repo_url.strip(),
                github_token.strip(),
                st.session_state.gmail_session_id,
                revision_instruction=revision_text,
                prior_plan=st.session_state.pending_plan,
            )
            if proposal.get("plan"):
                st.session_state.pending_plan = proposal["plan"]
            else:
                st.error("Could not revise the plan right now — the previous plan is still shown below. Try again or rephrase the requested change.")
        except requests.RequestException as exc:
            st.error(f"Could not reach the backend at {backend_url}. ({exc})")
        st.rerun()
    elif generate_clicked:
        plan_to_run = st.session_state.pending_plan
        run_prompt = st.session_state.pending_plan_prompt
        run_context = st.session_state.pending_plan_context
        st.session_state.pending_plan = None
        plan_slot.empty()
        _run_task(run_prompt, run_context, approved_plan=plan_to_run)
    elif discard_clicked:
        st.session_state.pending_plan = None
        st.rerun()

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
