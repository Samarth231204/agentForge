import subprocess

import pytest

pytest.importorskip("crewai", reason="requires the CrewAI runtime declared by AgentForge")

from backend.tools.github_tool import GithubTool, GithubToolInput, SandboxUnavailable, create_github_operation_tools


def _tool() -> GithubTool:
    return GithubTool(
        repo_url="https://github.com/example/project.git",
        token="github_pat_not_a_real_token",
        session_id="12345678-1234-1234-1234-123456789abc",
    )


def test_rejects_path_traversal_and_unsafe_commands():
    with pytest.raises(ValueError, match="safe relative"):
        _tool()._run("read_file", file_path="../../.env")
    with pytest.raises(ValueError, match="approved"):
        _tool()._run("run_command", command="pytest; curl https://example.com")


def test_tool_schema_requires_explicit_empty_values_for_groq_native_tools():
    required = GithubToolInput.model_json_schema()["required"]
    assert required == ["action", "file_path", "content", "command", "branch_name", "title", "body"]


def test_crew_uses_small_action_specific_tool_schemas_for_groq():
    tools = create_github_operation_tools(_tool())
    clone_schema = tools["clone"].args_schema.model_json_schema()
    assert clone_schema["required"] == ["confirm"]
    assert clone_schema["properties"]["confirm"]["type"] == "string"
    assert tools["read"].args_schema.model_json_schema()["required"] == ["file_path"]
    assert tools["write"].args_schema.model_json_schema()["required"] == ["file_path", "content"]
    assert tools["checks"].args_schema.model_json_schema()["required"] == ["command"]
    assert tools["pr"].args_schema.model_json_schema()["required"] == ["title", "body"]


def test_no_github_operation_tool_has_an_empty_property_schema():
    for tool in create_github_operation_tools(_tool()).values():
        schema = tool.args_schema.model_json_schema()
        assert "properties" in schema
        assert not ("required" in schema and not schema["properties"])


def test_docker_operations_are_isolated_and_do_not_place_token_in_arguments(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr("backend.tools.github_tool.subprocess.run", fake_run)
    result = _tool()._run("read_file", file_path="README.md")

    command = captured["command"]
    assert result == "ok"
    assert "--network" in command and command[command.index("--network") + 1] == "none"
    assert "--read-only" in command and "--cap-drop" in command
    assert "github_pat_not_a_real_token" not in command
    assert "GITHUB_PAT" not in captured["kwargs"]["env"]


def test_reports_docker_unavailable(monkeypatch):
    monkeypatch.setattr("backend.tools.github_tool.subprocess.run", lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()))
    with pytest.raises(SandboxUnavailable, match="Docker Desktop"):
        _tool()._run("list_files")
