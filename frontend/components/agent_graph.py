"""Readable workflow and agent-status panel without a graphing dependency."""

from __future__ import annotations

import streamlit as st


def render_agent_graph(events: list[dict]) -> None:
    st.subheader("Agent network")
    workflow = next((event["data"].get("workflow") for event in events if event["type"] == "workflow_started"), None)
    agents = next((event["data"].get("agents", []) for event in events if event["type"] == "workflow_started"), [])
    completed = {event["data"].get("agent") for event in events if event["type"] == "agent_completed"}
    active = next((event["data"].get("agent") for event in reversed(events) if event["type"] == "agent_started"), None)
    if workflow:
        st.caption(f"Workflow: `{workflow}`")
    if not agents:
        st.caption("Waiting for task classification…")
        return
    for agent in agents:
        icon = "✅" if agent in completed else "🔄" if agent == active else "○"
        st.write(f"{icon} {agent}")
