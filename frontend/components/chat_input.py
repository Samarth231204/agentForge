"""Prompt form for a Phase 1 task."""

from __future__ import annotations

import streamlit as st


def render_chat_input(disabled: bool = False) -> tuple[bool, str, str]:
    st.markdown('<div class="af-card">', unsafe_allow_html=True)
    prompt = st.text_area("What would you like AgentForge to do?", placeholder="Research practical FastAPI SSE patterns for a small Python team.", height=100, disabled=disabled)
    context = st.text_area("Optional context", placeholder="Audience, tone, constraints, or the decision you are making.", height=70, disabled=disabled)
    submitted = st.button("Run task ➔", type="primary", disabled=disabled)
    st.caption("Try research (“Compare …”), a draft email, a GitHub request (“Add tests to this repository …”), or a booking (“Check table availability at …”). Email tasks remain draft-only; bookings never enter payment or login details.")
    st.markdown("</div>", unsafe_allow_html=True)
    return submitted, prompt, context
