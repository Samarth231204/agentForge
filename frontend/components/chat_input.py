"""Prompt form for a Phase 1 task."""

from __future__ import annotations

import streamlit as st


def render_chat_input(disabled: bool = False) -> tuple[bool, str, str]:
    prompt = st.text_area("What would you like AgentForge to do?", placeholder="Research practical FastAPI SSE patterns for a small Python team.", height=100, disabled=disabled)
    context = st.text_area("Optional context", placeholder="Audience, tone, constraints, or the decision you are making.", height=70, disabled=disabled)
    submitted = st.button("Run task", type="primary", disabled=disabled)
    st.caption("Try research (“Compare …”), a draft email, or a GitHub request (“Add tests to this repository …”). Email tasks remain draft-only.")
    return submitted, prompt, context
