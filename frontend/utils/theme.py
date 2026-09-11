"""Shared visual theme: an indigo-on-black "LLM console" look injected once
per page load. Streamlit's own theme (frontend/.streamlit/config.toml) sets
the base palette; this layers in the fonts, glow, cards, and badges that
config.toml alone can't express."""

from __future__ import annotations

import streamlit as st

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

:root {
    --bg-0: #05070C;
    --bg-1: #0A0E18;
    --bg-2: #0D1220;
    --line: rgba(79, 124, 255, 0.16);
    --accent: #4F7CFF;
    --accent-bright: #7AA2FF;
    --accent-glow: rgba(79, 124, 255, 0.35);
    --ok: #34D399;
    --muted: #7C8AA8;
    --text: #E7ECFB;
}

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}

[data-testid="stAppViewContainer"] {
    background:
        radial-gradient(1100px 500px at 12% -8%, rgba(79,124,255,0.14), transparent 60%),
        radial-gradient(900px 500px at 100% 0%, rgba(122,162,255,0.08), transparent 55%),
        var(--bg-0);
}

[data-testid="stHeader"] { background: transparent; }

[data-testid="stSidebar"] {
    background: linear-gradient(180deg, var(--bg-1), var(--bg-0));
    border-right: 1px solid var(--line);
}
[data-testid="stSidebar"] h2 { color: var(--text); }

/* ---------- hero header ---------- */
.af-hero {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 6px 0 2px 0;
}
.af-hero .af-mark {
    width: 42px; height: 42px;
    border-radius: 11px;
    display: flex; align-items: center; justify-content: center;
    background: linear-gradient(135deg, var(--accent), #8B5CF6);
    box-shadow: 0 0 24px var(--accent-glow);
    font-size: 20px;
}
.af-hero h1 {
    font-size: 1.9rem;
    font-weight: 700;
    margin: 0;
    background: linear-gradient(90deg, #EAF0FF 10%, var(--accent-bright) 60%, #8B5CF6 100%);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
    letter-spacing: -0.02em;
}
.af-hero .af-sub {
    margin: 2px 0 0 0;
    color: var(--muted);
    font-size: 0.92rem;
}

/* ---------- generic cards ---------- */
.af-card {
    background: rgba(255,255,255,0.025);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 18px 20px;
    box-shadow: 0 8px 30px rgba(0,0,0,0.25);
    margin-bottom: 14px;
}
.af-card h3, .af-card .af-card-title {
    margin-top: 0;
    font-size: 0.82rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--accent-bright);
    font-weight: 600;
}

/* ---------- inputs & buttons ---------- */
textarea, input[type="text"], input[type="password"] {
    background: var(--bg-2) !important;
    border: 1px solid var(--line) !important;
    border-radius: 10px !important;
    color: var(--text) !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.9rem !important;
}
textarea:focus, input[type="text"]:focus, input[type="password"]:focus {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 3px var(--accent-glow) !important;
}

div.stButton > button {
    background: linear-gradient(135deg, var(--accent), #6D5CFB);
    color: white;
    border: none;
    border-radius: 10px;
    font-weight: 600;
    padding: 0.5rem 1.1rem;
    transition: box-shadow 0.15s ease, transform 0.15s ease;
}
div.stButton > button:hover {
    box-shadow: 0 0 18px var(--accent-glow);
    transform: translateY(-1px);
}
div.stButton > button:disabled {
    background: var(--bg-2);
    color: var(--muted);
    box-shadow: none;
}

/* ---------- status badges ---------- */
.af-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
    border: 1px solid var(--line);
    background: rgba(255,255,255,0.03);
}
.af-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.af-dot-ok { background: var(--ok); box-shadow: 0 0 8px var(--ok); }
.af-dot-off { background: var(--muted); }
.af-dot-pulse { background: var(--accent-bright); box-shadow: 0 0 10px var(--accent); animation: af-pulse 1.4s ease-in-out infinite; }

@keyframes af-pulse {
    0%   { transform: scale(0.85); opacity: 0.7; }
    50%  { transform: scale(1.25); opacity: 1; }
    100% { transform: scale(0.85); opacity: 0.7; }
}

/* ---------- agent graph nodes ---------- */
.af-node {
    display: flex; align-items: center; gap: 10px;
    padding: 9px 12px;
    margin-bottom: 6px;
    border-radius: 10px;
    border: 1px solid var(--line);
    background: rgba(255,255,255,0.02);
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.85rem;
}
.af-node-active { border-color: var(--accent); box-shadow: 0 0 14px var(--accent-glow); }
.af-node-done { color: var(--ok); border-color: rgba(52,211,153,0.3); }
.af-node-pending { color: var(--muted); }

/* ---------- event timeline ---------- */
.af-timeline { border-left: 2px solid var(--line); padding-left: 16px; margin-left: 4px; }
.af-event {
    position: relative;
    padding: 2px 0 12px 0;
    font-size: 0.85rem;
}
.af-event::before {
    content: '';
    position: absolute;
    left: -20.5px; top: 6px;
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--accent-bright);
    box-shadow: 0 0 6px var(--accent);
}
.af-event .af-event-type {
    font-family: 'JetBrains Mono', monospace;
    color: var(--accent-bright);
    font-weight: 600;
}
.af-event .af-event-seq { color: var(--muted); margin-right: 6px; }
.af-event .af-event-summary { color: var(--text); opacity: 0.85; }

hr, [data-testid="stDivider"] { border-color: var(--line) !important; }
</style>
"""


def inject() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def hero(title: str, subtitle: str, mark: str = "✦") -> None:
    st.markdown(
        f"""
        <div class="af-hero">
            <div class="af-mark">{mark}</div>
            <div>
                <h1>{title}</h1>
                <p class="af-sub">{subtitle}</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
