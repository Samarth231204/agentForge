"""Phase 12: shows a proposed multi-step pipeline plan for review before
anything runs — the frontend half of the review gate main.py enforces
server-side (a compound request only ever gets proposed, never executed,
until the user approves this exact plan)."""

from __future__ import annotations

import streamlit as st


def render_plan_review(plan: dict) -> tuple[bool, str, bool, bool]:
    """Returns (revise_clicked, revision_text, generate_clicked, discard_clicked)."""
    st.subheader("Proposed plan")
    st.write(plan.get("summary", ""))

    for step in plan.get("steps", []):
        with st.expander(f"**{step['name']}** — {step['blueprint']}", expanded=True):
            st.caption(f"Intent: {step['intent']}")
            if step.get("tools"):
                st.caption(f"Tools: {', '.join(step['tools'])}")
            if step.get("depends_on"):
                st.caption(f"Runs after: {', '.join(step['depends_on'])}")
            if step.get("blueprint") == "parallel_fanout":
                st.caption(f"Runs {step.get('fanout_count', 1)} of these concurrently")
            st.write(step.get("instructions", ""))

    st.caption("Nothing has run yet — no email has been sent, no browser action taken, no code pushed. Review the plan above, then ask for changes or approve it to actually run.")

    revision_text = st.text_input("Ask for a change to this plan (optional)", key="plan_revision_text", placeholder="e.g. only check 3 companies instead of 5")
    col_revise, col_generate, col_discard = st.columns(3)
    revise_clicked = col_revise.button("Revise plan", disabled=not revision_text.strip())
    generate_clicked = col_generate.button("Generate", type="primary")
    discard_clicked = col_discard.button("Discard")

    return revise_clicked, revision_text, generate_clicked, discard_clicked
