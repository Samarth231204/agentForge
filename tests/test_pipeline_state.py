from backend.pipeline_state import InMemoryPipelineStateStore, get_pipeline_state_store


def test_set_get_step_result_round_trip():
    store = InMemoryPipelineStateStore()
    assert store.get_step_result("run-1", "research") is None

    store.set_step_result("run-1", "research", "found 3 companies")
    assert store.get_step_result("run-1", "research") == "found 3 companies"


def test_step_results_support_structured_values_not_just_strings():
    store = InMemoryPipelineStateStore()
    store.set_step_result("run-1", "fanout", ["a@x.com", "b@x.com", "c@x.com"])
    assert store.get_step_result("run-1", "fanout") == ["a@x.com", "b@x.com", "c@x.com"]

    store.set_step_result("run-1", "metadata", {"company": "Acme", "confidence": 0.9})
    assert store.get_step_result("run-1", "metadata") == {"company": "Acme", "confidence": 0.9}


def test_get_all_results_returns_every_step_for_a_run():
    store = InMemoryPipelineStateStore()
    store.set_step_result("run-1", "step-a", "output-a")
    store.set_step_result("run-1", "step-b", "output-b")
    assert store.get_all_results("run-1") == {"step-a": "output-a", "step-b": "output-b"}


def test_runs_are_isolated_from_each_other():
    store = InMemoryPipelineStateStore()
    store.set_step_result("run-a", "step", "from run a")
    store.set_step_result("run-b", "step", "from run b")
    assert store.get_step_result("run-a", "step") == "from run a"
    assert store.get_step_result("run-b", "step") == "from run b"
    store.delete_run("run-a")
    assert store.get_step_result("run-b", "step") == "from run b"


def test_delete_run_removes_every_step_for_that_run():
    store = InMemoryPipelineStateStore()
    store.set_step_result("run-1", "step-a", "output-a")
    store.set_step_result("run-1", "step-b", "output-b")
    store.delete_run("run-1")
    assert store.get_all_results("run-1") == {}
    assert store.get_step_result("run-1", "step-a") is None


def test_delete_run_on_a_run_that_never_existed_does_not_raise():
    store = InMemoryPipelineStateStore()
    store.delete_run("never-existed")  # must not raise


def test_get_pipeline_state_store_returns_a_process_wide_singleton():
    assert get_pipeline_state_store() is get_pipeline_state_store()
