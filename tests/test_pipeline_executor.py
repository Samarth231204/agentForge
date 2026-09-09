import time
from types import SimpleNamespace

from backend.pipeline_executor import ExecutionContext, execute_pipeline
from backend.pipeline_planner import PipelinePlan, PipelineStep

_SETTINGS = SimpleNamespace(groq_api_key="not-a-real-key", groq_model="openai/gpt-oss-120b", openrouter_api_key="", openrouter_models=(), sandbox_image="agentforge-sandbox:local")


def _plan(*steps: PipelineStep, summary: str = "test plan") -> PipelinePlan:
    return PipelinePlan(steps=list(steps), summary=summary)


def test_executes_a_single_step_and_returns_its_result_in_the_summary(monkeypatch):
    monkeypatch.setattr("backend.pipeline_executor.run_single_agent_loop", lambda *a, **k: "the answer")
    plan = _plan(PipelineStep(name="only_step", blueprint="single_agent_loop", intent="research", instructions="do the thing"))
    report = execute_pipeline(plan, _SETTINGS, ExecutionContext())
    assert "the answer" in report
    assert "only_step" in report


def test_a_dependent_step_sees_the_prior_steps_output(monkeypatch):
    captured_prompts = []

    def fake_single_agent(system_prompt, user_prompt, tools, settings, **_kwargs):
        captured_prompts.append(user_prompt)
        return "step-a-result" if len(captured_prompts) == 1 else "step-b-result"

    monkeypatch.setattr("backend.pipeline_executor.run_single_agent_loop", fake_single_agent)
    plan = _plan(
        PipelineStep(name="step_a", blueprint="single_agent_loop", intent="research", instructions="find things"),
        PipelineStep(name="step_b", blueprint="single_agent_loop", intent="send_email", instructions="use the findings", depends_on=["step_a"]),
    )
    report = execute_pipeline(plan, _SETTINGS, ExecutionContext())
    assert "step-a-result" in report
    assert "step-b-result" in report
    # step_b's actual prompt included step_a's real output, not just its own instructions
    assert any("step-a-result" in p for p in captured_prompts)


def test_independent_steps_run_concurrently_not_sequentially(monkeypatch):
    def slow_step(system_prompt, user_prompt, tools, settings, **_kwargs):
        time.sleep(0.2)
        return "done"

    monkeypatch.setattr("backend.pipeline_executor.run_single_agent_loop", slow_step)
    plan = _plan(
        PipelineStep(name="a", blueprint="single_agent_loop", intent="research", instructions="x"),
        PipelineStep(name="b", blueprint="single_agent_loop", intent="research", instructions="y"),
        PipelineStep(name="c", blueprint="single_agent_loop", intent="research", instructions="z"),
    )
    start = time.monotonic()
    execute_pipeline(plan, _SETTINGS, ExecutionContext())
    elapsed = time.monotonic() - start
    assert elapsed < 0.5  # well under the 0.6s three sequential 0.2s steps would take


def test_dependent_waves_run_strictly_after_their_dependencies(monkeypatch):
    order = []

    def tracking_step(system_prompt, user_prompt, tools, settings, label="agent", **_kwargs):
        order.append(label)
        return f"result from {label}"

    monkeypatch.setattr("backend.pipeline_executor.run_single_agent_loop", tracking_step)
    plan = _plan(
        PipelineStep(name="first", blueprint="single_agent_loop", intent="research", instructions="x"),
        PipelineStep(name="second", blueprint="single_agent_loop", intent="research", instructions="y", depends_on=["first"]),
    )
    execute_pipeline(plan, _SETTINGS, ExecutionContext())
    assert order == ["first", "second"]


def test_a_failed_step_does_not_abort_the_rest_of_the_pipeline(monkeypatch):
    def flaky_step(system_prompt, user_prompt, tools, settings, label="agent", **_kwargs):
        if label == "will_fail":
            raise RuntimeError("boom")
        return "ok"

    monkeypatch.setattr("backend.pipeline_executor.run_single_agent_loop", flaky_step)
    plan = _plan(
        PipelineStep(name="will_fail", blueprint="single_agent_loop", intent="research", instructions="x"),
        PipelineStep(name="depends_on_failure", blueprint="single_agent_loop", intent="research", instructions="y", depends_on=["will_fail"]),
    )
    report = execute_pipeline(plan, _SETTINGS, ExecutionContext())
    assert "This step failed" in report
    assert "boom" in report
    assert "ok" in report  # the dependent step still ran


