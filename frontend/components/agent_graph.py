"""Readable workflow and agent-status panel without a graphing dependency."""

from __future__ import annotations

from html import escape

import streamlit as st


def render_agent_graph(events: list[dict]) -> None:
    workflow = next((event["data"].get("workflow") for event in events if event["type"] == "workflow_started"), None)
    agents = next((event["data"].get("agents", []) for event in events if event["type"] == "workflow_started"), [])
    completed = {event["data"].get("agent") for event in events if event["type"] == "agent_completed"}
    active = next((event["data"].get("agent") for event in reversed(events) if event["type"] == "agent_started"), None)

    html = ['<div class="af-card">', '<div class="af-card-title">Agent network</div>']
    if workflow:
        html.append(f'<p style="color:var(--muted);font-family:\'JetBrains Mono\',monospace;font-size:0.82rem;margin-top:-4px;">workflow: {escape(str(workflow))}</p>')
    if not agents:
        html.append('<p style="color:var(--muted);">Waiting for task classification…</p>')
    else:
        for agent in agents:
            if agent in completed:
                cls, icon = "af-node-done", "✓"
            elif agent == active:
                cls, icon = "af-node-active", '<span class="af-dot af-dot-pulse"></span>'
            else:
                cls, icon = "af-node-pending", "○"
            html.append(f'<div class="af-node {cls}"><span>{icon}</span><span>{escape(str(agent))}</span></div>')
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)
