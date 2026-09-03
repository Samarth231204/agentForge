<div align="center">

# 🔥 AGENTFORGE 🔥
### *The AI Agent Platform That Does Everything While You Sit Back and Look Brilliant*

[![Built with Groq](https://img.shields.io/badge/Powered%20By-Groq%20LLaMA%203.1%2070B-orange?style=for-the-badge)](https://groq.com)
[![CrewAI](https://img.shields.io/badge/Agent%20Framework-CrewAI-blue?style=for-the-badge)](https://crewai.com)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-green?style=for-the-badge)](https://fastapi.tiangolo.com)
[![Streamlit](https://img.shields.io/badge/Frontend-Streamlit-red?style=for-the-badge)](https://streamlit.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](LICENSE)

> *"You type. Agents toil. Magic happens."*

---

**AgentForge** is not just another AI chatbot wrapper slapped together over a weekend.
It is a **full-stack, multi-phase, multi-agent autonomous execution platform** —
a living, breathing digital workforce that reads your GitHub repos, writes your code,
runs your tests, sends your emails, books your appointments, browses the web, and then
has the *audacity* to critique its own performance and get better at it.

All for free. All in real time. All while showing you every single agent thought, tool call,
and result in a gorgeous live-streaming interface.

</div>

---

## ✨ What On Earth Does This Thing Actually Do?

You type something like:

> *"Find the 5 most recent AI research papers, summarize each one, and email the summaries to my team."*

And AgentForge:

1. **Parses your intent** using a 70-billion-parameter LLaMA brain running at Groq's insane inference speed
2. **Assembles a crew** of specialized AI agents — Researcher, Summarizer, Email Drafter, Sender — each with its own role, backstory, and toolset
3. **Executes the whole thing end-to-end** — searches DuckDuckGo, synthesizes results, drafts emails, sends via Gmail OAuth2
4. **Shows you every single step** live, in real time, with every tool call and agent thought streamed to your browser

No hallucinated fake results. No "I can help with that!" followed by nothing. **Actual execution. Actual output.**

---

## 🏗️ The Architecture (A Love Story in Four Acts)

AgentForge is built across **four legendary phases**, each more powerful than the last.


### Phase 1 — The Core Engine *(The Foundation of Everything Glorious)*
The base. The origin. The beating heart. A **FastAPI backend** streams real-time Server-Sent Events (SSE) to a **Streamlit frontend**. An **Email Crew** drafts and dispatches communications with the eloquence of a Victorian correspondent. A **Search Flow** scours the web via DuckDuckGo — zero API key, zero cost, zero compromise. Only one secret required: your **Groq API key**.

### Phase 2 — GitHub Integration + The Docker Sandbox *(Where Code Meets Consequences)*
This is where things get *properly* dramatic.

AgentForge gains the power to **clone your entire GitHub repository**, dispatch a four-agent crew into it, and emerge with working code, passing tests, and an open pull request — all without touching your actual machine. The secret weapon? **A hermetically sealed Docker sandbox** — an isolated container dungeon where code runs in total captivity, cut off from your host filesystem and the open internet, with no escape route and no mercy.

The crew:
- 🔍 **Reader Agent** — Clones the repo, maps every file, understands the codebase like a new senior hire on day one
- 🛠️ **Coder Agent** — Makes precise, surgical changes. No unnecessary refactoring. No chaos. Pure intent.
- 🧪 **Tester Agent** — Runs `pytest`, scrutinizes output, and refuses to approve anything that doesn't pass
- 📬 **PR Manager Agent** — Pushes to a fresh branch, writes a professional pull request description, and leaves reviewers with nothing to complain about

Your GitHub Personal Access Token lives only in your browser session. It is never logged, never stored, never spoken of in polite company.

### Phase 3 — Browser Automation + Gmail OAuth2 + Supabase *(The Internet Is Now Your Servant)*
**Playwright headless Chromium** is unleashed. AgentForge can now navigate real websites, click real buttons, fill real forms, and complete real bookings — all without you lifting a finger or switching a tab.

Simultaneously, **Gmail OAuth2** gives agents the sacred ability to send email on your behalf — not via SMTP trickery or throwaway credentials, but through Google's own blessed API, with proper refresh tokens, stored securely in **Supabase** PostgreSQL so you never have to authorize twice.

The Booking Crew — Navigator, Form Filler, Confirmer — turns the web into a to-do list that completes itself.

### Phase 4 — Memory, Self-Improvement & The Critic *(The System That Gets Smarter Than You)*
The final form. The apotheosis.

**Mem0** gives every agent persistent memory across sessions. Agents remember your preferences. They remember what worked. They remember what didn't. They learn.

After every completed task, a **Critic Agent** convenes a one-agent post-mortem, reviews what went wrong, documents lessons learned, and deposits them into a shared memory store that future crews read before they begin. The system improves *itself* over time.

A **Streamlit performance dashboard** shows you agent metrics, task success rates, and memory usage — because if your AI workforce is going to operate autonomously, you deserve to know how well it's doing.

---

## 🧰 The Technology Stack (Every Tool, Justified)

| Layer | Technology | Why |
|---|---|---|
| **LLM** | Groq — LLaMA 3.1 70B | Fastest inference on earth. Free tier. No compromises. |
| **Agent Framework** | CrewAI | Both `Crew` (collaborative) and `Flow` (sequential) paradigms |
| **Backend** | FastAPI + Uvicorn | Async, fast, SSE-native, production-grade |
| **Frontend** | Streamlit | Python-native. Beautiful. Zero frontend fatigue. |
| **Web Search** | duckduckgo-search | No API key. No rate limit anxiety. No bill. |
| **Email** | Gmail API + OAuth2 | Real authentication. Real deliverability. |
| **Browser** | Playwright (Chromium) | Headless. Precise. Open source. |
| **Sandboxing** | Docker (isolated container) | Code runs in a prison. Your machine stays safe. |
| **Database** | Supabase (PostgreSQL) | Free tier. Real SQL. Row-level security. |
| **Memory** | Mem0 | Persistent agent memory across sessions |
| **Hosting** | Render + Streamlit Cloud | Both free tiers. Both production-capable. |

---

## 📁 Repository Structure

