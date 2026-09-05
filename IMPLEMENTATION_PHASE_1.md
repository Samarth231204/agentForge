# Phase 1 — Research + Email Drafting

## Pipeline

```
Streamlit UI (prompt, optional context)
        │
        ▼
POST /tasks  { prompt, context }
        │
        ▼
IntentParser (keyword fast-path, else one Groq call)
   → intent ∈ { research, draft_email, unsupported }
        │
        ├── research ─────────────────────────────────────┐
        │   WebSearchTool.search() → DDGS() public web     │
        │   search, normalized + deduped, ≤5 results        │
        │        │                                          │
        │        ▼                                          │
        │   create_search_crew() – 1 CrewAI agent           │
        │   (Research Synthesizer) answers using ONLY       │
        │   the supplied snippets                           │
        │                                                    │
        ├── draft_email ──────────────────────────────────┐ │
        │   create_email_crew() – 3 sequential CrewAI      │ │
        │   agents: Planner → Writer → Reviewer            │ │
        │   Reviewer's output must end with the             │ │
        │   draft-only notice                               │ │
        │                                                    │
        └── unsupported ──────────────────────────────────┘ │
            No LLM workflow — returns a plain explanation     │
            (used for GitHub/browser/booking-shaped requests) │
        │                                                      │
        ▼                                                      ▼
Events emitted throughout (task_started, intent_detected, workflow_started,
agent_started/completed, tool_started/completed, result, task_completed)
        │
        ▼
Streamlit renders the agent-network panel, live activity log, and result
```

## Summary

Phase 1 is the minimal usable AgentForge loop: a user types a natural-language request, Groq classifies it into one of three intents, and a small CrewAI workflow runs and streams its progress back. There is no persistence anywhere — no database, no saved sessions, no files written to disk — every task lives entirely in an in-memory, per-request event queue (`EventEmitter`) for the duration of that one HTTP call.

Classification is cheap by design: `IntentParser._forced_intent()` checks for obvious keywords first (`"send email"`, `"github"`, `"browser"`, etc.) and only falls back to an actual Groq call when nothing matches, keeping most requests to zero or one LLM call before routing.

Research and email drafting are both implemented as CrewAI `Crew` objects built fresh per request (`create_search_crew`, `create_email_crew`), each backed by a Groq model via `crewai.LLM(model=f"groq/{settings.groq_model}", ...)`. Research is retrieval-then-synthesis: `WebSearchTool` (backed by `ddgs`) fetches and normalizes real search results *before* any LLM involvement, and the single Research Synthesizer agent is instructed to answer using only those snippets — it never has open-ended access to the web itself. Email drafting is three sequential agents (Planner → Writer → Reviewer) chained via CrewAI's `Task(context=[...])`, ending in a hard requirement that the output contains the literal notice "Draft only — AgentForge did not send or schedule this email." (`crew_engine.py` appends it defensively if the model ever omits it).

