from backend.intent_parser import Intent
from backend.event_emitter import EventEmitter


class _SyncThread:
    """Stand-in for threading.Thread that runs its target synchronously on
    .start() — used to make _run_task's background side-effect thread
    deterministic and observable within a single test."""

    def __init__(self, target, args=(), **_kwargs):
        self._target = target
        self._args = args

    def start(self) -> None:
        self._target(*self._args)


def test_json_task_endpoint_returns_ordered_events(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY_1", "test-key")
    from fastapi.testclient import TestClient

    import backend.main as main

    monkeypatch.setattr(
        main.IntentParser,
        "parse_intents",
        lambda _self, _prompt, _context, _has_repo_context=False, _has_gmail_context=False: [Intent(
            intent="unsupported", confidence=1, reason="test", task_summary="test"
        )],
    )
    with TestClient(main.app) as client:
        response = client.post("/tasks", json={"prompt": "Create a GitHub pull request", "context": ""})

    assert response.status_code == 200
    assert [event["type"] for event in response.json()["events"]] == [
        "task_started",
        "intent_detected",
        "result",
        "task_completed",
    ]


def test_history_endpoint_returns_entries_after_a_completed_task(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY_1", "test-key")
    from fastapi.testclient import TestClient

    import backend.main as main
    from backend.history_store import InMemoryHistoryStore
    from backend.memory_manager import NullMemoryManager

    fresh_history_store = InMemoryHistoryStore()
    monkeypatch.setattr(main, "get_history_store", lambda: fresh_history_store)
    monkeypatch.setattr(main, "get_memory_manager", lambda: NullMemoryManager())
    monkeypatch.setattr(main, "run_critic", lambda *_a, **_k: None)
    monkeypatch.setattr(
        main.IntentParser,
        "parse_intents",
        lambda _self, _prompt, _context, _has_repo_context=False, _has_gmail_context=False: [Intent(
            intent="research", confidence=1, reason="test", task_summary="test"
        )],
    )
    monkeypatch.setattr(main.CrewEngine, "run", lambda self, *_a, **_k: {"intent": "research", "content": "Some answer", "sources": []})
    monkeypatch.setattr(main, "Thread", _SyncThread)

    with TestClient(main.app) as client:
        response = client.post("/tasks", json={"prompt": "Research something", "context": "", "history_session_id": "hist-1"})
        assert response.status_code == 200
        history_response = client.get("/history", params={"state": "hist-1"})

    assert history_response.status_code == 200
    tasks = history_response.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["intent"] == "research"
    assert tasks[0]["prompt"] == "Research something"
    assert tasks[0]["content"] == "Some answer"

    # A different session id sees nothing — history is isolated per session.
    other_session = client.get("/history", params={"state": "some-other-session"})
    assert other_session.json()["tasks"] == []


def test_history_endpoint_degrades_gracefully_when_the_store_is_unreachable(monkeypatch):
    from fastapi.testclient import TestClient

    import backend.main as main

    class ExplodingHistoryStore:
        def list(self, *_a, **_k):
            raise RuntimeError("Redis is down")

    monkeypatch.setattr(main, "get_history_store", lambda: ExplodingHistoryStore())

    with TestClient(main.app) as client:
        response = client.get("/history", params={"state": "any-session"})

    assert response.status_code == 200
    assert response.json() == {"tasks": []}


def test_side_effect_failures_never_affect_the_task_response(monkeypatch):
    """Memory/history/critic are best-effort side channels — an outage in
    any of them must never change the task's own HTTP response."""
    monkeypatch.setenv("GROQ_API_KEY_1", "test-key")
    from fastapi.testclient import TestClient

    import backend.main as main

    class ExplodingHistoryStore:
        def append(self, *_a, **_k):
            raise RuntimeError("Redis is down")

    class ExplodingMemoryManager:
        def remember(self, *_a, **_k):
            raise RuntimeError("Mem0 is down")

        def recall(self, *_a, **_k):
            raise RuntimeError("Mem0 is down")

    def exploding_critic(*_a, **_k):
        raise RuntimeError("critic review is down")

    monkeypatch.setattr(main, "get_history_store", lambda: ExplodingHistoryStore())
    monkeypatch.setattr(main, "get_memory_manager", lambda: ExplodingMemoryManager())
    monkeypatch.setattr(main, "run_critic", exploding_critic)
    monkeypatch.setattr(
        main.IntentParser,
        "parse_intents",
        lambda _self, _prompt, _context, _has_repo_context=False, _has_gmail_context=False: [Intent(
            intent="research", confidence=1, reason="test", task_summary="test"
        )],
    )
    monkeypatch.setattr(main.CrewEngine, "run", lambda self, *_a, **_k: {"intent": "research", "content": "Some answer", "sources": []})
    monkeypatch.setattr(main, "Thread", _SyncThread)

    with TestClient(main.app) as client:
        response = client.post("/tasks", json={"prompt": "Research something", "context": "", "history_session_id": "hist-2"})

    assert response.status_code == 200
    events = response.json()["events"]
    assert events[-1]["type"] == "task_completed"
    assert events[-1]["data"]["status"] == "completed"
    result_event = next(event for event in events if event["type"] == "result")
    assert result_event["data"]["content"] == "Some answer"


def test_a_gmail_token_store_outage_does_not_fail_an_unrelated_task(monkeypatch):
    """has_gmail_context is only a classification hint — a Redis outage on
    that check must not fail a task that has nothing to do with Gmail."""
    import backend.main as main

    class ExplodingTokenStore:
        def has(self, *_a, **_k):
            raise RuntimeError("Redis is down")

    monkeypatch.setattr(main, "get_token_store", lambda: ExplodingTokenStore())
    monkeypatch.setattr(
        main.IntentParser,
        "parse_intents",
        lambda _self, _prompt, _context, _has_repo_context=False, _has_gmail_context=False: [Intent(
            intent="unsupported", confidence=1, reason="test", task_summary="test"
        )],
    )
    emitter = EventEmitter("test-gmail-outage")
    main._run_task("Research something", "", "", "", "", "some-gmail-session", "", emitter)

    events = []
    while True:
        event = emitter.queue.get()
        if event is None:
            break
        events.append(event.model_dump(mode="json"))

    assert events[-1] == {**events[-1], "type": "task_completed", "data": {"status": "completed"}}


def _compound_intents():
    return [
        Intent(intent="research", confidence=0.9, reason="find companies", task_summary="x"),
        Intent(intent="send_email", confidence=0.85, reason="email each one", task_summary="y"),
    ]


def test_a_compound_request_with_a_valid_plan_proposes_it_without_executing(monkeypatch):
    """Phase 12's review gate: a compound request that the planner can turn
    into a valid plan gets PROPOSED, not run — nothing with real side
    effects (a sent email, a pushed commit, a browser action) executes
    until the user explicitly approves this exact plan via approved_plan."""
    import backend.main as main
    from backend.pipeline_planner import PipelinePlan, PipelineStep

    monkeypatch.setattr(main.IntentParser, "parse_intents", lambda _self, _p, _c, _r=False, _g=False: _compound_intents())
    fake_plan = PipelinePlan(steps=[PipelineStep(name="only_step", blueprint="single_agent_loop", intent="research", instructions="do it")], summary="test plan")
    monkeypatch.setattr(main, "plan_pipeline", lambda *_a, **_k: fake_plan)

    def exploding_execute(*_a, **_k):
        raise AssertionError("execute_pipeline must not run without an approved_plan")

    monkeypatch.setattr(main, "execute_pipeline", exploding_execute)

    emitter = EventEmitter("test-compound-proposed")
    main._run_task("Find AI job postings and email each company's talent team", "", "", "", "", "", "", emitter)

    events = []
    while True:
        event = emitter.queue.get()
        if event is None:
            break
        events.append(event.model_dump(mode="json"))

    intent_event = next(e for e in events if e["type"] == "intent_detected")
    assert intent_event["data"]["compound"] is True
    assert intent_event["data"]["all_intents"] == ["research", "send_email"]

    workflow_event = next(e for e in events if e["type"] == "workflow_started")
    assert workflow_event["data"]["workflow"] == "dynamic_pipeline_proposed"
    assert workflow_event["data"]["agents"] == ["only_step"]

    result_event = next(e for e in events if e["type"] == "result")
    assert result_event["data"]["intent"] == "pipeline_proposed"
    assert result_event["data"]["plan"]["summary"] == "test plan"
    assert events[-1] == {**events[-1], "type": "task_completed", "data": {"status": "completed"}}


def test_an_approved_plan_actually_executes_via_the_pipeline_executor(monkeypatch):
    """The only path that actually runs a multi-step plan: approved_plan
    set on the request, matching what the frontend sends after the user
    clicks Generate on a reviewed plan."""
    import backend.main as main
    from backend.pipeline_planner import PipelinePlan, PipelineStep

    plan = PipelinePlan(steps=[PipelineStep(name="only_step", blueprint="single_agent_loop", intent="research", instructions="do it")], summary="approved plan")

    def exploding_plan_pipeline(*_a, **_k):
        raise AssertionError("plan_pipeline must not be called when approved_plan is already provided")

    monkeypatch.setattr(main, "plan_pipeline", exploding_plan_pipeline)
    monkeypatch.setattr(main, "execute_pipeline", lambda *_a, **_k: "the pipeline actually ran")

    emitter = EventEmitter("test-compound-approved")
    main._run_task("Find AI job postings and email each company's talent team", "", "", "", "", "", "", emitter, approved_plan=plan.model_dump())

    events = []
    while True:
        event = emitter.queue.get()
        if event is None:
            break
        events.append(event.model_dump(mode="json"))

    workflow_event = next(e for e in events if e["type"] == "workflow_started")
    assert workflow_event["data"]["workflow"] == "dynamic_pipeline"
    assert workflow_event["data"]["agents"] == ["only_step"]

    result_event = next(e for e in events if e["type"] == "result")
    assert result_event["data"]["content"] == "the pipeline actually ran"
    assert events[-1] == {**events[-1], "type": "task_completed", "data": {"status": "completed"}}


def test_an_invalid_approved_plan_fails_cleanly_instead_of_crashing():
    import backend.main as main

    emitter = EventEmitter("test-invalid-approved-plan")
    main._run_task("some prompt", "", "", "", "", "", "", emitter, approved_plan={"not": "a valid plan shape"})

    events = []
    while True:
        event = emitter.queue.get()
        if event is None:
            break
        events.append(event.model_dump(mode="json"))

    assert events[0]["type"] == "error"
    assert events[0]["data"]["code"] == "invalid_plan"
    assert events[-1] == {**events[-1], "type": "task_completed", "data": {"status": "failed"}}


def test_a_compound_request_the_planner_cannot_plan_reports_a_clear_message(monkeypatch):
    """If the planner itself fails (every candidate model produced an
    invalid plan), this must report that plainly rather than crash or
    silently only run the first detected intent."""
    import backend.main as main

    monkeypatch.setattr(main.IntentParser, "parse_intents", lambda _self, _p, _c, _r=False, _g=False: _compound_intents())
    monkeypatch.setattr(main, "plan_pipeline", lambda *_a, **_k: None)

    emitter = EventEmitter("test-compound-unplannable")
    main._run_task("Find AI job postings and email each company's talent team", "", "", "", "", "", "", emitter)

    events = []
    while True:
        event = emitter.queue.get()
        if event is None:
            break
        events.append(event.model_dump(mode="json"))

    result_event = next(e for e in events if e["type"] == "result")
    assert "research" in result_event["data"]["content"]
    assert "send_email" in result_event["data"]["content"]
    assert "could not build a reliable plan" in result_event["data"]["content"]

    assert events[-1] == {**events[-1], "type": "task_completed", "data": {"status": "completed"}}


def test_tasks_endpoint_actually_forwards_approved_plan_to_run_task(monkeypatch):
    """Regression: an earlier edit updated /tasks/stream's call site to
    forward approved_plan but missed the plain /tasks (JSON) endpoint —
    the one curl and the real Streamlit frontend actually use — so
    approved_plan was silently dropped and every "approved" plan request
    ran the ordinary single-intent path instead of executing the plan.
    Only caught by testing the real HTTP route, not by calling
    main._run_task() directly (which is what the other approved_plan test
    does and why this specific gap slipped through)."""
    from fastapi.testclient import TestClient

    import backend.main as main
    from backend.pipeline_planner import PipelinePlan, PipelineStep

    monkeypatch.setenv("GROQ_API_KEY_1", "test-key")

    def exploding_parse_intents(*_a, **_k):
        raise AssertionError("intent classification must not run when approved_plan is provided")

    monkeypatch.setattr(main.IntentParser, "parse_intents", exploding_parse_intents)
    monkeypatch.setattr(main, "execute_pipeline", lambda *_a, **_k: "executed via the real HTTP route")

    plan = PipelinePlan(steps=[PipelineStep(name="only_step", blueprint="single_agent_loop", intent="research", instructions="do it")], summary="approved plan")

    with TestClient(main.app) as client:
        response = client.post("/tasks", json={"prompt": "find companies and email them", "approved_plan": plan.model_dump()})

    assert response.status_code == 200
    events = response.json()["events"]
    result_event = next(e for e in events if e["type"] == "result")
    assert result_event["data"]["content"] == "executed via the real HTTP route"


def test_pipeline_plan_endpoint_reports_a_single_ordinary_intent_as_not_compound(monkeypatch):
    from fastapi.testclient import TestClient

    import backend.main as main

    monkeypatch.setenv("GROQ_API_KEY_1", "test-key")
    monkeypatch.setattr(main.IntentParser, "parse_intents", lambda _self, _p, _c, _r=False, _g=False: [Intent(intent="research", confidence=0.9, reason="ok", task_summary="x")])

    with TestClient(main.app) as client:
        response = client.post("/pipeline/plan", json={"prompt": "what is the capital of France"})

    assert response.status_code == 200
    body = response.json()
    assert body == {"compound": False, "intent": "research", "plan": None}


def test_pipeline_plan_endpoint_returns_a_plan_for_a_compound_request(monkeypatch):
    from fastapi.testclient import TestClient

    import backend.main as main
    from backend.pipeline_planner import PipelinePlan, PipelineStep

    monkeypatch.setenv("GROQ_API_KEY_1", "test-key")
    monkeypatch.setattr(main.IntentParser, "parse_intents", lambda _self, _p, _c, _r=False, _g=False: _compound_intents())
    fake_plan = PipelinePlan(steps=[PipelineStep(name="only_step", blueprint="single_agent_loop", intent="research", instructions="do it")], summary="test plan")
    monkeypatch.setattr(main, "plan_pipeline", lambda *_a, **_k: fake_plan)

    with TestClient(main.app) as client:
        response = client.post("/pipeline/plan", json={"prompt": "find companies and email them"})

    assert response.status_code == 200
    body = response.json()
    assert body["compound"] is True
    assert body["plan"]["summary"] == "test plan"
    assert body["plan"]["steps"][0]["name"] == "only_step"


def test_pipeline_plan_endpoint_reports_none_when_the_planner_fails(monkeypatch):
    from fastapi.testclient import TestClient

    import backend.main as main

    monkeypatch.setenv("GROQ_API_KEY_1", "test-key")
    monkeypatch.setattr(main.IntentParser, "parse_intents", lambda _self, _p, _c, _r=False, _g=False: _compound_intents())
    monkeypatch.setattr(main, "plan_pipeline", lambda *_a, **_k: None)

    with TestClient(main.app) as client:
        response = client.post("/pipeline/plan", json={"prompt": "find companies and email them"})

    assert response.status_code == 200
    assert response.json() == {"compound": True, "intent": None, "plan": None}


def test_pipeline_plan_endpoint_includes_the_revision_instruction_when_revising(monkeypatch):
    from fastapi.testclient import TestClient

    import backend.main as main
    from backend.pipeline_planner import PipelinePlan, PipelineStep

    monkeypatch.setenv("GROQ_API_KEY_1", "test-key")
    monkeypatch.setattr(main.IntentParser, "parse_intents", lambda _self, _p, _c, _r=False, _g=False: _compound_intents())
    captured_prompts = []

    def capturing_plan_pipeline(prompt, intents, settings):
        captured_prompts.append(prompt)
        return PipelinePlan(steps=[PipelineStep(name="s", blueprint="single_agent_loop", intent="research", instructions="x")], summary="revised plan")

    monkeypatch.setattr(main, "plan_pipeline", capturing_plan_pipeline)

    with TestClient(main.app) as client:
        response = client.post(
            "/pipeline/plan",
            json={
                "prompt": "find companies and email them",
                "revision_instruction": "only check 3 companies instead of 5",
                "prior_plan": {"summary": "original plan"},
            },
        )

    assert response.status_code == 200
    assert response.json()["plan"]["summary"] == "revised plan"
    assert "only check 3 companies instead of 5" in captured_prompts[0]
    assert "original plan" in captured_prompts[0]


def test_rate_limited_task_has_a_clear_retryable_event(monkeypatch):
    import backend.main as main

    class RateLimitError(Exception):
        pass

    monkeypatch.setattr(main.IntentParser, "parse_intents", lambda *_args, **_kwargs: (_ for _ in ()).throw(RateLimitError("rate_limit_exceeded")))
    emitter = EventEmitter("test-rate-limit")
    main._run_task("Research something", "", "", "", "", "", "", emitter)
    events = []
    while True:
        event = emitter.queue.get()
        if event is None:
            break
        events.append(event.model_dump(mode="json"))

    assert events[0]["data"]["code"] == "llm_rate_limited"
    assert events[0]["data"]["retryable"] is True
    assert events[-1] == {**events[-1], "type": "task_completed", "data": {"status": "failed"}}


def test_github_rate_limit_delay_uses_provider_hint():
    from backend.crew_engine import CrewEngine

    assert CrewEngine._retry_delay(Exception("Please try again in 1.38s.")) == 5
    assert CrewEngine._retry_delay(Exception("Please try again in 99s.")) == 20


def test_call_with_retry_retries_once_on_a_transient_error(monkeypatch):
    import backend.main as main

    monkeypatch.setattr(main.CrewEngine, "_retry_delay", staticmethod(lambda _exc: 0))
    attempts = {"count": 0}

    def flaky():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise Exception("503 - model is overloaded, please try again")
        return "ok"

    result = main._call_with_retry(flaky, EventEmitter("retry-test"), "test step")
    assert result == "ok"
    assert attempts["count"] == 2


def test_call_with_retry_retries_more_than_once_when_needed(monkeypatch):
    import backend.main as main

    monkeypatch.setattr(main.CrewEngine, "_retry_delay", staticmethod(lambda _exc: 0))
    attempts = {"count": 0}

    def flaky():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise Exception("429 rate_limit_exceeded")
        return "ok"

    result = main._call_with_retry(flaky, EventEmitter("retry-test-3"), "test step")
    assert result == "ok"
    assert attempts["count"] == 3


def test_call_with_retry_gives_up_after_exhausting_all_retries(monkeypatch):
    import backend.main as main

    monkeypatch.setattr(main.CrewEngine, "_retry_delay", staticmethod(lambda _exc: 0))
    attempts = {"count": 0}

    def always_rate_limited():
        attempts["count"] += 1
        raise Exception("429 rate_limit_exceeded")

    try:
        main._call_with_retry(always_rate_limited, EventEmitter("retry-test-4"), "test step")
        assert False, "expected the exhausted retryable error to propagate"
    except Exception as exc:
        assert "rate_limit_exceeded" in str(exc)
    assert attempts["count"] == 3  # initial attempt + 2 retries (the default)


def test_call_with_retry_never_retries_a_permanent_error():
    import backend.main as main

    def always_fails():
        raise ValueError("this request will never succeed")

    try:
        main._call_with_retry(always_fails, EventEmitter("retry-test-2"), "test step")
        assert False, "expected the permanent error to propagate"
    except ValueError:
        pass
