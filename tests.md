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

---

## Phase 8 — Blueprint taxonomy

**What changed:** `backend/blueprints.py` adds 3 reusable execution patterns (`single_agent_loop`, `sequential_stages`, `parallel_fanout`) for the Phase 10 planner to choose from — all thin wrappers around already-tested machinery (`agent_loop.py`, `llm_fallback.py`). Nothing existing changed; nothing calls these yet outside their own tests.

**Validate it yourself:**

1. Run the automated tests, including the real wall-clock concurrency proof:
   ```
   .venv312/bin/python -m pytest tests/test_blueprints.py -v
   ```
2. See all three blueprints work against the real LLM, including genuine concurrent overlap:
   ```
   .venv312/bin/python -c "
   import time
   from backend.config import get_settings
   from backend.blueprints import run_single_agent_loop, run_sequential_stages, Stage, run_parallel_fanout

   settings = get_settings()
   print(run_single_agent_loop('You are helpful.', 'Reply with one word: hello', tools=[], settings=settings))

   stages = [
       Stage('Brainstorm', 'Suggest one tagline for a coffee shop, just the tagline.', lambda p, prior: p),
       Stage('Polish', 'Polish this tagline to be punchier, just the final tagline.', lambda p, prior: prior[0]),
   ]
   print(run_sequential_stages(stages, 'A cozy neighborhood coffee shop', settings))

   start = time.monotonic()
   results = run_parallel_fanout('Reply with one word.', ['sky color?', 'grass color?', 'banana color?'], lambda i: i, lambda: [], settings)
   print(results, f'{time.monotonic()-start:.2f}s for 3 concurrent calls')
   "
   ```
   The fan-out timing should be noticeably less than 3x a single call's latency — that's the proof it's genuinely concurrent, not sequential.
3. Nothing to check on the frontend for this phase either — same reason as Phase 7.

---

## Phase 9 — Redis pipeline-state store

**What changed:** `backend/pipeline_state.py` adds a Redis-backed store for one pipeline run's step-to-step handoff data — the mechanism Phase 11's executor will use to pass a step's output into the next step. No TTL (unlike Gmail tokens) — explicit `delete_run()` cleanup once a run finishes instead.

**Validate it yourself:**

1. Run the automated tests:
   ```
   .venv312/bin/python -m pytest tests/test_pipeline_state.py -v
   ```
2. With Redis running (`docker start agentforge-redis` if needed), confirm real persistence and cleanup:
   ```
   .venv312/bin/python -c "
   from backend.config import get_settings
   from backend.pipeline_state import get_pipeline_state_store

   get_settings.cache_clear()
   get_pipeline_state_store.cache_clear()
   store = get_pipeline_state_store()
   print('store type:', type(store).__name__)  # should say RedisPipelineStateStore

   store.set_step_result('my-test-run', 'research', ['Company A', 'Company B'])
   print('all results:', store.get_all_results('my-test-run'))
   store.delete_run('my-test-run')
   print('after delete:', store.get_all_results('my-test-run'))  # should be {}
   "
   ```
3. Confirm directly in Redis (should show the hash before you run step 2's delete, empty/missing after):
   ```
   docker exec agentforge-redis redis-cli HGETALL "agentforge:pipeline_state:my-test-run"
   ```
4. Nothing to check on the frontend for this phase either — same reason as Phase 7/8.

---

## Phase 10 — Pipeline planner

**What changed:** `backend/pipeline_planner.py` turns a compound request's detected intents into a concrete, validated `PipelinePlan` (steps, blueprint per step, tools, dependencies, fan-out count) — the "brain" of the pipeline system. Nothing calls it from `main.py` yet (that's Phase 11); this is planner-only.

**Validate it yourself:**

1. Run the automated tests:
   ```
   .venv312/bin/python -m pytest tests/test_pipeline_planner.py -v
   ```
2. See a real plan generated for the exact motivating example from this roadmap:
   ```
   .venv312/bin/python -c "
   from backend.config import get_settings
   from backend.intent_parser import IntentParser
   from backend.pipeline_planner import plan_pipeline

   settings = get_settings()
   prompt = 'find the top 3 AI companies hiring right now, then visit each of their individual career pages on their own website to find their talent acquisition team email, then send each one a personalized email'
   intents = IntentParser().parse_intents(prompt, has_gmail_context=True)
   plan = plan_pipeline(prompt, intents, settings)
   print('summary:', plan.summary)
   for step in plan.steps:
       print(f'- [{step.name}] blueprint={step.blueprint} tools={step.tools} fanout={step.fanout_count} depends_on={step.depends_on}')
   "
   ```
   Should print a multi-step plan, likely including at least one `parallel_fanout` step capped at `fanout=3` (matching "top 3"), with `depends_on` chaining steps in a sensible order. Try a few different phrasings — the plan's exact shape (single-agent vs. fan-out) legitimately varies with how explicit the request is about needing individual site visits.
3. Nothing to check on the frontend for this phase either — same reason as Phase 7/8/9.

