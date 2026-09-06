import subprocess

import pytest

from backend.tools.github_tool import GithubTool, SandboxUnavailable


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
