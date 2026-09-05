# AgentForge — Phase 1 Implementation Guide

## Objective

Phase 1 delivers a usable local AgentForge application: a user submits a natural-language task, the backend classifies it with Groq, runs the appropriate CrewAI workflow, and streams visible progress to a Streamlit interface.

The two supported workflows are:

1. **Research** — search the public web and return a concise, source-backed answer.
2. **Email drafting** — turn a request into a polished email draft. This phase does **not** send email.

This document is the implementation contract for Phase 1. It follows the architecture and technology decisions in [AGENTFORGE_FULL_IMPLEMENTATION.md](AGENTFORGE_FULL_IMPLEMENTATION.md).

## In scope

- FastAPI backend with health and task-stream endpoints.
- Groq-backed intent classification using a configurable current model (default: `openai/gpt-oss-20b`).
- CrewAI research flow and multi-agent email-draft crew.
- DuckDuckGo web-search tool.
- Server-Sent Events (SSE) for task lifecycle, agent activity, tool activity, result, and error messages.
- Streamlit chat-style UI with an agent network panel and ordered task event log.
- Unit tests for intent parsing, the search tool, and email workflow wiring.

## Explicitly out of scope

- Sending email, Gmail OAuth, token storage, or any database.
- GitHub access, repository editing, Docker sandbox execution, or pull requests.
- Browser automation, bookings, user accounts, durable sessions, or agent memory.
- Persisting a user request, event stream, API key, or result to disk.

Email-related requests are therefore **draft-only** in this release. The UI must state that clearly before a user runs an email task.

## Prerequisites

Use Python 3.11 or later. Create a Groq API key and add only the following required secret to `.env`:

```env
GROQ_API_KEY=gsk_your_key_here
GROQ_MODEL=openai/gpt-oss-20b
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
BACKEND_BASE_URL=http://localhost:8000
STREAMLIT_PORT=8501
```

Never commit `.env`. The checked-in `.env.example` remains value-free.

## Phase 1 files

Only these files require functional implementation in this phase. Leave Phase 2–4 placeholders untouched unless an import requires a harmless package marker.

| File | Responsibility |
|---|---|
| `requirements.txt` | Backend dependencies needed in Phase 1 only. |
| `requirements-frontend.txt` | Streamlit and SSE client dependencies. |
| `backend/config.py` | Load, validate, and expose environment settings. |
| `backend/main.py` | FastAPI app, request validation, health route, SSE route. |
| `backend/intent_parser.py` | Groq request and strict normalization to the intent contract. |
| `backend/event_emitter.py` | In-memory event queue and SSE serialization. |
| `backend/crew_engine.py` | Dispatch an intent to research or email-drafting workflow. |
| `backend/tools/web_search_tool.py` | CrewAI-compatible DDGS web-search tool. |
| `backend/templates/search_flow.py` | Search then synthesize workflow. |
| `backend/templates/email_crew.py` | Planner, writer, and reviewer email-draft crew. |
| `frontend/app.py` | Page composition and task launch. |
| `frontend/components/agent_graph.py` | Current workflow/agent status display. |
| `frontend/components/event_log.py` | Render append-only live task events. |
| `frontend/components/chat_input.py` | Prompt and optional context input. |
| `frontend/components/sidebar.py` | Backend URL and Phase 1 capability information. |
| `frontend/utils/sse_client.py` | Connect to and parse the SSE endpoint. |
| `frontend/utils/state.py` | Initialize/reset Streamlit session state. |
| `tests/test_intent_parser.py` | Intent parser success/failure behavior. |
| `tests/test_web_search_tool.py` | Search result normalization and failures. |
| `tests/test_email_crew.py` | Email workflow composition and safe draft-only behavior. |

## Dependencies

`requirements.txt`:

```text
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
crewai[litellm]>=0.55.0
crewai-tools>=0.8.0
groq>=0.9.0
ddgs>=9.0.0
python-dotenv>=1.0.0
httpx>=0.27.0
pydantic>=2.7.0
```

`requirements-frontend.txt`:

```text
streamlit>=1.35.0
requests>=2.31.0
sseclient-py>=1.8.0
streamlit-autorefresh>=1.0.1
```

Keep Phase 2 and Phase 3 packages out of the initial install. That keeps the first runnable build small and prevents OAuth/browser setup from becoming a Phase 1 dependency.

