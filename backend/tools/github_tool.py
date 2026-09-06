"""GitHub repository tool backed exclusively by a constrained Docker sandbox."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import PurePosixPath

import httpx


class SandboxUnavailable(RuntimeError):
    """Raised when Docker is unavailable or a sandbox operation cannot run."""


class GithubTool:
    """Operate a single session's repository without exposing its PAT to the agent."""

    def __init__(self, *, repo_url: str, token: str, session_id: str, image: str = "agentforge-sandbox:local") -> None:
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
            "git diff --cached --quiet && { echo 'No changes to push.'; echo '--- git status (including gitignored) ---'; git status --short --ignored; echo '--- git log -1 ---'; git log -1 --oneline; exit 0; }; "
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