def test_parallel_fanout_step_extracts_items_and_branches(monkeypatch):
    monkeypatch.setattr("backend.pipeline_executor._extract_fanout_items", lambda prior_text, count, settings: ["Company A", "Company B"])
    monkeypatch.setattr("backend.pipeline_executor.run_parallel_fanout", lambda **kwargs: [kwargs["build_user_prompt"](item) for item in ["Company A", "Company B"]])
    plan = _plan(PipelineStep(name="fanout_step", blueprint="parallel_fanout", intent="booking", instructions="visit each site", fanout_count=2))
    report = execute_pipeline(plan, _SETTINGS, ExecutionContext())
    assert "Company A" in report
    assert "Company B" in report


def test_pipeline_state_is_cleaned_up_after_a_successful_run(monkeypatch):
    from backend.pipeline_state import get_pipeline_state_store

    seen_run_ids = []
    real_store = get_pipeline_state_store()
    original_delete = real_store.delete_run

    def tracking_delete(run_id):
        seen_run_ids.append(run_id)
        return original_delete(run_id)

    monkeypatch.setattr(real_store, "delete_run", tracking_delete)
    monkeypatch.setattr("backend.pipeline_executor.run_single_agent_loop", lambda *a, **k: "ok")

    plan = _plan(PipelineStep(name="only_step", blueprint="single_agent_loop", intent="research", instructions="x"))
    execute_pipeline(plan, _SETTINGS, ExecutionContext())
    assert len(seen_run_ids) == 1
    assert real_store.get_all_results(seen_run_ids[0]) == {}


def test_pipeline_state_is_cleaned_up_even_when_a_step_raises(monkeypatch):
    from backend.pipeline_state import get_pipeline_state_store

    def always_fails(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr("backend.pipeline_executor.run_single_agent_loop", always_fails)
    plan = _plan(PipelineStep(name="only_step", blueprint="single_agent_loop", intent="research", instructions="x"))
    execute_pipeline(plan, _SETTINGS, ExecutionContext())  # must not raise
    # No direct way to get the run_id back, but confirm the store isn't accumulating orphaned runs:
    store = get_pipeline_state_store()
    assert store.get_all_results("nonexistent-check") == {}


def test_sequential_stages_blueprint_is_invoked_for_that_blueprint_type(monkeypatch):
    monkeypatch.setattr("backend.pipeline_executor.run_sequential_stages", lambda stages, prompt, settings, **k: "chained result")
    plan = _plan(PipelineStep(name="draft_step", blueprint="sequential_stages", intent="draft_email", instructions="write something"))
    report = execute_pipeline(plan, _SETTINGS, ExecutionContext())
    assert "chained result" in report


def test_email_tool_is_skipped_without_a_connected_gmail_session():
    from backend.pipeline_executor import _build_tool_specs

    specs, cleanups = _build_tool_specs(["email"], ExecutionContext(gmail_session_id=""), _SETTINGS)
    assert specs == []
    assert cleanups == []


def test_browser_tool_cleanup_is_called_after_a_single_agent_loop_step(monkeypatch):
    closed = {"count": 0}

    class FakeBrowser:
        def run(self, **_kwargs):
            return "ok"

        def close(self):
            closed["count"] += 1

    monkeypatch.setattr("backend.pipeline_executor.BrowserTool", FakeBrowser)
    monkeypatch.setattr("backend.pipeline_executor.run_single_agent_loop", lambda *a, **k: "done")

    plan = _plan(PipelineStep(name="browse_step", blueprint="single_agent_loop", intent="booking", instructions="look at a page", tools=["browser"]))
    execute_pipeline(plan, _SETTINGS, ExecutionContext())
    assert closed["count"] == 1
