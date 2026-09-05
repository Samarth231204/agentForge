# Phase 2 — GitHub Integration + Docker Sandbox

## Pipeline

```
Streamlit sidebar (repo URL, PAT, "Start a new branch" checkbox)
        │
        ▼
POST /tasks  { prompt, repo_url, github_token, session_id }
        │
        ▼
IntentParser → intent = "github"
        │
        ▼
CrewEngine._run_github()
        │
        ▼
run_github_workflow(session_id)
   session_id → branch = "agentforge/<8 chars>"
        │
        ▼
GithubTool  (per-call `docker run` into agentforge-sandbox:local)
   1. clone        – fresh clone; checks out this session's branch if it
                      already exists remotely, else the default branch
   2. list_files   – repo file tree (no LLM)
   3. read_file ×5 – sample the most relevant files (no LLM)
   4. [ 1 Groq LLM call ]  → JSON plan: {files[], pr_title, pr_body}
   5. write_file × N       – apply the plan (no LLM)
   6. push_branch          – commit + push (no-op if nothing changed)
   7. create_pull_request  – open PR, or report the existing open PR

   (run_command exists — pytest/ruff/black, output returned either way —
    but the pipeline above doesn't call it automatically; see §6/§7)
        │
        ▼
GithubTool.cleanup() – always removes the Docker volume (finally block)
        │
        ▼
Events streamed back to Streamlit; result rendered in the activity log
```

## Summary

A GitHub task is handled by a small, deterministic Python pipeline (not a CrewAI multi-agent loop) that makes exactly one LLM call per query, keeping token usage low. Every git and file operation happens inside a locked-down, network-restricted Docker container (`agentforge-sandbox:local`): the container runs as root only long enough to `chown` the mounted volume to an unprivileged `sandboxuser`, then drops into that user to actually run git/pytest/etc. The container has no host filesystem access, a capped memory/CPU/PID budget, and only the four Linux capabilities needed to hand off ownership and drop privileges (`CHOWN`, `DAC_OVERRIDE`, `SETUID`, `SETGID`) — everything else is dropped.

The GitHub PAT never touches the sandbox's command-line arguments. It's passed to `docker run` as an environment variable name only (`-e GITHUB_PAT`), with the actual value supplied through the host process's environment; git picks it up via a `GIT_ASKPASS` helper baked into the image. This keeps the token out of both host and container process listings (`ps aux`).

Each browser session gets a stable `session_id`, generated once in Streamlit and reused across queries. That id determines the branch name (`agentforge/<session_id[:8]>`) and the Docker volume name. Because every query re-clones from scratch anyway, the volume holds no state worth keeping between queries — so it's deleted after every task, and continuity is achieved purely by having `clone` check out the session's branch from the remote if it already exists. This means repeated queries in one session keep committing to the same branch and the same pull request (new commits get pushed to the existing PR instead of erroring); checking "Start a new branch" in the sidebar (or changing the repo URL) generates a fresh `session_id` and starts a clean branch.

## Full walkthrough — every file, in order

This traces one GitHub request through the entire stack, file by file, exactly as the code executes.

### 1. Frontend — the browser session (`frontend/`)

- **`utils/state.py`** — On first page load, `initialize_state()` seeds `st.session_state` with defaults, including `github_repo_url`, `github_token`, `github_session_id` (starts `""`), and `github_session_repo`.
- **`components/sidebar.py`** (`render_sidebar()`) — Renders the Backend URL field, the Repository URL and PAT inputs (inside a "GitHub repository access" expander, PAT masked with `type="password"`), the **"Start a new branch"** checkbox, and a "Reset session" button. Returns `(backend_url, repo_url, github_token, reset, new_branch)`.
- **`app.py`** (top level, runs on every Streamlit interaction):
  1. Calls `render_sidebar()`; if "Reset session" was clicked, calls `reset_state()` and reruns.
  2. `render_chat_input()` collects the prompt and optional context, returning `submitted` once the user clicks run.
  3. On submit: strips the repo URL, then decides whether to keep or replace `st.session_state.github_session_id` — replaced only if the "Start a new branch" checkbox was checked, or no session id exists yet, or the repo URL changed since the id was created (`github_session_repo` mismatch). A new id is `uuid.uuid4().hex` (32 hex chars).
  4. Calls `stream_task(backend_url, prompt, context, repo_url, github_token, session_id)`.
