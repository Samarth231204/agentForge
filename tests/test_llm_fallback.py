from types import SimpleNamespace

import litellm
import pytest

from backend.llm_fallback import build_llm_candidates, complete_with_fallback, is_json_validation_failure


def _fake_response(content="ok"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _corrupted_error(failed_generation: str | None = '{"name": "browser<|channel|>commentary", "arguments": {"action":"get_text","selector":"body"}}') -> litellm.BadRequestError:
    body = (
        "GroqException - {\"error\":{\"message\":\"Tool call validation failed: attempted to call "
        "tool 'browser<|channel|>commentary' which was not in request.tools\","
        "\"type\":\"invalid_request_error\",\"code\":\"tool_use_failed\","
        f'"failed_generation":"{failed_generation.replace(chr(34), chr(92) + chr(34)) if failed_generation else ""}"}}}}'
    )
    return litellm.BadRequestError(message=body, model="groq/openai/gpt-oss-120b", llm_provider="groq")


def _rate_limit_error(model: str) -> litellm.RateLimitError:
    return litellm.RateLimitError(message=f"Rate limit reached for model `{model}`: rate_limit_exceeded", model=model, llm_provider="groq")


def _json_validation_error() -> litellm.BadRequestError:
    # Reproduces the real error live-observed from pipeline_planner.py:
    # Groq rejects its own generation against the requested JSON schema
    # and raises this instead of returning malformed content.
    body = (
        'GroqException - {"error":{"message":"Failed to validate JSON. Please adjust your prompt. '
        'See \'failed_generation\' for more details.","type":"invalid_request_error",'
        '"code":"json_validate_failed","failed_generation":""}}'
    )
    return litellm.BadRequestError(message=body, model="groq/openai/gpt-oss-120b", llm_provider="groq")


def test_is_json_validation_failure_recognizes_groqs_real_error_shape():
    assert is_json_validation_failure(_json_validation_error()) is True


def test_is_json_validation_failure_is_false_for_unrelated_errors():
    assert is_json_validation_failure(_rate_limit_error("x")) is False
    assert is_json_validation_failure(ValueError("something else entirely")) is False


# --- build_llm_candidates -------------------------------------------------


def test_build_llm_candidates_orders_primary_then_groq_fallbacks():
    settings = SimpleNamespace(groq_model="openai/gpt-oss-20b", groq_api_key="k", openrouter_api_key="", openrouter_models=())
    candidates = build_llm_candidates(settings)
    models = [model for model, _key in candidates]
    assert models == ["groq/openai/gpt-oss-20b", "groq/openai/gpt-oss-120b", "groq/qwen/qwen3.8-27b"]
    assert all(key == "k" for _model, key in candidates)


def test_build_llm_candidates_deduplicates_primary_if_already_a_fallback():
    settings = SimpleNamespace(groq_model="openai/gpt-oss-120b", groq_api_key="k", openrouter_api_key="", openrouter_models=())
    models = [model for model, _key in build_llm_candidates(settings)]
    assert models == ["groq/openai/gpt-oss-120b", "groq/openai/gpt-oss-20b", "groq/qwen/qwen3.8-27b"]


def test_build_llm_candidates_appends_openrouter_only_when_api_key_is_set():
    without_key = SimpleNamespace(groq_model="openai/gpt-oss-20b", groq_api_key="k", openrouter_api_key="", openrouter_models=("some/model",))
    assert "openrouter" not in str(build_llm_candidates(without_key))

    without_models = SimpleNamespace(groq_model="openai/gpt-oss-20b", groq_api_key="k", openrouter_api_key="or-key", openrouter_models=())
    assert "openrouter" not in str(build_llm_candidates(without_models))

    fully_configured = SimpleNamespace(groq_model="openai/gpt-oss-20b", groq_api_key="k", openrouter_api_key="or-key", openrouter_models=("some/model",))
    candidates = build_llm_candidates(fully_configured)
    assert candidates[-1] == ("openrouter/some/model", "or-key")


def test_build_llm_candidates_appends_every_configured_openrouter_model_in_order():
    settings = SimpleNamespace(
        groq_model="openai/gpt-oss-20b",
        groq_api_key="k",
        openrouter_api_key="or-key",
        openrouter_models=("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "cohere/north-mini-code:free", "inclusionai/ling-3.0-flash-fin:free"),
    )
    candidates = build_llm_candidates(settings)
    openrouter_candidates = [c for c in candidates if c[0].startswith("openrouter/")]
    assert openrouter_candidates == [
        ("openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "or-key"),
        ("openrouter/cohere/north-mini-code:free", "or-key"),
        ("openrouter/inclusionai/ling-3.0-flash-fin:free", "or-key"),
    ]


# --- complete_with_fallback: cross-candidate fallback ---------------------


def test_complete_with_fallback_advances_to_next_candidate_on_rate_limit(monkeypatch):
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "groq/model-a":
            raise _rate_limit_error("model-a")
        return _fake_response(content="answered by model-b")

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", fake_completion)
    result = complete_with_fallback([("groq/model-a", "k1"), ("groq/model-b", "k2")], messages=[])
    assert result.choices[0].message.content == "answered by model-b"
    assert calls == ["groq/model-a", "groq/model-b"]


def test_complete_with_fallback_raises_after_every_candidate_exhausted(monkeypatch):
    def fake_completion(**kwargs):
        raise _rate_limit_error(kwargs["model"])

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", fake_completion)
    with pytest.raises(litellm.RateLimitError):
        complete_with_fallback([("groq/model-a", "k1"), ("groq/model-b", "k2")], messages=[])


def test_complete_with_fallback_does_not_advance_on_a_permanent_error(monkeypatch):
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs["model"])
        raise litellm.AuthenticationError(message="invalid API key", model=kwargs["model"], llm_provider="groq")

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", fake_completion)
    with pytest.raises(litellm.AuthenticationError):
        complete_with_fallback([("groq/model-a", "k1"), ("groq/model-b", "k2")], messages=[])
    assert calls == ["groq/model-a"]  # never tried model-b for a permanent error


def test_complete_with_fallback_advances_past_an_openrouter_style_resource_exhausted_error(monkeypatch):
    """Reproduces a real error hit live against nvidia/nemotron-3-nano-omni
    via OpenRouter: 'Upstream error from Nvidia: ResourceExhausted: Worker
    local total request limit reached (16/16)' — no underscore, not a
    litellm.RateLimitError instance, and didn't match any prior marker, so
    complete_with_fallback() raised instead of advancing to the next
    candidate. is_rate_limited() must recognize this shape too."""
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "openrouter/model-a":
            raise litellm.APIError(
                status_code=500,
                message="OpenrouterException - Upstream error from Nvidia: ResourceExhausted: Worker local total request limit reached (16/16)",
                model="openrouter/model-a",
                llm_provider="openrouter",
            )
        return _fake_response(content="answered by model-b")

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", fake_completion)
    result = complete_with_fallback([("openrouter/model-a", "k1"), ("openrouter/model-b", "k2")], messages=[])
    assert result.choices[0].message.content == "answered by model-b"
    assert calls == ["openrouter/model-a", "openrouter/model-b"]


def test_complete_with_fallback_requires_at_least_one_candidate():
    with pytest.raises(ValueError, match="at least one candidate"):
        complete_with_fallback([], messages=[])


# --- per-candidate corruption recovery/retry (moved from test_agent_loop) -


def test_salvages_a_corrupted_tool_call_without_retrying(monkeypatch):
    calls = {"count": 0}

    def fake_completion(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise _corrupted_error()
        tool_messages = [m for m in kwargs["messages"] if m["role"] == "tool"]
        assert tool_messages == [{"role": "tool", "tool_call_id": "recovered-corrupted-call", "content": "read: body content"}]
        return _fake_response(content="done")

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", fake_completion)
    # This test exercises complete_with_fallback end-to-end via agent_loop's
    # tool-execution contract, but at this layer we only need to confirm the
    # raw completion call recovers without a second identical request — the
    # full tool-loop behavior is covered in test_agent_loop.py.
    result = complete_with_fallback([("groq/x", "k")], messages=[{"role": "user", "content": "hi"}], tools=[])
    assert result.choices[0].message.tool_calls[0].function.name == "browser"
    assert calls["count"] == 1


def test_retries_twice_when_corruption_happens_twice_in_a_row(monkeypatch):
    # Real production case: the harmony corruption hit two calls in a row
    # with no failed_generation to salvage, so blind retry had to kick in twice.
    calls = {"count": 0}

    def fake_completion(**_kwargs):
        calls["count"] += 1
        if calls["count"] < 3:
            raise _corrupted_error(failed_generation=None)
        return _fake_response(content="recovered on third attempt")

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", fake_completion)
    result = complete_with_fallback([("groq/x", "k")], messages=[])
    assert result.choices[0].message.content == "recovered on third attempt"
    assert calls["count"] == 3


def test_gives_up_after_three_consecutive_corruptions_on_one_candidate(monkeypatch):
    calls = {"count": 0}

    def fake_completion(**_kwargs):
        calls["count"] += 1
        raise _corrupted_error(failed_generation=None)

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", fake_completion)
    with pytest.raises(litellm.BadRequestError, match="not in request.tools"):
        complete_with_fallback([("groq/x", "k")], messages=[])
    assert calls["count"] == 3


def test_does_not_retry_a_genuinely_permanent_bad_request(monkeypatch):
    def fake_completion(**_kwargs):
        raise litellm.BadRequestError(message="invalid API key", model="groq/x", llm_provider="groq")

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", fake_completion)
    with pytest.raises(litellm.BadRequestError, match="invalid API key"):
        complete_with_fallback([("groq/x", "k")], messages=[])
