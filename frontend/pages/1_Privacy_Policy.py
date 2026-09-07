"""Static privacy policy page, required by Google's OAuth verification /
production-publishing requirements for the Gmail integration (gmail.send
scope). Streamlit auto-routes any file under frontend/pages/ to its own URL
on the same domain as the main app, e.g.
https://agentforge231204.streamlit.app/Privacy_Policy — no separate hosting
or authorized domain needed."""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Privacy Policy — AgentForge", page_icon="⚒️")

st.title("Privacy Policy")
st.caption("Last updated: 2026-09-07")

st.markdown(
    """
AgentForge is a multi-agent workspace for public-web research, email
sending, and GitHub repository tasks. This page explains what AgentForge
does with your data when you connect a Google account or a GitHub account.

### Gmail integration

When you click **Connect Gmail**, Google shows you a consent screen asking
you to authorize AgentForge to send email on your behalf. AgentForge
requests exactly one Gmail permission:

- `gmail.send` — send email as you.

AgentForge never requests permission to read, search, or modify your
mailbox, and never requests any other Google data (contacts, files,
profile, etc.).

**What is stored:** After you approve, Google issues AgentForge an OAuth
access token and refresh token for your account. AgentForge stores only
this token pair — not your email address, name, or any other profile
information — associated with your current browser session. It is used
solely to call Gmail's send API on requests you make within that session.

**How it is stored and erased:** Tokens are held in a server-side cache
with an automatic expiration window. An idle session's token is erased
automatically after the expiration window elapses, with no manual action
required. Actively used sessions have that window renewed on each use, so
they do not expire mid-session. You can also disconnect at any time from
the AgentForge sidebar, which erases the stored token immediately.

**What is never stored:** The content of emails you send, your Gmail
address, or the contents of your mailbox. AgentForge does not read your
inbox — the `gmail.send` scope does not allow it to.

### GitHub integration

If you provide a GitHub personal access token and repository URL to use
AgentForge's repository-editing workflow, that token is used only for the
duration of the task you run and is not written to disk or persisted
beyond your current session.

### Contact

Questions about this policy or how AgentForge handles your data can be
sent to samarthgoss@gmail.com.
"""
)
