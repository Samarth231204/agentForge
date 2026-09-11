"""Chronological task activity view."""

from __future__ import annotations

from html import escape

import streamlit as st


def render_event_log(events: list[dict]) -> None:
    html = ['<div class="af-card">', '<div class="af-card-title">Live activity</div>']
    if not events:
        html.append('<p style="color:var(--muted);">Events will appear here while AgentForge works.</p>')
    else:
        html.append('<div class="af-timeline">')
        for event in events:
            event_type = escape(event.get("type", "event").replace("_", " ").title())
            data = event.get("data", {})
            summary = data.get("summary") or data.get("message") or data.get("reason") or data.get("input_summary") or ""
            seq = escape(str(event.get("sequence", "–")))
            html.append(
                f'<div class="af-event"><span class="af-event-seq">{seq}</span>'
                f'<span class="af-event-type">{event_type}</span>'
                + (f'<div class="af-event-summary">{escape(str(summary))}</div>' if summary else "")
                + "</div>"
            )
        html.append("</div>")
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)
