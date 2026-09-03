"""Chronological task activity view."""

from __future__ import annotations

import streamlit as st


def render_event_log(events: list[dict]) -> None:
    st.subheader("Live activity")
    if not events:
        st.caption("Events will appear here while AgentForge works.")
        return
    for event in events:
        event_type = event.get("type", "event").replace("_", " ").title()
        data = event.get("data", {})
        summary = data.get("summary") or data.get("message") or data.get("reason") or data.get("input_summary") or ""
        st.caption(f"{event.get('sequence', '–')}. {event_type}" + (f" — {summary}" if summary else ""))
