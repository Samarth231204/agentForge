from backend.intent_parser import Intent
from backend.event_emitter import EventEmitter


def test_json_task_endpoint_returns_ordered_events(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    from fastapi.testclient import TestClient

    import backend.main as main

    monkeypatch.setattr(
        main.IntentParser,
        "parse",
        lambda _self, _prompt, _context: Intent(
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
    main._run_task("Research something", "", "", "", emitter)
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
