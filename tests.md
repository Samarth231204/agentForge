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