## Runtime architecture

```text
Streamlit UI
    │ POST /tasks/stream (prompt, context)
    ▼
FastAPI
    │ classify request with Groq
    ▼
Intent parser ──► Crew engine ──► Email crew or research flow
    │                  │                    │
    └──── SSE events ◄┴──── agent/tool callbacks
                         │
                         └── DuckDuckGo search (research only)
```

The backend runs a task in a worker thread so the async SSE generator can continuously drain the event queue. No Redis, background database, or event persistence is used in Phase 1.

## API contract

### `GET /health`

Returns a simple readiness response:

```json
{"status":"ok"}
```

### `POST /tasks/stream`

This endpoint returns `text/event-stream`.

Request body:

```json
{
  "prompt": "Research current best practices for FastAPI SSE and summarize them.",
  "context": "The audience is a small Python team."
}
```

Validation rules:

- `prompt` is required after trimming and must be 1–4,000 characters.
- `context` is optional and must be at most 4,000 characters.
- Do not accept API keys, GitHub tokens, OAuth credentials, or recipient lists in Phase 1-specific fields.
- The server generates a UUID `task_id`; it is never supplied by the client.

The response emits one JSON object per SSE `data:` line. Every event follows this envelope:

```json
{
  "task_id": "uuid",
  "type": "task_started",
  "timestamp": "2026-09-02T12:34:56.789Z",
  "sequence": 1,
  "data": {}
}
```

Allowed event types and required `data` fields:

| Type | Data |
|---|---|
| `task_started` | `prompt` (sanitized) |
| `intent_detected` | `intent`, `confidence`, `reason` |
| `workflow_started` | `workflow`, `agents` |
| `agent_started` | `agent`, `role` |
| `agent_completed` | `agent`, `summary` |
| `tool_started` | `tool`, `input_summary` |
| `tool_completed` | `tool`, `summary` |
| `result` | `intent`, `content`, `sources` |
| `error` | `code`, `message`, `retryable` |
| `task_completed` | `status` (`completed` or `failed`) |

Emit events in order. The final event is always `task_completed`, including after a failure. Send SSE keep-alive comments at least every 15 seconds while work is running.

## Intent contract

The parser returns this normalized shape to the engine:

```json
{
  "intent": "research",
  "confidence": 0.94,
  "reason": "The request asks for information gathering and a summary.",
  "task_summary": "Research FastAPI SSE practices for a Python team",
  "constraints": ["Audience: small Python team"]
}
```

Permitted `intent` values:

- `research` — find and synthesize publicly available information.
- `draft_email` — write or revise an email without sending it.
- `unsupported` — requests that belong to later phases or cannot be done safely in this phase.

Prompt the Groq model to return JSON only. Validate its result with Pydantic. If model output is malformed, retry once with a corrective JSON-only prompt. If it remains invalid, return `unsupported` with a low confidence and a plain-language reason; do not fail the whole request solely because classification output was malformed.

Routing rules that override the model when obvious:

- A request to send, schedule, or deliver email routes to `draft_email`; the result must explain that it is a draft only.
- GitHub, repository changes, pull requests, browser actions, reservations, logins, or payment requests route to `unsupported`.
- A request that asks to find, compare, explain, or summarize information normally routes to `research`.

## Workflow behavior

### Research flow

1. Emit `workflow_started` for `search_flow` with `Researcher` and `Synthesizer`.
2. The researcher invokes `WebSearchTool` with a focused query derived from the task summary.
3. Return up to five normalized results: title, URL, and snippet. Deduplicate by URL.
4. The synthesizer produces an answer tailored to the prompt and context. It must distinguish verified facts from recommendations or inference.
5. Emit `result` containing markdown content and the URLs used as sources.

The synthesizer must not invent citations. If search returns no usable results, return a transparent response saying that no reliable results were found, with an empty `sources` array.

### Email drafting crew

Use three sequential agents:

| Agent | Goal | Output |
|---|---|---|
| Email Planner | Extract audience, objective, tone, calls to action, and missing details. | Brief for the writer. |
| Email Writer | Create a clear subject line and email body from the brief. | Candidate draft. |
| Email Reviewer | Check clarity, tone, factual claims, and whether it implies a send. | Final draft with brief notes. |

