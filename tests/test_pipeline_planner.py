import json
from types import SimpleNamespace

import pytest

from backend.blueprints import DEFAULT_FANOUT_CAP
from backend.intent_parser import Intent
from backend.pipeline_planner import PipelinePlan, PipelineStep, plan_pipeline

_SETTINGS = SimpleNamespace(groq_api_key="not-a-real-key", groq_model="openai/gpt-oss-120b", openrouter_api_key="", openrouter_models=())

_INTENTS = [
    Intent(intent="research", confidence=0.9, reason="find companies", task_summary="Find AI companies hiring"),
    Intent(intent="send_email", confidence=0.85, reason="email each one", task_summary="Email each company's talent team"),
]


def _fake_response(content: dict) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(content)))])


def _valid_plan_json(fanout_count: int = 3) -> dict:
    return {
        "steps": [
            {"name": "find_companies", "blueprint": "single_agent_loop", "intent": "research", "tools": ["web_search"], "instructions": "Find AI companies hiring right now.", "depends_on": [], "fanout_count": 1},
            {"name": "email_each", "blueprint": "parallel_fanout", "intent": "send_email", "tools": ["browser", "email"], "instructions": "Visit each company's site and email their talent team.", "depends_on": ["find_companies"], "fanout_count": fanout_count},
        ],
        "summary": "Research AI companies, then email each one's talent team.",
    }


def test_plan_pipeline_returns_a_valid_plan_from_model_json(monkeypatch):
    monkeypatch.setattr("backend.llm_fallback.litellm.completion", lambda **_kwargs: _fake_response(_valid_plan_json()))
    plan = plan_pipeline("research AI companies and email their talent teams", _INTENTS, _SETTINGS)
    assert plan is not None
    assert len(plan.steps) == 2
    assert plan.steps[0].name == "find_companies"
    assert plan.steps[1].depends_on == ["find_companies"]
    assert plan.summary


def test_plan_pipeline_caps_fanout_count_at_the_default_limit(monkeypatch):
    monkeypatch.setattr("backend.llm_fallback.litellm.completion", lambda **_kwargs: _fake_response(_valid_plan_json(fanout_count=999)))
    plan = plan_pipeline("...", _INTENTS, _SETTINGS)
    assert plan is not None
    fanout_step = next(s for s in plan.steps if s.blueprint == "parallel_fanout")
    assert fanout_step.fanout_count == DEFAULT_FANOUT_CAP


def test_plan_pipeline_drops_unknown_tool_names(monkeypatch):
    plan_json = _valid_plan_json()
    plan_json["steps"][0]["tools"] = ["web_search", "made_up_tool"]
    monkeypatch.setattr("backend.llm_fallback.litellm.completion", lambda **_kwargs: _fake_response(plan_json))
    plan = plan_pipeline("...", _INTENTS, _SETTINGS)
    assert plan is not None
    assert plan.steps[0].tools == ["web_search"]


def test_plan_pipeline_rejects_a_step_that_depends_on_an_unknown_step(monkeypatch):
    plan_json = _valid_plan_json()
    plan_json["steps"][1]["depends_on"] = ["step_that_does_not_exist"]
    monkeypatch.setattr("backend.llm_fallback.litellm.completion", lambda **_kwargs: _fake_response(plan_json))
    plan = plan_pipeline("...", _INTENTS, _SETTINGS)
    # Invalid on every attempt/candidate (only one candidate configured here) -> gives up -> None.
    assert plan is None


def test_plan_pipeline_retries_after_groqs_own_json_schema_rejection(monkeypatch):
    """Real bug found live: Groq's response_format={"type": "json_object"}
    validation can reject a generation outright and raise a BadRequestError
    (code json_validate_failed) instead of returning malformed content —
    this must be retried like any other malformed-JSON reply, not
    propagate as an unhandled exception."""
    import litellm

    attempts = {"count": 0}

    def flaky(*, model, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            body = (
                'GroqException - {"error":{"message":"Failed to validate JSON.","type":"invalid_request_error",'
                '"code":"json_validate_failed","failed_generation":""}}'
            )
            raise litellm.BadRequestError(message=body, model=model, llm_provider="groq")
        return _fake_response(_valid_plan_json())

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", flaky)
    plan = plan_pipeline("...", _INTENTS, _SETTINGS)
    assert plan is not None
    assert attempts["count"] == 2


def test_plan_pipeline_returns_none_when_every_candidate_fails_to_produce_valid_json(monkeypatch):
    monkeypatch.setattr("backend.llm_fallback.litellm.completion", lambda **_kwargs: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))]))
    plan = plan_pipeline("...", _INTENTS, _SETTINGS)
    assert plan is None


def test_plan_pipeline_advances_to_the_next_candidate_model_on_a_provider_error(monkeypatch):
    import litellm

    models_tried = []

    def flaky(*, model, **_kwargs):
        models_tried.append(model)
        if len(models_tried) == 1:
            raise litellm.RateLimitError(message="rate_limit_exceeded", model=model, llm_provider="groq")
        return _fake_response(_valid_plan_json())

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", flaky)
    plan = plan_pipeline("...", _INTENTS, _SETTINGS)
    assert plan is not None
    assert len(models_tried) == 2
    assert models_tried[0] != models_tried[1]


def test_pipeline_step_fanout_count_defaults_to_one():
    step = PipelineStep(name="x", blueprint="single_agent_loop", intent="research", instructions="do the thing")
    assert step.fanout_count == 1


def test_pipeline_plan_requires_at_least_one_step():
    with pytest.raises(Exception):
        PipelinePlan(steps=[], summary="empty plan")
