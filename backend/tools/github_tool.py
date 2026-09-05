"""GitHub repository tool backed exclusively by a constrained Docker sandbox."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import PurePosixPath
from typing import Literal

import httpx
from crewai.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr


class SandboxUnavailable(RuntimeError):
    """Raised when Docker is unavailable or a sandbox operation cannot run."""


class GithubToolInput(BaseModel):
    action: Literal["clone", "list_files", "read_file", "write_file", "run_command", "push_branch", "create_pull_request"] = Field(description="The sandbox operation to perform.")
    # Groq native tools currently validate every declared property. Every call
    # must therefore include all fields and use an empty string when a field is
    # irrelevant to its selected action.
    file_path: str = Field(description="Relative path inside the cloned repository, or an empty string.")
    content: str = Field(description="Complete text content for write_file, or an empty string.")
    command: str = Field(description="One approved test or lint command, or an empty string.")
    branch_name: str = Field(description="agentforge/... branch name for push/PR, or an empty string.")
    title: str = Field(description="Pull request title, or an empty string.")
    body: str = Field(description="Pull request description, or an empty string.")


class GithubTool(BaseTool):
    """Operate a single session's repository without exposing its PAT to the agent."""

    name: str = "github_sandbox"
    description: str = (
        "Use the isolated GitHub sandbox. Clone first, then list_files/read_file/write_file, "
        "run an approved test command, push_branch, and create_pull_request. Credentials "
        "are configured outside the agent and must never be requested, displayed, or logged. "
        "EVERY call must include action, file_path, content, command, branch_name, title, and body; "
        "set every field not needed by that action to an empty string."
    )
    args_schema: type[BaseModel] = GithubToolInput
    _repo_url: str = PrivateAttr(default="")
    _token: str = PrivateAttr(default="")
    _session_id: str = PrivateAttr(default="")
    _image: str = PrivateAttr(default="agentforge-sandbox:local")

    def __init__(self, *, repo_url: str, token: str, session_id: str, image: str = "agentforge-sandbox:local", **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._repo_url = self._validate_repo_url(repo_url)
        if not token.strip():
            raise ValueError("A GitHub token is required for repository tasks.")
        if not re.fullmatch(r"[a-zA-Z0-9-]{8,80}", session_id):
            raise ValueError("Invalid sandbox session identifier.")
        self._token = token.strip()
        self._session_id = session_id
        self._image = image

    @property
    def volume_name(self) -> str:
        return f"agentforge-{self._session_id}"

    def _run(self, action: str, file_path: str = "", content: str = "", command: str = "", branch_name: str = "", title: str = "", body: str = "") -> str:
        if action == "clone":
            return self._clone(branch_name)
        if action == "list_files":
            return self._docker("cd /sandbox/repo && find . -path './.git' -prune -o -type f -print | sed 's#^./##' | sort")
        if action == "read_file":
            path = self._safe_path(file_path)
            return self._docker(f"cd /sandbox/repo && test -f {shlex.quote(path)} && cat -- {shlex.quote(path)}")
        if action == "write_file":
            path = self._safe_path(file_path)
            return self._docker(f"cd /sandbox/repo && mkdir -p -- {shlex.quote(str(PurePosixPath(path).parent))}; cat > {shlex.quote(path)}", input_text=content)
        if action == "run_command":
            self._validate_command(command)
            return self._docker(f"cd /sandbox/repo && {command}", timeout=90, allow_nonzero_exit=True)
        if action == "push_branch":
            return self._push_branch(branch_name)
        if action == "create_pull_request":
            return self._create_pull_request(branch_name, title, body)
        raise ValueError(f"Unknown sandbox action: {action}")

    # Credentials are supplied to git via GIT_ASKPASS (backed by the GITHUB_PAT
    # env var) rather than embedded in the remote URL. Docker only receives the
    # env var *name* on its command line (see _docker), so the token never
    # appears in the sandbox script text or in host/container process listings.
    _GIT_AUTH_ENV = {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/opt/agentforge/git-askpass",
    }

    def _clone(self, branch_name: str = "") -> str:
        # If this session already pushed a branch in an earlier query, continue
        # on it instead of restarting from the default branch. A blank or
        # not-yet-pushed branch_name is a no-op (silently falls through to the
        # default branch), so the first query in a session is unaffected.
        checkout_existing = ""
        if branch_name:
            branch = self._safe_branch(branch_name)
            checkout_existing = f"cd /sandbox/repo && (git fetch origin {shlex.quote(branch)} && git checkout {shlex.quote(branch)}) >/dev/null 2>&1 || true; "
        script = (
            'set -eu; '
            'find /sandbox/repo -mindepth 1 -delete 2>/dev/null || true; '
            f'git clone -- {shlex.quote(self._repo_url)} /sandbox/repo; '
            f'{checkout_existing}'
            'true'
        )
        return self._docker(script, network="bridge", secret_env={**self._GIT_AUTH_ENV, "GITHUB_PAT": self._token}, timeout=300)

    def _push_branch(self, branch_name: str) -> str:
        branch = self._safe_branch(branch_name)
        script = (
            f"set -eu; cd /sandbox/repo; "
            f"git remote set-url origin {shlex.quote(self._repo_url)}; "
            "git config user.email agentforge@local.invalid; git config user.name AgentForge; "
            f"git checkout -B {shlex.quote(branch)}; git add --all; "
            "git diff --cached --quiet && { echo 'No changes to push.'; exit 0; }; "
            "git commit -m 'AgentForge requested change'; git push --set-upstream origin HEAD"
        )
        return self._docker(script, network="bridge", secret_env={**self._GIT_AUTH_ENV, "GITHUB_PAT": self._token}, timeout=300)

    def _create_pull_request(self, branch_name: str, title: str, body: str) -> str:
        branch = self._safe_branch(branch_name)
        owner, repo = self._github_coordinates(self._repo_url)
        headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {self._token}"}
        response = httpx.post(
            f"https://api.github.com/repos/{owner}/{repo}/pulls",
            headers=headers,
            json={"title": title.strip() or "AgentForge change", "head": branch, "base": self._default_branch(owner, repo), "body": body.strip()},
            timeout=30,
        )
        if response.status_code == 422 and "already exists" in response.text.lower():
            # A later query on the same session's branch: the earlier query's
            # PR is still open and now carries this query's newly pushed
            # commits too, so surface that PR rather than failing.
            existing = httpx.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls",
                headers=headers,
                params={"head": f"{owner}:{branch}", "state": "open"},
                timeout=30,
            )
            if existing.status_code < 400 and existing.json():
                return f"Pushed new commits to the existing pull request: {existing.json()[0].get('html_url', '')}"
        if response.status_code >= 400:
            raise SandboxUnavailable(f"GitHub could not create the pull request ({response.status_code}): {response.text[:300]}")
        data = response.json()
        return f"Pull request created: {data.get('html_url', '')}"

    def _default_branch(self, owner: str, repo: str) -> str:
        response = httpx.get(f"https://api.github.com/repos/{owner}/{repo}", headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {self._token}"}, timeout=30)
        if response.status_code >= 400:
            raise SandboxUnavailable("GitHub could not read repository metadata. Check the token's repository access.")
        return str(response.json().get("default_branch") or "main")

    def cleanup(self) -> None:
        """Remove this session's Docker volume. Every action re-clones from
        scratch, so nothing of value is lost; state that matters lives on the
        pushed remote branch, not the local volume."""
        subprocess.run(["docker", "volume", "rm", "-f", self.volume_name], capture_output=True, text=True, timeout=30, check=False)

    def _ensure_volume(self) -> None:
        try:
            self._host_docker(["volume", "create", self.volume_name], timeout=30)
        except SandboxUnavailable:
            # Ignore error if volume already exists.
            pass
        # On Docker Desktop for Mac with VirtioFS, the volume mountpoint (/sandbox)
        # is always root-owned regardless of chmod. The workaround is to create the
        # /sandbox/repo subdirectory as root and chown it to sandboxuser (uid 10001).
        # Subdirectory ownership persists correctly across container runs.
        self._host_docker(
            ["run", "--rm", "--user", "0", "--network", "none", "-v", f"{self.volume_name}:/sandbox",
             self._image, "bash", "-c", "mkdir -p /sandbox/repo && chown -R 10001:10001 /sandbox/repo"],
            timeout=30,
        )

    def _docker(self, script: str, *, input_text: str | None = None, network: str = "none", secret_env: dict[str, str] | None = None, timeout: int = 60, allow_nonzero_exit: bool = False) -> str:
        self._ensure_volume()
        # We start as root to hand ownership of the mounted volume and tmpfs
        # home dir to sandboxuser, then drop into that user via su. Keep the
        # sandbox locked down otherwise: drop every capability except the four
        # that step requires (CHOWN/DAC_OVERRIDE to take/access the freshly
        # mounted root-owned paths, SETUID/SETGID for su to drop privileges).
        command = [
            "run", "--rm", "--user", "root", "--network", network, "--read-only",
            "--cap-drop", "ALL",
            "--cap-add", "CHOWN", "--cap-add", "DAC_OVERRIDE", "--cap-add", "SETUID", "--cap-add", "SETGID",
            "--pids-limit", "256", "--memory", "1g", "--cpus", "1",
            "-v", f"{self.volume_name}:/sandbox",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--tmpfs", "/home/sandboxuser:rw,noexec,nosuid,size=64m",
        ]
        if input_text is not None:
            command.append("-i")
        for key in (secret_env or {}):
            command.extend(["-e", key])

        # Ensure /sandbox/repo and the tmpfs home dir are owned by sandboxuser
        # (both are freshly mounted root-owned each run), then execute as sandboxuser.
        # A non-login `su` (no "-") is required: a login shell resets the
        # environment, dropping GIT_ASKPASS/GITHUB_PAT before git ever sees them.
        wrapped_script = (
            f"mkdir -p /sandbox/repo && chown -R 10001:10001 /sandbox/repo /home/sandboxuser && "
            f"HOME=/home/sandboxuser su sandboxuser -c {shlex.quote(script)}"
        )

        command.extend([self._image, "bash", "-lc", wrapped_script])
        return self._host_docker(command, input_text=input_text, secret_env=secret_env, timeout=timeout, allow_nonzero_exit=allow_nonzero_exit)

    @staticmethod
    def _host_docker(arguments: list[str], *, input_text: str | None = None, secret_env: dict[str, str] | None = None, timeout: int, allow_nonzero_exit: bool = False) -> str:
        try:
            environment = os.environ.copy()
            environment.update(secret_env or {})
            result = subprocess.run(["docker", *arguments], input=input_text, capture_output=True, text=True, timeout=timeout, check=False, env=environment)
        except FileNotFoundError as exc:
            raise SandboxUnavailable("Docker Desktop is not running. Start Docker Desktop and retry.") from exc
        except subprocess.TimeoutExpired as exc:
            raise SandboxUnavailable("The Docker sandbox timed out.") from exc
        if result.returncode != 0 and not allow_nonzero_exit:
            detail = (result.stderr or result.stdout or "").strip()[-800:]
            raise SandboxUnavailable(f"The Docker sandbox could not complete that operation: {detail}")
        output = (result.stdout or result.stderr).strip()
        if allow_nonzero_exit and result.returncode != 0:
            output = f"{output}\n[exit code {result.returncode}]"
        return output

    @staticmethod
    def _safe_path(file_path: str) -> str:
        path = PurePosixPath(file_path)
        if not file_path or path.is_absolute() or ".." in path.parts or ".git" in path.parts:
            raise ValueError("file_path must be a safe relative repository path.")
        return str(path)

    @staticmethod
    def _safe_branch(branch_name: str) -> str:
        if not re.fullmatch(r"agentforge/[a-zA-Z0-9._-]{1,60}", branch_name):
            raise ValueError("Branch names must begin with agentforge/ and use safe characters.")
        return branch_name

    @staticmethod
    def _validate_command(command: str) -> None:
        cleaned = command.strip()
        if any(character in cleaned for character in ";|&`$\n") or not cleaned:
            raise ValueError("Only one approved test or lint command may be run.")
        parts = shlex.split(cleaned)
        allowed = (["pytest"], ["python", "-m", "pytest"], ["python3", "-m", "pytest"], ["ruff", "check"], ["black", "--check"])
        if not any(parts[: len(prefix)] == prefix for prefix in allowed):
            raise ValueError("Command is not approved. Use pytest, ruff check, or black --check.")

    @staticmethod
    def _validate_repo_url(repo_url: str) -> str:
        value = repo_url.strip()
        if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?/?", value):
            raise ValueError("Repository URL must be an HTTPS github.com repository URL.")
        return value.rstrip("/")

    @staticmethod
    def _github_coordinates(repo_url: str) -> tuple[str, str]:
        match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?", repo_url)
        if not match:
            raise ValueError("Could not determine GitHub repository coordinates.")
        return match.group(1), match.group(2)


# Groq's native tool parser is considerably more reliable with small schemas.
# These adapters deliberately expose one operation per tool while the service
# retains the session-scoped repository URL, PAT, and branch convention.
class _ConfirmationInput(BaseModel):
    """Avoid an empty-object schema, which Groq rejects after CrewAI conversion."""

    confirm: str = Field(description="Set this to the exact string 'yes' to confirm this operation.")


class _FilePathInput(BaseModel):
    file_path: str = Field(description="A safe relative path inside the repository.")


class _WriteFileInput(BaseModel):
    file_path: str = Field(description="A safe relative path inside the repository.")
    content: str = Field(description="The complete replacement content for that file.")


class _CommandInput(BaseModel):
    command: str = Field(description="One approved command: pytest, python -m pytest, ruff check, or black --check.")


class _PullRequestInput(BaseModel):
    title: str = Field(description="A concise pull request title.")
    body: str = Field(description="A markdown pull request description.")


class _SandboxOperationTool(BaseTool):
    _service: GithubTool = PrivateAttr()

    def __init__(self, *, service: GithubTool, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._service = service


class CloneRepositoryTool(_SandboxOperationTool):
    name: str = "clone_repository"
    description: str = "Clone the configured GitHub repository into the isolated sandbox. Call this before any other repository operation with confirm='yes'."
    args_schema: type[BaseModel] = _ConfirmationInput

    def _run(self, confirm: str) -> str:
        _require_confirmation(confirm)
        return self._service._run("clone")


class ListRepositoryFilesTool(_SandboxOperationTool):
    name: str = "list_repository_files"
    description: str = "List non-git files in the already-cloned isolated repository. Call with confirm='yes'."
    args_schema: type[BaseModel] = _ConfirmationInput

    def _run(self, confirm: str) -> str:
        _require_confirmation(confirm)
        return self._service._run("list_files")


class ReadRepositoryFileTool(_SandboxOperationTool):
    name: str = "read_repository_file"
    description: str = "Read one safe relative file from the already-cloned isolated repository."
    args_schema: type[BaseModel] = _FilePathInput

    def _run(self, file_path: str) -> str:
        return self._service._run("read_file", file_path=file_path)


class WriteRepositoryFileTool(_SandboxOperationTool):
    name: str = "write_repository_file"
    description: str = "Write complete content to one safe relative file in the isolated repository."
    args_schema: type[BaseModel] = _WriteFileInput

    def _run(self, file_path: str, content: str) -> str:
        return self._service._run("write_file", file_path=file_path, content=content)


class RunRepositoryChecksTool(_SandboxOperationTool):
    name: str = "run_repository_checks"
    description: str = "Run one approved test or lint command in the isolated repository with network disabled."
    args_schema: type[BaseModel] = _CommandInput

    def _run(self, command: str) -> str:
        return self._service._run("run_command", command=command)


class PushRepositoryBranchTool(_SandboxOperationTool):
    name: str = "push_repository_branch"
    description: str = "Commit all reviewed changes and push the preconfigured agentforge branch. Call with confirm='yes'."
    args_schema: type[BaseModel] = _ConfirmationInput

    def _run(self, confirm: str) -> str:
        _require_confirmation(confirm)
        return self._service._run("push_branch", branch_name=self._service._safe_branch(f"agentforge/{self._service._session_id[:8]}"))


class CreatePullRequestTool(_SandboxOperationTool):
    name: str = "create_pull_request"
    description: str = "Open a pull request from the preconfigured pushed agentforge branch."
    args_schema: type[BaseModel] = _PullRequestInput

    def _run(self, title: str, body: str) -> str:
        branch = self._service._safe_branch(f"agentforge/{self._service._session_id[:8]}")
        return self._service._run("create_pull_request", branch_name=branch, title=title, body=body)


def create_github_operation_tools(service: GithubTool) -> dict[str, BaseTool]:
    """Return operation-specific wrappers for use in a CrewAI agent definition."""
    return {
        "clone": CloneRepositoryTool(service=service),
        "list": ListRepositoryFilesTool(service=service),
        "read": ReadRepositoryFileTool(service=service),
        "write": WriteRepositoryFileTool(service=service),
        "checks": RunRepositoryChecksTool(service=service),
        "push": PushRepositoryBranchTool(service=service),
        "pr": CreatePullRequestTool(service=service),
    }


def _require_confirmation(confirm: str) -> None:
    if confirm.strip().lower() != "yes":
        raise ValueError("Set confirm to 'yes' to run this repository operation.")
