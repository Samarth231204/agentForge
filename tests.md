# AgentForge — Manual Validation Guide

A phase-by-phase guide for manually validating the dynamic multi-agent pipeline system (`AGENTFORGE_FULL_IMPLEMENTATION_2.md`), written incrementally as each phase completes. Automated `pytest` coverage exists for all of this too (`pytest tests/ -q` from the repo root) — this document is specifically for *you* to independently confirm real, live behavior yourself, the same way every phase in this project has been verified throughout (never trusting mocked tests alone).

Prerequisites for any of the checks below: `.venv312` set up with `requirements.txt` installed, `.env` populated (Groq keys required; Redis/Mem0/OpenRouter/Google optional but needed for the phases that use them), and — for phases involving Redis — a local Redis reachable at `REDIS_URL` (e.g. `docker run -d --name agentforge-redis -p 6379:6379 redis:7-alpine`).

---

## Phase 5 — OpenRouter fallback chain expansion

**What changed:** the OpenRouter portion of the LLM fallback chain went from exactly one hardcoded model to a configurable, ordered list of several.

**Validate it yourself:**

1. Run the automated tests:
   ```
   .venv312/bin/python -m pytest tests/test_llm_fallback.py tests/test_config.py -v
   ```
2. Confirm your real config now lists multiple models:
   ```
   .venv312/bin/python -c "
   from backend.config import get_settings
   print(get_settings().openrouter_models)
   "
   ```
   Should print a tuple of 5 model names, not one.
3. See a real model answer through the actual project function:
   ```
   .venv312/bin/python -c "
   from backend.config import get_settings
   from backend.llm_fallback import complete_with_fallback

   settings = get_settings()
   candidates = [(f'openrouter/{m}', settings.openrouter_api_key) for m in settings.openrouter_models]
   print('trying:', [m for m, _ in candidates])
   response = complete_with_fallback(candidates, temperature=0.2, messages=[{'role': 'user', 'content': 'Reply with exactly one word: hello'}])
   print('answered by:', response.model)
   print('response:', response.choices[0].message.content)
   "
   ```

---

## Phase 6 — Multi-intent classification

**What changed:** `IntentParser` can now detect a compound request (one needing more than one of the 6 intent categories) instead of always forcing a single classification. Also fixes a real bug where the classifier occasionally returned an invalid `constraints` field and silently gave up rather than trying other models.

**Validate it yourself:**

1. Run the automated tests:
   ```
   .venv312/bin/python -m pytest tests/test_intent_parser.py tests/test_task_api.py -v
   ```
2. Start the backend (`.venv312/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000`) and confirm an ordinary single-intent request still works exactly as before:
   ```
   curl -s -X POST http://localhost:8000/tasks -H "Content-Type: application/json" \
     -d '{"prompt": "what is the capital of France", "context": ""}' | python3 -m json.tool
   ```
   Should classify as `research` and return a real answer (this specific prompt used to intermittently fail before the `constraints` bug fix — if you see "I could not reliably classify this request," run it again and let me know, since that would mean a regression).
3. Confirm a genuinely compound request is detected and handled gracefully (not crashed, not silently truncated to one step):
   ```
   curl -s -X POST http://localhost:8000/tasks -H "Content-Type: application/json" \
     -d '{"prompt": "research the top 3 AI companies hiring right now, then reach out to their recruiting teams about job opportunities", "context": "", "gmail_session_id": "some-session"}' | python3 -m json.tool
   ```
   Look for an `intent_detected` event with `"compound": true` and `"all_intents": ["research", "send_email"]` (or similar), and a `result` event explaining that multi-step pipelines aren't runnable yet. Note: a prompt containing exact phrases like "send an email" will instead hit the fast keyword-based single-intent path (by design) and never reach this compound-detection logic — phrase it more indirectly (as above) to actually exercise the LLM classifier.
4. This flows through the existing `/tasks` endpoint and event shape unchanged, so it's already visible in the real Streamlit frontend today — submit either prompt above through the actual UI and confirm the result panel renders correctly either way.

---

## Phase 7 — Tool registry

**What changed:** `backend/tool_registry.py` now describes all 4 existing tools (browser, web_search, github, email) — name, description, JSON schema — in one place, for the Phase 10 planner to read later. No existing behavior changed anywhere; nothing calls this registry yet outside its own tests.

**Validate it yourself:**

1. Run the automated tests:
   ```
   .venv312/bin/python -m pytest tests/test_tool_registry.py -v
   ```
2. Inspect the real registry directly (no HTTP endpoint exists for this yet, so this is the equivalent of a "real-service" check for this phase):
   ```
   .venv312/bin/python -c "
   from backend.tool_registry import get_tool_registry
   for tool in get_tool_registry():
       print(tool.name, '->', list(tool.schema['properties'].keys()))
   "
   ```
   Should print all 4 tools with their real parameter names.
3. Nothing to check on the frontend for this phase — it's a pure backend building block with no wired endpoint yet.