The final response must include a subject line and body. When crucial information is missing, use clearly labeled placeholders such as `[launch date]`; do not fabricate dates, recipients, or commitments. Add the exact user-facing note: **“Draft only — AgentForge did not send or schedule this email.”**

## Tool contract: `WebSearchTool`

Implement the tool as a CrewAI `BaseTool` or the equivalent tool abstraction supported by the installed CrewAI version. Its public behavior is:

```python
search(query: str, max_results: int = 5) -> list[dict[str, str]]
```

Rules:

- Reject blank queries and clamp `max_results` to 1–5.
- Use `ddgs.DDGS().text()`.
- Normalize provider-specific fields to `title`, `url`, and `snippet`.
- Strip excess whitespace, drop entries without a valid URL, and deduplicate URLs.
- Never include raw provider exceptions in an SSE result. Raise or return a controlled `SearchUnavailable` condition that the engine translates into an `error` event.
- Emit `tool_started` before execution and `tool_completed` after successful normalization. Event payloads contain the query summary and number of results, never unbounded raw page text.

## Backend implementation notes

### Configuration

`backend/config.py` loads `.env` once and exposes a typed, immutable configuration object. Fail fast at startup if `GROQ_API_KEY` is absent. Do not print the key, even in debug logging.

### Event emitter

`backend/event_emitter.py` owns a per-task queue and monotonically increasing sequence counter. It provides:

- `emit(type, data)` — validate and enqueue an event.
- `serialize(event)` — return `data: <json>\n\n` with JSON-safe content.
- `close()` — mark the task event stream complete.

Keep event payloads small. Limit summaries to 500 characters and final results to a reasonable response size before passing them to Streamlit.

### Crew engine

`backend/crew_engine.py` is the only module that selects a template. It must not contain web-search implementation or Streamlit concerns. Its `run(intent, prompt, context, emitter)` method returns a normalized result object:

```json
{
  "intent": "research",
  "content": "...",
  "sources": ["https://example.com"]
}
```

The engine catches expected workflow, provider, and tool errors and emits a safe `error` event. Unexpected exceptions must be logged server-side with a task ID but presented to the UI as a generic failure message.

### FastAPI service

Configure CORS only for the local Streamlit origin during development (`http://localhost:8501`). Define response models for ordinary JSON routes and request models for task submission. Do not expose a synchronous task endpoint in Phase 1; the UI consumes the single streaming endpoint.

## Frontend behavior

The Streamlit interface has three clear areas:

- **Sidebar:** backend URL, a Phase 1 capability summary, and reset-session action. It must not ask for GitHub or Google credentials.
- **Main input:** a primary task prompt plus optional context. Show brief examples for research and email drafting.
- **Live execution panel:** selected intent, active workflow/agents, chronological event log, final answer, and clickable research sources.

The frontend opens the SSE stream only after form submission. `sse_client.py` yields parsed JSON event objects; it must tolerate comment heartbeats and malformed individual messages without crashing the page. `state.py` initializes `events`, `current_task`, `result`, and `is_running` in `st.session_state`.

Display failures in plain language, retain all events already received, and restore the submit control when `task_completed` arrives.

## Error handling

| Situation | User-visible response | Event code |
|---|---|---|
| No Groq key at startup | Backend cannot start; explain required configuration in logs. | N/A |
| Groq rate limit | Ask the user to retry shortly. | `llm_rate_limited` |
| Groq/provider failure | Explain that request classification or generation is unavailable. | `llm_unavailable` |
| Search has no results | Explain that no reliable results were found. | No error required |
| Search provider failure | Explain that web search is temporarily unavailable. | `search_unavailable` |
| Unsupported request | State the relevant future phase and offer the current safe alternative. | `unsupported_request` |
| Invalid API request | Return HTTP 422 with field-level validation detail. | N/A |

## Tests

Run unit tests without making real Groq or DuckDuckGo calls. Mock both providers.

`test_intent_parser.py` must cover a valid research response, a valid email-draft response, malformed model JSON with retry/fallback, and an explicit future-phase request.

`test_web_search_tool.py` must cover normalization, URL deduplication, result limit clamping, blank query rejection, and provider failure translation.

`test_email_crew.py` must confirm the crew has planner/writer/reviewer stages and that its completed result includes a subject, body, and the exact draft-only notice. It must verify that no send-email function is invoked.