- **`utils/sse_client.py`** (`stream_task`) — Does a single blocking `POST {backend_url}/tasks` with JSON body `{prompt, context, repo_url, github_token, session_id}`, then yields every event object from the JSON response's `events` list. (The backend also exposes a true SSE endpoint, `/tasks/stream`, but the Streamlit UI uses the buffered JSON one for local reliability — see `main.py` below.)
- Back in `app.py`, each yielded event is appended to `st.session_state.events` and rendered live via `components/agent_graph.py` (workflow/agent status) and `components/event_log.py` (chronological log); a `result`-type event's `data` becomes the final displayed answer.

### 2. Backend entry — FastAPI (`backend/main.py`)

- `TaskRequest` (Pydantic model) validates the incoming JSON: `prompt` (1–4000 chars, required), `context`, `repo_url`, `github_token`, `session_id` (all optional, capped lengths).
- `POST /tasks` (the endpoint Streamlit actually calls): creates an `EventEmitter` with a fresh, request-scoped `task_id = uuid.uuid4()` (this is *not* the same as the frontend's `session_id` — the task id identifies this one HTTP call/event stream; the session id identifies the GitHub branch/volume across multiple calls). Emits `task_started`, then calls `_run_task(...)` synchronously, then drains the emitter's queue into a JSON list and returns `{"events": [...]}`.
- `_run_task()`:
  1. `IntentParser().parse(prompt, context)` → classifies the request.
  2. Emits `intent_detected`.
  3. Calls `CrewEngine().run(intent, prompt, context, emitter, repo_url=..., github_token=..., session_id=...)`.
  4. Emits `result`, then `task_completed` (`status: "completed"` or `"failed"` — any exception is caught here, logged server-side with the task id, and turned into a generic `error` event so nothing internal, especially not the PAT, ever reaches the UI).
  5. `finally: emitter.close()`.

### 3. Intent classification (`backend/intent_parser.py`)

- `IntentParser.parse()` first checks `_forced_intent()` — a fast keyword override. If the prompt contains any of `"github", "pull request", "clone repo", "repository", "repo ", "repo."`, it immediately returns `intent="github"` with confidence 0.98, **without calling the LLM at all**. (Only prompts that don't match any forced keyword fall through to an actual Groq classification call using `GROQ_MODEL` from `.env`.)

### 4. Dispatch (`backend/crew_engine.py`)

- `CrewEngine.run()` switches on `intent.intent`. For `"github"`, it calls `_run_github(task_summary, prompt, repo_url, github_token, session_id, emitter)`.
- `_run_github()`:
  1. If `repo_url` or `github_token` is blank, returns immediately with a message asking the user to fill in the sidebar — no Docker, no LLM call.
  2. Emits `workflow_started` (`workflow: "github_crew"`, the four cosmetic stage names `Code Reader / Software Engineer / QA Engineer / PR Manager`) and one `agent_started` per stage — these are purely UI/event-log labels; the actual work underneath is the single deterministic function below, not four separate LLM-driven agents.
  3. Computes `github_session_id = session_id or emitter.task_id` (falls back to the task id only for callers that never supply a session id — e.g. direct API tests).
  4. Calls `run_github_workflow(prompt, repo_url, github_token, github_session_id, self.settings)` — this is where all the real work happens (§5–6 below).
  5. Emits one `agent_completed` per stage, then returns `{"intent": "github", "content": <output string>, "sources": []}`.

### 5. The workflow itself (`backend/templates/github_crew.py`)

`run_github_workflow()` is a plain deterministic function — **not** a CrewAI multi-agent crew, despite the four "agent" labels emitted above. It makes exactly one LLM call, keeping token usage low enough for Groq's free tier.

1. `branch = f"agentforge/{session_id[:8]}"` — the branch name is fully determined by the session id.
2. Constructs one `GithubTool(repo_url, token, session_id, image=settings.sandbox_image)` (`settings.sandbox_image` defaults to `agentforge-sandbox:local`, overridable via `SANDBOX_IMAGE` in `.env`).
3. Runs `_run_workflow(...)` inside a `try`, with `github.cleanup()` in the `finally` — the Docker volume is removed no matter how the task ends.
4. Inside `_run_workflow` (no LLM yet):
   - `github._run("clone", branch_name=branch)` — see §6 for what this does inside Docker.
   - `github._run("list_files")` → full repo file listing.
   - Picks the 5 files most likely to be relevant (root-level and common source/doc/config extensions ranked first) and reads each via `github._run("read_file", ...)`, truncated to 400 characters apiece to keep the prompt small.
5. **The one LLM call**: builds a system prompt instructing the model to return *only* JSON — `{"summary", "files": [{"path", "content"}], "pr_title", "pr_body"}` — with the repo's file list (capped at 50 entries) and the sampled file contents as user content. Calls `litellm.completion(model=f"groq/{settings.groq_model}", api_key=settings.groq_api_key, ...)`.
6. `_parse_json_plan()` strips markdown code fences if present and parses the JSON; if that fails, regex-extracts the first `{...}` block and retries. If no usable plan or no `files` come back, the function returns early with the raw model output shown to the user — no writes, no push.
7. Applying the plan (no LLM): for each `{path, content}` entry, calls `github._run("write_file", file_path=path, content=content)`.
8. `github._run("push_branch", branch_name=branch)` — commits and pushes (§6). If nothing changed, returns early with "No changes were needed."
9. `github._run("create_pull_request", branch_name=branch, title=plan.pr_title, body=plan.pr_body)` — opens the PR, or (if one is already open on this branch from an earlier query in the same session) reports that PR's URL instead of erroring.
10. Returns a human-readable summary string combining the plan's summary, the list of changed files, and the PR result — this is what flows back up as the `result` event's `content`.

Note: `GithubTool` also implements a `run_command` action (§6) — restricted to `pytest`, `python -m pytest`, `python3 -m pytest`, `ruff check`, or `black --check`, and returns its output either way rather than raising on a normal test failure. It exists and is exercised by the test suite (`tests/test_github_tool.py`), but the current deterministic `_run_workflow` above never calls it — there's no automatic test/lint step between writing files and pushing. That's a gap worth closing later, not a claim that it already runs today.

### 6. Inside the sandbox (`backend/tools/github_tool.py`)

Every `GithubTool._run(action, ...)` call ultimately shells out to `docker run` via `_docker()`/`_host_docker()`. Key mechanics, per action:

- **Volume**: `volume_name = f"agentforge-{session_id}"`. `_ensure_volume()` creates it if missing, then runs one throwaway root container to `mkdir -p /sandbox/repo && chown -R 10001:10001 /sandbox/repo` (works around Docker Desktop/VirtioFS always mounting the volume root as root-owned).
- **Container hardening** (every `_docker()` call): `--rm --user root --network <none|bridge> --read-only --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add SETUID --cap-add SETGID --pids-limit 256 --memory 1g --cpus 1`, plus `--tmpfs /tmp` and `--tmpfs /home/sandboxuser` (both `noexec,nosuid`, capped at 64MB). Network is `"none"` for every action except `clone` and `push_branch`, which need `"bridge"` to reach GitHub.
- **Privilege drop**: inside the container, `mkdir -p /sandbox/repo && chown -R 10001:10001 /sandbox/repo /home/sandboxuser && HOME=/home/sandboxuser su sandboxuser -c "<script>"` — root only exists to hand ownership to uid 10001 (`sandboxuser`), then a **non-login** `su` drops into it. (A login `su -` would both `cd` into the tmpfs home dir instead of `/sandbox/repo` and reset the environment, breaking auth — see bugs #3–4 below.)
- **Credentials**: the token is never written into any script text or URL. `secret_env = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/opt/agentforge/git-askpass", "GITHUB_PAT": token}` is passed to `docker run` as `-e KEY` (name only); the actual value comes from the host Python process's own environment (`_host_docker` copies `os.environ` and overlays `secret_env` onto it just for that subprocess call). Inside the container, git calls `/opt/agentforge/git-askpass` (baked into `docker/sandbox.Dockerfile`) whenever it needs credentials; that script answers `x-access-token` for a username prompt and `$GITHUB_PAT` for a password prompt.
- **`clone`**: wipes `/sandbox/repo`, clones `repo_url` fresh, then — if a `branch_name` was supplied — silently attempts `git fetch origin <branch> && git checkout <branch>` (output redirected to `/dev/null`, always followed by `|| true`) so a session's second-or-later query resumes on its own branch instead of the default branch; a first-ever query for that session just falls through cleanly since the branch doesn't exist yet.
- **`list_files` / `read_file` / `write_file` / `run_command`**: each explicitly `cd /sandbox/repo &&` before doing anything, since the sandbox user's `$PWD` after `su` is not automatically the repo directory. `read_file`/`write_file` reject absolute paths, `..` segments, or anything touching `.git` (`_safe_path`). `run_command` rejects shell metacharacters and only allows the exact prefixes `pytest`, `python -m pytest`, `python3 -m pytest`, `ruff check`, `black --check` (`_validate_command`), and — uniquely among actions — passes `allow_nonzero_exit=True` so a failing test returns its output (tagged `[exit code N]`) instead of raising.
- **`push_branch`**: sets `origin` back to the plain `repo_url` (no embedded token), configures a synthetic git identity (`agentforge@local.invalid` / `AgentForge`), `git checkout -B <branch>`, stages everything, and either exits early with "No changes to push." (clean diff) or commits and `git push --set-upstream origin HEAD`.
- **`create_pull_request`**: not a Docker call — a direct `httpx` call from the host process to the GitHub REST API (`POST /repos/{owner}/{repo}/pulls`) using the same PAT as a Bearer token, with `base` looked up via `GET /repos/{owner}/{repo}` (its `default_branch` field). A `422` containing "already exists" is treated as success: it looks up the existing open PR for that branch via `GET .../pulls?head=owner:branch&state=open` and reports that PR's URL instead of failing.
- **`cleanup()`**: runs `docker volume rm -f <volume_name>` unconditionally after the workflow finishes (success or failure) — safe because every action above always starts by re-cloning, so the volume never holds anything that isn't already on the remote branch.
- **Any non-zero `docker run` exit** (outside the `run_command` case above) raises `SandboxUnavailable` with the last ~800 characters of the container's `stderr`/`stdout` attached, which is what surfaces as the `task_failed` error's server-side log (never shown raw to the browser — `main.py`'s `_run_task` catches it and emits a generic user-facing message).

### 7. What happens after the pipeline finishes

The function above returns a plain string; nothing merges anything automatically. The end state on GitHub is a **pushed branch plus an open pull request** — merging into the repository's default branch is always a manual, human action taken later in the GitHub UI (or via `gh pr merge`), by design: it's the review gate before an LLM-authored change lands on the default branch. Back on the AgentForge side, that string becomes the `result` event's `content`, which `crew_engine.py` wraps and `main.py` streams back as the final JSON event; Streamlit's `app.py` renders it as the "Result" panel.

## Issues found and changes made (chronological)

1. **Push crashed with `SandboxUnavailable`.** The PAT was embedded as the URL *username* (`https://TOKEN@github.com/...`). GitHub then demanded a password, git had no tty to prompt on, and the push failed outright.
2. **Token leaked into process listings.** The fix in progress for #1 had embedded the raw PAT directly into the shell script passed as a `docker run ... bash -lc "<script>"` argument — visible via `ps aux` on the host while the command ran. Restored the pre-existing `GIT_ASKPASS` + `GITHUB_PAT` env-var mechanism instead, which only ever exposes the *name* of the env var on the command line.
3. **Silent write loss (the real root cause of "no changes to push").** `su - sandboxuser` runs a login shell, which `cd`s into `/home/sandboxuser` — an ephemeral tmpfs wiped every container run. Every `write_file`/`read_file`/`list_files`/`run_command` was silently operating there instead of `/sandbox/repo`, so agent writes never reached the actual git checkout. Fixed by explicitly `cd`-ing into `/sandbox/repo` in each script.
4. **Env vars dropped before git ever saw them.** The same login shell (`su -`) resets the environment, so `GIT_ASKPASS`/`GITHUB_PAT` never reached the git process even after re-enabling them. Switched to a non-login `su` (no `-`) with `HOME` set explicitly.
5. **Sandbox hardening regression.** An earlier fix for the `chown` permission problem had removed `--cap-drop ALL` entirely to let root run `chown`. Restored full capability dropping, adding back only the four capabilities actually required (`CHOWN`, `DAC_OVERRIDE`, `SETUID`, `SETGID`).
6. **`run_command` crashed on any failing test.** A normal non-zero pytest exit (e.g. real assertion failures, or "no tests collected") was raised as a fatal `SandboxUnavailable`, killing the whole workflow instead of surfacing the test output. Changed to always return output, tagged with `[exit code N]` on failure.
7. **Opaque sandbox errors.** `SandboxUnavailable` discarded `stderr`, forcing manual reproduction scripts to diagnose anything. Now includes the last ~800 chars of `stderr`/`stdout` in the raised message (safe now that the token never appears in it).
8. **Leaked credential in scratch files.** Debug/test scripts written while diagnosing the above had a live PAT hardcoded in plaintext. Confirmed it was already revoked, then deleted all the throwaway debug/test scripts (and a couple of stray empty artifacts left behind by one of them).
9. **First live frontend test rejected: "Invalid username or token."** The auth mechanism itself now worked correctly and reached GitHub cleanly, but the specific PAT being tested had expired. Fixed by regenerating a fresh fine-grained PAT.
10. **Second live frontend test rejected: `403 Permission denied`.** The new PAT authenticated fine but lacked write access to the target repo. Fixed by setting the correct scopes on the fine-grained PAT: **Contents: Read and write** and **Pull requests: Read and write**, both explicitly granted on the selected repository (Metadata: Read-only comes along automatically; no Account-level permissions are needed). The workflow then completed a full clone → write → push → PR cycle successfully.
11. **Every query created a brand-new branch and PR.** By design at the time, `session_id` was the per-request task id, so no two queries ever shared a branch. Added a persistent, frontend-generated `session_id` (stored in Streamlit session state, reused across queries against the same repo) plus clone-time checkout of that branch if it already exists remotely, so repeated queries build on the same branch by default. A "Start a new branch" sidebar checkbox (or switching the repo URL) generates a fresh `session_id` to opt out.
12. **Duplicate-PR error on a second query to the same branch.** Once branches persist across queries, opening a PR a second time hits GitHub's "already exists" `422`. Now detected and handled by looking up and returning the existing open PR instead of failing.
13. **Docker volumes accumulating on disk.** Volumes were never removed after a task (only the container was `--rm`'d). Added `GithubTool.cleanup()`, called in a `finally` block after every workflow run, since no query-to-query state actually depends on the volume surviving.