The backend exposes both a true `text/event-stream` endpoint (`/tasks/stream`) and a buffered JSON endpoint (`/tasks`) that runs the task to completion and returns its full ordered event list at once; the Streamlit frontend uses the JSON endpoint (see bug #5 below) while `/tasks/stream` remains available for API clients that can hold a streaming connection open. Either way, every event follows the same envelope (`task_id`, `type`, `timestamp`, monotonic `sequence`, `data`), and the stream always ends with a `task_completed` event even on failure.

## Full walkthrough — every file, in order

### 1. Frontend — the browser session (`frontend/`)

- **`utils/state.py`** — `initialize_state()` seeds `st.session_state` with `events`, `current_task`, `result`, `is_running`, plus the (Phase 2-added) GitHub fields.
- **`components/sidebar.py`** — Backend URL field and a Phase capability summary; in Phase 1 alone this carried no credential inputs at all.
- **`components/chat_input.py`** (`render_chat_input`) — one `st.text_area` for the prompt, one for optional context, and a "Run task" button; returns `(submitted, prompt, context)`.
- **`app.py`** — on submit: resets `events`/`result`, sets `is_running = True`, then iterates `stream_task(...)`, appending each event to `st.session_state.events` and re-rendering `agent_graph` + `event_log` after every single event (so the UI updates live even though the transport is a single buffered JSON response, not real streaming, from the browser's point of view — Streamlit reruns render calls per loop iteration). A `result`-type event's `data` is stashed separately and rendered as the final "Result" panel with clickable source links.
- **`utils/sse_client.py`** (`stream_task`) — posts `{prompt, context}` (plus the Phase 2 GitHub fields, defaulted empty) to `{backend_url}/tasks` and yields each object from the returned `events` list. `_parse_sse_lines` also exists to parse true `data:`-prefixed SSE lines for the `/tasks/stream` endpoint, tolerating keep-alive comments and malformed individual messages.
- **`components/agent_graph.py`** — reads the event list for the most recent `workflow_started` (to get the workflow name and agent list) and `agent_completed`/`agent_started` events, rendering ✅/🔄/○ per agent — pure derived UI state, no separate tracking.
- **`components/event_log.py`** — renders every event as one line: sequence number, humanized type, and whichever of `summary`/`message`/`reason`/`input_summary` is present in its `data`.

### 2. Backend entry — FastAPI (`backend/main.py`)

- `TaskRequest` validates `prompt` (1–4000 chars, required) and `context` (optional, ≤4000 chars). (`repo_url`/`github_token`/`session_id` were added later, in Phase 2.)
- `GET /health` — trivial readiness probe (`{"status": "ok"}`), used by the local runbook before opening the UI.
- `POST /tasks/stream` — creates an `EventEmitter(uuid.uuid4())`, emits `task_started` synchronously (so the SSE client sees something immediately), spawns `_run_task(...)` on a daemon `Thread`, and streams the emitter's queue back as `text/event-stream`, sending a `: keep-alive\n\n` comment every 15 seconds of inactivity so intermediate proxies don't time out the connection.
- `POST /tasks` — the endpoint actually used by Streamlit: runs `_run_task(...)` synchronously on the request thread (no background thread needed since nothing is streamed live to the caller), drains the whole queue, and returns `{"events": [...]}` as one JSON response.
- `_run_task()` — the shared core for both endpoints: classify intent → emit `intent_detected` → `CrewEngine().run(...)` → emit `result` → emit `task_completed`. Any exception is caught, logged server-side with the task id (never with the prompt's or provider's raw internals exposed to the client), and translated into either an `llm_rate_limited` or a generic `task_failed` error event before `task_completed` and `emitter.close()` fire regardless.
- `lifespan()` calls `get_settings()` once at startup — the app refuses to boot at all if `GROQ_API_KEY` is missing, rather than failing confusingly on the first request.

### 3. Intent classification (`backend/intent_parser.py`)

- `Intent` (Pydantic model): `intent` (one of `research | draft_email | github | unsupported`), `confidence`, `reason`, `task_summary`, `constraints`.
- `IntentParser.parse()`:
  1. `_forced_intent()` first — keyword checks for GitHub/repo language → `github`; browser/booking/login/payment language → `unsupported`; explicit send/schedule/write/draft email language → `draft_email`. Any match returns immediately, **no Groq call**.
  2. Otherwise calls Groq (`chat.completions.create` with `response_format={"type": "json_object"}` and `temperature=0`) with a system prompt listing the four valid intents and their meanings, validates the JSON response against `Intent`, and retries once with a corrective "your previous answer was invalid" follow-up if parsing/validation fails.
  3. If both attempts fail, falls back to `unsupported` with confidence `0.0` and an honest "I could not reliably classify this request" reason, rather than crashing the request.

### 4. Dispatch (`backend/crew_engine.py`)

- `CrewEngine.run()` switches on `intent.intent`:
  - `unsupported` → returns immediately with `intent.reason` plus a note about what Phase 1 can actually do. No LLM call, no event emission beyond what `main.py` already sent.
  - `draft_email` → `_run_email(...)`.
  - `research` (the `else` branch, i.e. anything not explicitly one of the other three) → `_run_research(...)`.
  - `github` → `_run_github(...)` (Phase 2; see `IMPLEMENTATION_PHASE_2.md`).
- `_run_email()`: emits `workflow_started` (`email_crew`, agents `Email Planner/Writer/Reviewer`) and one `agent_started` per stage, calls `create_email_crew(prompt, context, settings).kickoff()`, defensively appends `DRAFT_NOTICE` if the model's output somehow omitted it, emits `agent_completed` per stage, and returns `{"intent": "draft_email", "content": output, "sources": []}`.
- `_run_research()`: emits `workflow_started` (`search_flow`, agents `Researcher/Research Synthesizer`) and `agent_started` for the Researcher, then `tool_started`/`tool_completed` around `WebSearchTool.search(prompt)`. A `SearchUnavailable` exception is caught here specifically and turned into a `search_unavailable` error event with a plain-language message, rather than propagating up as a generic failure. An empty result list short-circuits with an honest "could not find reliable results" message — no synthesis call is made with zero sources. Otherwise emits `agent_started` for the Synthesizer, calls `create_search_crew(prompt, context, sources, settings).kickoff()`, emits `agent_completed`, and returns `{"intent": "research", "content": output, "sources": [urls...]}`.
- `_is_rate_limited()` / `_retry_delay()` — shared helpers (also used by the Phase 2 GitHub path) that recognize a provider rate-limit error by class name or by substrings like `"rate_limit_exceeded"`, `"429"`, `"503"` in the error text, and compute a safe retry delay by parsing "try again in Ns" out of the error message when present.

### 5. Research workflow (`backend/tools/web_search_tool.py`, `backend/templates/search_flow.py`)

- `WebSearchTool.search(query, max_results=5)` (a `@staticmethod`, callable both as a CrewAI tool and directly from `crew_engine.py`): rejects a blank query, clamps `max_results` to 1–5, calls `DDGS().text(...)`, and normalizes each raw result to `{title, url, snippet}` via `_normalize` — dropping anything without a proper `http(s)://` URL, deduplicating by URL, collapsing whitespace in title/snippet. Any exception from the provider is caught and re-raised as `SearchUnavailable`, a controlled type the engine knows how to translate into a safe UI message rather than an opaque stack trace.
- `create_search_crew()`: one CrewAI `Agent` ("Research Synthesizer") and one `Task` whose description embeds the prompt, context, and every source's title/URL/snippet verbatim, with an explicit instruction not to invent citations or facts and to clearly flag any inference. Calls `disable_unsupported_cache_breakpoints()` first (see bug #3 below) before constructing the `LLM`.

### 6. Email drafting workflow (`backend/templates/email_crew.py`)

- Three `Agent`s (Planner, Writer, Reviewer) and three `Task`s run sequentially (`Process.sequential`): the Planner's brief feeds the Writer via `Task(context=[brief])`, and the Reviewer sees both prior tasks' outputs (`context=[brief, draft]`). The Reviewer's task description explicitly mandates the output format (`Subject: ... Body: ... {DRAFT_NOTICE}`) rather than trusting the model to volunteer it, which is also why `crew_engine.py` appends the notice again as a belt-and-suspenders check.

### 7. Compatibility shim (`backend/crewai_compat.py`)

- `disable_unsupported_cache_breakpoints()` monkeypatches `crewai.llms.cache.mark_cache_breakpoint` to a no-op. CrewAI unconditionally tags outgoing messages with an Anthropic-specific `cache_breakpoint` field; Groq's API rejects requests carrying it with an HTTP 400. Both crew builders call this once before constructing their `LLM`, scoped to AgentForge's own Groq-backed workflows.

### 8. Events, end to end (`backend/event_emitter.py`)

- `EventEmitter` wraps a thread-safe `queue.Queue`, a `Lock`-protected monotonic `sequence` counter, and a `_closed` flag. `emit()` builds and enqueues an `AgentEvent` (Pydantic model: `task_id`, `type`, UTC `timestamp`, `sequence`, `data`); `serialize()` renders one as an SSE `data: <json>\n\n` line; `close()` enqueues a `None` sentinel exactly once, which both endpoint loops in `main.py` treat as "stream is done."

## Issues found and changes made (chronological)

1. **Deprecated Groq model.** The project originally targeted `llama-3.1-70b-versatile`. Migrated the default to `openai/gpt-oss-20b` (`GROQ_MODEL` in `.env`/`config.py`).
2. **CrewAI couldn't call Groq at all.** CrewAI's default LLM integration didn't support Groq directly; added `crewai[litellm]` so CrewAI routes model calls through LiteLLM, which does.
3. **Groq rejected every CrewAI request with HTTP 400.** CrewAI was unconditionally attaching an Anthropic-only `cache_breakpoint` field to outgoing messages, which Groq's API doesn't accept. Added the `crewai_compat.disable_unsupported_cache_breakpoints()` monkeypatch, called before building either crew's `LLM`.
4. **`duckduckgo-search` was renamed/deprecated upstream.** Migrated `WebSearchTool` to the `ddgs` package (`DDGS().text(...)`), keeping the same normalized `{title, url, snippet}` output contract.
5. **The raw SSE endpoint (`/tasks/stream`) was unreliable against the local Streamlit setup.** Rather than fight Streamlit's interaction with a long-held streaming HTTP connection, added a second, bounded `POST /tasks` endpoint that runs the task to completion server-side and returns its full ordered event list as one JSON payload, and switched the Streamlit frontend (`sse_client.stream_task`) to use it. `/tasks/stream` was kept as-is for API clients that can safely hold an SSE connection open.