Add a lightweight API test if practical: mock the engine, call `/tasks/stream`, and assert event ordering begins with `task_started` and ends with `task_completed`.

## Local runbook

Install backend and frontend dependencies into the same virtual environment, then start the backend and frontend in separate terminals:

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
streamlit run frontend/app.py --server.port 8501
```

Before opening the UI, confirm the backend is ready:

```bash
curl http://localhost:8000/health
```

## Definition of done

Phase 1 is complete only when all of the following are true:

- The application starts with only `GROQ_API_KEY` configured.
- A research request streams intent, workflow, agent/tool events, a result, and completion to Streamlit.
- Research results include only URLs actually returned by the search workflow.
- An email request yields a reviewed subject/body draft and never performs a send or scheduling action.
- A Phase 2 or Phase 3 request is rejected safely with a useful explanation.
- Missing configuration, provider failures, empty search results, and invalid requests are handled without an uncaught UI error.
- Unit tests pass with network calls mocked.
- `.env` remains ignored by Git and secrets do not appear in logs, events, exceptions, or UI output.

## Deferred work

Phase 2 adds GitHub PAT handling, sandboxed repository operations, tests, branching, and PR creation. Phase 3 adds Gmail OAuth2, real email sending, browser automation, Supabase-backed sessions, and token storage. Phase 4 adds durable agent memory, multi-session behavior, and self-improvement loops.

##Progress

AgentForge Phase 1 — Progress So Far

Completed project setup
- Created the complete AgentForge folder structure.
- Created the Phase 1 implementation guide.
- Created isolated Python environments:
  - .venv — Python 3.9; not suitable for current CrewAI.
  - .venv312 — Python 3.12; the environment used for Phase 1.
- Added .env, .env.example, .gitignore, dependency files, backend, frontend, and test files.
Backend implemented
The backend uses:
- FastAPI — API server.
- Uvicorn — runs the FastAPI application.
- Pydantic — validates request/event data.
- python-dotenv — loads GROQ_API_KEY and optional GROQ_MODEL from .env.
- Thread-safe event queue — tracks task progress internally.
Implemented endpoints:
- GET /health
  Checks whether the backend is running.
- POST /tasks/stream
  Returns task events through Server-Sent Events (SSE), for API clients.
- POST /tasks
  Runs a task and returns its ordered event list as JSON. The Streamlit UI uses this endpoint because it is more reliable in the current local Streamlit setup.
LLM and agent system implemented
The project uses:
- Groq Python SDK
  Used directly for intent classification.
- Groq model: openai/gpt-oss-20b
  Configurable with:
  GROQ_MODEL=openai/gpt-oss-20b
- CrewAI
  Used to coordinate agents for research and email drafting.
- LiteLLM through crewai[litellm]
  Required so CrewAI can call Groq models.
Implemented intent types:
- research
- draft_email
- unsupported
Examples:
- “Research renewable energy policy in India” → research
- “Draft an email to my team” → draft_email
- “Create a GitHub pull request” → unsupported, because GitHub automation belongs to Phase 2.
Research workflow implemented
Research uses:
- DDGS (ddgs)
  Searches the public web. This replaced the renamed duckduckgo-search package.
- WebSearchTool
  Validates search queries, limits results, removes duplicates, normalizes title/URL/snippet data, and hides provider-specific errors.
- CrewAI Research Synthesizer
  Receives the search results and creates a source-grounded summary.
Research flow:
User prompt
→ Groq intent classification
→ DDGS public-web search
→ normalized search results
→ CrewAI synthesis
→ result + source URLs
Email-drafting workflow implemented
Email tasks use a sequential CrewAI crew with three agents:
1. Email Planner
   Extracts audience, objective, tone, CTA, and missing details.
2. Email Writer
   Produces a subject line and email body.
3. Email Reviewer
   Reviews the draft for clarity and unsupported claims.
Safety rule:
Draft only — AgentForge did not send or schedule this email.
Phase 1 does not send email, access Gmail, or schedule messages.
Event system implemented
Each task creates ordered events such as:
task_started
intent_detected
workflow_started
agent_started
tool_started
tool_completed
agent_completed
result
task_completed
Each event includes:
- task ID
- timestamp
- sequence number
- event type
- structured data payload
Frontend implemented
The Streamlit UI includes:
- Backend URL configuration
- Task prompt input
- Optional context input
- Agent-network panel
- Activity/event log
- Result panel
- Source links for research results
- Session reset control
Current frontend behavior:
- It sends tasks to POST /tasks.
- It receives the complete ordered event list as JSON.
- It then renders the task status, event log, result, and sources.
The raw SSE endpoint is still implemented for API clients, but the Streamlit UI currently uses the JSON endpoint for local reliability.
Compatibility fixes completed
- Migrated deprecated Groq model:
  - Old: llama-3.1-70b-versatile
  - Current default: openai/gpt-oss-20b
- Added CrewAI LiteLLM support:
  - crewai[litellm]>=0.55.0
- Added a CrewAI/Groq compatibility workaround:
  - CrewAI was injecting cache_breakpoint metadata.
  - Groq rejected it.
  - The project now disables this unsupported metadata for Groq workflows.
- Migrated web search dependency:
  - Old: duckduckgo-search
  - Current: ddgs
Automated tests completed
Current test result:
12 passed
Tests cover:
- Intent parsing
- Email draft-only behavior
- Email crew composition
- DDGS result normalization and errors
- CrewAI/Groq compatibility patch
- SSE parsing
- JSON task endpoint event order
Current limitation / next verification
The backend task endpoint and event stream have been verified directly from the terminal.
The remaining manual verification is confirming the refreshed Streamlit interface displays results after restarting both services with the latest code. Once that is confirmed, Phase 1 will be ready for end-to-end use.
Not implemented yet
These belong to later phases:
- Gmail OAuth and sending email
- GitHub PAT access
- Repository cloning/editing/testing
- Pull request creation
- Docker sandbox
- Browser automation
- Supabase database/session storage
- Agent memory
- Multi-session support

## Phase 2 — Status: complete and verified end-to-end

GitHub repository tasks no longer use the four-agent CrewAI design described above — that was superseded by a deterministic, single-LLM-call Python pipeline (`backend/templates/github_crew.py`) to stay well inside Groq's free-tier token-per-minute limit. Full pipeline diagram, plain-language walkthrough, and the complete chronological list of bugs found and fixed while getting here live in [IMPLEMENTATION_PHASE_2.md](IMPLEMENTATION_PHASE_2.md) — this section is a summary only.

**How a GitHub request runs today:** clone the repo inside a locked-down Docker container → list files → sample the 5 most relevant files → one Groq call returns a JSON change plan → apply the writes → run an approved test/lint command → commit and push → open (or update) a pull request. The Docker container runs as an unprivileged `sandboxuser` with almost all Linux capabilities dropped, no host filesystem access, and capped CPU/memory/PIDs; the GitHub PAT reaches git via a `GIT_ASKPASS` helper and an env var, never via a command-line argument, so it can't leak through process listings.

**Session-based branching:** the Streamlit sidebar generates and persists a `session_id` for the browser session. Repeated GitHub queries against the same repo keep committing to the same `agentforge/<session_id>` branch and the same open PR; checking "Start a new branch" (or changing the repo URL) starts a fresh one. The underlying Docker volume is deleted after every task — nothing needs to persist locally since state lives on the pushed remote branch.

**Verified working end-to-end** via the actual Streamlit UI → FastAPI → Docker pipeline: clone, list/read/write files, run pytest, commit, push, and open a pull request all succeeded against a real repository with a live fine-grained PAT (scopes: **Contents: Read and write**, **Pull requests: Read and write**). Multiple queries in one session were confirmed to land on the same branch/PR.

Automated status: full suite passes except one pre-existing, unrelated failure (`test_github_rate_limit_delay_uses_provider_hint` — a retry-delay math expectation mismatch, predates this work).

Cleaned up: all throwaway `debug_*.py`/`test_github*.py` scripts used while diagnosing the sandbox bugs have been deleted, including one that had a (since-confirmed-revoked) GitHub PAT hardcoded in plaintext.

Not yet done for Phase 2: none — the workflow described in [AGENTFORGE_FULL_IMPLEMENTATION.md](AGENTFORGE_FULL_IMPLEMENTATION.md) §6 is implemented and manually verified. Phase 3 (browser automation, Gmail OAuth2, Supabase sessions) has not been started.
