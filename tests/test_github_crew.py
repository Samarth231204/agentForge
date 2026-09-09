from types import SimpleNamespace

from backend.templates import github_crew


class FakeGithub:
    def __init__(self, list_files_result="README.md", read_file_result="hello"):
        self._list_files_result = list_files_result
        self._read_file_result = read_file_result
        self.calls = []

    def _run(self, action, **kwargs):
        self.calls.append((action, kwargs))
        if action == "clone":
            return "Cloning..."
        if action == "list_files":
            return self._list_files_result
        if action == "read_file":
            return self._read_file_result
        if action == "write_file":
            return ""
        if action == "push_branch":
            return "pushed"
        if action == "create_pull_request":
            return "Pull request created: https://github.com/example/project/pull/1"
        raise AssertionError(f"unexpected action {action}")


def test_truncated_plan_reports_the_actual_cause_instead_of_raw_json(monkeypatch):
    def fake_completion(**_kwargs):
        # A plan cut off mid-generation: valid JSON prefix, no closing braces.
        truncated = '{"summary":"big change","files":[{"path":"summary.md","content":"# Title\\n\\nSome long content that never'
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=truncated), finish_reason="length")])

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", fake_completion)
    github = FakeGithub()
    settings = SimpleNamespace(groq_api_key="not-a-real-key", groq_model="openai/gpt-oss-120b", openrouter_api_key="", openrouter_models=())

    result = github_crew._run_workflow("write a big summary", github, "agentforge/test1234", settings)

    assert "too large to generate in a single step" in result
    assert "smaller" in result
    # No write_file/push_branch call should have happened for an unusable plan.
    assert not any(action == "write_file" for action, _ in github.calls)


def test_gitignored_file_produces_a_specific_actionable_message(monkeypatch):
    import json

    plan = json.dumps({"summary": "add a summary doc", "files": [{"path": "summary.md", "content": "# Summary"}], "pr_title": "t", "pr_body": "b"})

    def fake_completion(**_kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=plan), finish_reason="stop")])

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", fake_completion)

    class GitignoreGithub(FakeGithub):
        def _run(self, action, **kwargs):
            if action == "push_branch":
                self.calls.append((action, kwargs))
                return (
                    "No changes to push.\n--- git status (including gitignored) ---\n!! summary.md\n"
                    "--- git log -1 ---\n89c876f feat: enforce non-default SECRET_KEY"
                )
            return super()._run(action, **kwargs)

    github = GitignoreGithub()
    settings = SimpleNamespace(groq_api_key="not-a-real-key", groq_model="openai/gpt-oss-120b", openrouter_api_key="", openrouter_models=())

    result = github_crew._run_workflow("write a summary", github, "agentforge/test1234", settings)

    assert "summary.md" in result
    assert ".gitignore excludes" in result
    assert not any(action == "create_pull_request" for action, _ in github.calls)


def test_genuinely_malformed_plan_still_shows_the_raw_response(monkeypatch):
    def fake_completion(**_kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="not json at all"), finish_reason="stop")])

    monkeypatch.setattr("backend.llm_fallback.litellm.completion", fake_completion)
    github = FakeGithub()
    settings = SimpleNamespace(groq_api_key="not-a-real-key", groq_model="openai/gpt-oss-120b", openrouter_api_key="", openrouter_models=())

    result = github_crew._run_workflow("do something", github, "agentforge/test1234", settings)

    assert "could not determine what changes to make" in result
    assert "not json at all" in result
