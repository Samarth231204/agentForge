import time
from types import SimpleNamespace

from backend.agent_loop import ToolSpec
from backend.blueprints import DEFAULT_FANOUT_CAP, Stage, run_parallel_fanout, run_sequential_stages, run_single_agent_loop

_SETTINGS = SimpleNamespace(groq_api_key="not-a-real-key", groq_model="openai/gpt-oss-120b", openrouter_api_key="", openrouter_models=())


def _fake_response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))])


# --- single_agent_loop ------------------------------------------------------


def test_run_single_agent_loop_returns_the_models_plain_answer(monkeypatch):
    monkeypatch.setattr("backend.llm_fallback.litellm.completion", lambda **_kwargs: _fake_response("final answer"))
    result = run_single_agent_loop("system", "user", tools=[], settings=_SETTINGS)
    assert result == "final answer"


def test_run_single_agent_loop_executes_a_real_tool_call(monkeypatch):
    calls = {"count": 0}

    def fake_completion(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            tool_call = SimpleNamespace(id="1", function=SimpleNamespace(name="echo", arguments='{"text": "hi"}'))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tool_call]))])
        return _fake_response("done")

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", fake_completion)
    tool = ToolSpec(name="echo", description="Echoes.", parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, execute=lambda text="": f"echoed: {text}")
    result = run_single_agent_loop("system", "user", tools=[tool], settings=_SETTINGS)
    assert result == "done"
    assert calls["count"] == 2


# --- sequential_stages -------------------------------------------------------


def test_run_sequential_stages_chains_prior_outputs_into_later_stages(monkeypatch):
    calls = []

    def fake_completion(*, model, api_key, temperature, messages):
        calls.append(messages)
        stage_index = len(calls)
        return _fake_response({1: "BRIEF", 2: "DRAFT", 3: "FINAL"}[stage_index])

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", fake_completion)

    stages = [
        Stage(name="Planner", system_prompt="plan", build_user_prompt=lambda prompt, prior: f"Request: {prompt}"),
        Stage(name="Writer", system_prompt="write", build_user_prompt=lambda prompt, prior: f"Brief: {prior[0]}"),
        Stage(name="Reviewer", system_prompt="review", build_user_prompt=lambda prompt, prior: f"Brief: {prior[0]}, Draft: {prior[1]}"),
    ]
    result = run_sequential_stages(stages, "Write an email", _SETTINGS)

    assert len(calls) == 3
    assert "Write an email" in calls[0][1]["content"]
    assert "BRIEF" in calls[1][1]["content"]
    assert "BRIEF" in calls[2][1]["content"] and "DRAFT" in calls[2][1]["content"]
    assert result == "FINAL"


def test_run_sequential_stages_with_a_single_stage_returns_its_output():
    def fake_completion(*, model, api_key, temperature, messages):
        return _fake_response("only stage")

    import backend.llm_fallback as llm_fallback

    stages = [Stage(name="Only", system_prompt="s", build_user_prompt=lambda prompt, prior: prompt)]
    orig = llm_fallback.litellm.completion
    llm_fallback.litellm.completion = fake_completion
    try:
        result = run_sequential_stages(stages, "hello", _SETTINGS)
    finally:
        llm_fallback.litellm.completion = orig
    assert result == "only stage"


# --- parallel_fanout ----------------------------------------------------------


def test_run_parallel_fanout_gives_each_item_its_own_fresh_tools(monkeypatch):
    monkeypatch.setattr("backend.llm_fallback.litellm.completion", lambda **_kwargs: _fake_response("ok"))

    factory_calls = []

    def tool_factory():
        instance = object()
        factory_calls.append(instance)
        return []

    results = run_parallel_fanout(
        system_prompt="system",
        items=["a", "b", "c"],
        build_user_prompt=lambda item: f"visit {item}",
        tool_factory=tool_factory,
        settings=_SETTINGS,
    )

    assert results == ["ok", "ok", "ok"]
    assert len(factory_calls) == 3
    assert len(set(id(c) for c in factory_calls)) == 3  # every branch got a distinct tool-factory instance, never shared


def test_run_parallel_fanout_caps_at_the_default_fanout_limit(monkeypatch):
    monkeypatch.setattr("backend.llm_fallback.litellm.completion", lambda **_kwargs: _fake_response("ok"))
    items = [f"item-{i}" for i in range(DEFAULT_FANOUT_CAP + 10)]

    results = run_parallel_fanout(
        system_prompt="system",
        items=items,
        build_user_prompt=lambda item: item,
        tool_factory=lambda: [],
        settings=_SETTINGS,
    )

    assert len(results) == DEFAULT_FANOUT_CAP


def test_run_parallel_fanout_returns_results_in_the_same_order_as_items(monkeypatch):
    def fake_completion(*, model, api_key, temperature, messages, **_kwargs):
        # Echo back whatever the per-item user prompt said, to confirm
        # ordering survives concurrent execution.
        user_message = next(m["content"] for m in messages if m["role"] == "user")
        return _fake_response(user_message)

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", fake_completion)
    items = ["alpha", "beta", "gamma", "delta"]

    results = run_parallel_fanout(
        system_prompt="system",
        items=items,
        build_user_prompt=lambda item: item,
        tool_factory=lambda: [],
        settings=_SETTINGS,
    )
    assert results == items


def test_run_parallel_fanout_with_no_items_returns_an_empty_list():
    results = run_parallel_fanout(
        system_prompt="system",
        items=[],
        build_user_prompt=lambda item: item,
        tool_factory=lambda: [],
        settings=_SETTINGS,
    )
    assert results == []


def test_run_parallel_fanout_actually_overlaps_in_wall_clock_time(monkeypatch):
    # Proves real concurrency, not just "no crash": if branches ran
    # sequentially, 5 branches * 0.2s each would take >=1.0s; run
    # concurrently, it should take close to a single branch's duration.
    def fake_completion(**_kwargs):
        time.sleep(0.2)
        return _fake_response("ok")

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", fake_completion)
    items = [f"item-{i}" for i in range(5)]

    start = time.monotonic()
    results = run_parallel_fanout(
        system_prompt="system",
        items=items,
        build_user_prompt=lambda item: item,
        tool_factory=lambda: [],
        settings=_SETTINGS,
    )
    elapsed = time.monotonic() - start

    assert results == ["ok"] * 5
    assert elapsed < 0.6  # well under the 1.0s a sequential run would take
