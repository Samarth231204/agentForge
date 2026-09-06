from types import SimpleNamespace

from backend.agent_loop import ToolSpec, run_agent

_CANDIDATES = [("groq/x", "k")]


def _fake_tool_call(call_id, name, arguments_json):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments_json))


def _fake_response(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])


def test_run_agent_returns_immediately_when_the_model_gives_a_plain_answer(monkeypatch):
    import backend.llm_fallback as llm_fallback

    monkeypatch.setattr(llm_fallback.litellm, "completion", lambda **_kwargs: _fake_response(content="final answer"))
    result = run_agent("system", "user", tools=[], candidates=_CANDIDATES)
    assert result == "final answer"


def test_run_agent_executes_a_tool_call_and_feeds_the_result_back(monkeypatch):
    import backend.llm_fallback as llm_fallback

    calls = {"count": 0}

    def fake_completion(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return _fake_response(tool_calls=[_fake_tool_call("1", "echo", '{"text": "hi"}')])
        # Second call: confirm the tool's result reached the model as a tool message.
        tool_messages = [m for m in kwargs["messages"] if m["role"] == "tool"]
        assert tool_messages == [{"role": "tool", "tool_call_id": "1", "content": "echoed: hi"}]
        return _fake_response(content="done")

    monkeypatch.setattr(llm_fallback.litellm, "completion", fake_completion)
    tool = ToolSpec(name="echo", description="Echoes text.", parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, execute=lambda text="": f"echoed: {text}")
    result = run_agent("system", "user", tools=[tool], candidates=_CANDIDATES)
    assert result == "done"
    assert calls["count"] == 2


def test_run_agent_feeds_tool_errors_back_instead_of_crashing(monkeypatch):
    import backend.llm_fallback as llm_fallback

    calls = {"count": 0}

    def fake_completion(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return _fake_response(tool_calls=[_fake_tool_call("1", "breaks", "{}")])
        tool_messages = [m for m in kwargs["messages"] if m["role"] == "tool"]
        assert tool_messages[0]["content"] == "Error: boom"
        return _fake_response(content="recovered")

    def broken_execute():
        raise ValueError("boom")

    monkeypatch.setattr(llm_fallback.litellm, "completion", fake_completion)
    tool = ToolSpec(name="breaks", description="Always fails.", parameters={"type": "object", "properties": {}, "required": []}, execute=broken_execute)
    result = run_agent("system", "user", tools=[tool], candidates=_CANDIDATES)
    assert result == "recovered"


def test_run_agent_terminates_when_the_model_calls_final_answer(monkeypatch):
    import backend.llm_fallback as llm_fallback

    calls = {"count": 0}

    def fake_completion(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            # The model must have been offered a final_answer tool alongside its own.
            names = [t["function"]["name"] for t in kwargs["tools"]]
            assert "loop" in names and "final_answer" in names
            return _fake_response(tool_calls=[_fake_tool_call("1", "loop", "{}")])
        return _fake_response(tool_calls=[_fake_tool_call("2", "final_answer", '{"answer": "all done"}')])

    monkeypatch.setattr(llm_fallback.litellm, "completion", fake_completion)
    tool = ToolSpec(name="loop", description="A regular tool.", parameters={"type": "object", "properties": {}, "required": []}, execute=lambda: "did something")
    result = run_agent("system", "user", tools=[tool], candidates=_CANDIDATES)
    assert result == "all done"
    assert calls["count"] == 2


def test_run_agent_gives_up_gracefully_after_exhausting_iterations(monkeypatch):
    import backend.llm_fallback as llm_fallback

    def fake_completion(**_kwargs):
        # The model never calls final_answer and keeps looping forever.
        return _fake_response(tool_calls=[_fake_tool_call("1", "loop", "{}")])

    monkeypatch.setattr(llm_fallback.litellm, "completion", fake_completion)
    tool = ToolSpec(name="loop", description="Never resolves.", parameters={"type": "object", "properties": {}, "required": []}, execute=lambda: "still going")
    result = run_agent("system", "user", tools=[tool], candidates=_CANDIDATES, max_iterations=3, label="Tester")
    assert "Tester could not finish within its iteration budget" in result
    # The trace should show what the agent actually did, for diagnosing why.
    assert "loop({}) -> still going" in result
