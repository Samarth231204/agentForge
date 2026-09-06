from backend.intent_parser import Intent
from backend.event_emitter import EventEmitter


def test_json_task_endpoint_returns_ordered_events(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY_1", "test-key")
    from fastapi.testclient import TestClient

    import backend.main as main

    monkeypatch.setattr(
        main.IntentParser,
        "parse",
        lambda _self, _prompt, _context, _has_repo_context=False: Intent(
            intent="unsupported", confidence=1, reason="test", task_summary="test"
        ),
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


def test_rate_limited_task_has_a_clear_retryable_event(monkeypatch):
    import backend.main as main

    class RateLimitError(Exception):
        pass

    monkeypatch.setattr(main.IntentParser, "parse", lambda *_args: (_ for _ in ()).throw(RateLimitError("rate_limit_exceeded")))
    emitter = EventEmitter("test-rate-limit")
    main._run_task("Research something", "", "", "", "", emitter)
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
