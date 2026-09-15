/**
 * All GitHub-repository functions live here, in two distinct groups:
 *
 * 1. Sandboxed, write-capable operations (cloneRepo, listFiles, readFile,
 *    writeFile, pushBranch) — anything that touches a real filesystem or
 *    could execute untrusted content runs inside a fresh, capped,
 *    network-locked Docker container (one `docker run --rm` per call)
 *    mounted to a volume shared across every container for the same
 *    session. Used only by the "change" GitHub sub-workflow.
 *
 * 2. Pure-read operations (fetchRepoTree, fetchFileContent) — plain
 *    authenticated calls straight to GitHub's REST API, no Docker, no
 *    clone, no session state at all. A read never writes anything, so
 *    there's no reason to pay for a sandbox just to look at a file. Used
 *    by the "read" GitHub sub-workflow.
 *
 * createPullRequest also talks to the REST API directly (opening a PR
 * isn't a filesystem operation either).
 */
import { spawn } from "node:child_process";
import { posix as posixPath } from "node:path";

const SANDBOX_IMAGE = process.env.SANDBOX_IMAGE || "agentforge-sandbox:local";

// GIT_ASKPASS points at the script baked into the image (docker/sandbox.Dockerfile)
// that answers git's username/password prompts from the GITHUB_PAT env var —
// the token itself never appears in a command string or on disk.
const GIT_AUTH_ENV = { GIT_TERMINAL_PROMPT: "0", GIT_ASKPASS: "/opt/agentforge/git-askpass" };

export class GithubUnavailable extends Error {}

function volumeName(sessionId) {
  return `agentforge-${sessionId}`;
}

/** POSIX shell single-quoting, since Node has no shlex.quote equivalent. */
function shQuote(value) {
  return `'${String(value).replaceAll("'", `'\\''`)}'`;
}

/**
 * Runs `docker <args>`, feeding secretEnv values into the *subprocess's*
 * environment (never into args itself) so a secret like GITHUB_PAT never
 * appears in a command line/process list — only `-e GITHUB_PAT` (the key,
 * no value) ever appears in the docker invocation itself.
 */
function hostDocker(args, { inputText, secretEnv = {}, timeoutMs = 60000, allowNonZeroExit = false } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn("docker", args, {
      env: { ...process.env, ...secretEnv },
    });

    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new GithubUnavailable(`docker ${args[0]} timed out after ${timeoutMs}ms`));
    }, timeoutMs);

    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.stderr.on("data", (chunk) => (stderr += chunk));

    child.on("error", (err) => {
      clearTimeout(timer);
      reject(new GithubUnavailable(`Could not run docker: ${err.message}`));
    });

    child.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0 && !allowNonZeroExit) {
        reject(new GithubUnavailable(stderr.trim() || `docker exited with code ${code}`));
        return;
      }
      resolve(stdout);
    });

    if (inputText !== undefined) {
      child.stdin.write(inputText);
    }
    child.stdin.end();
  });
}

/**
 * The volume is created once per session and reused by every container run
 * for that session — this is what lets a "clone" container's work survive
 * into a later, entirely separate "read_file" container.
 */
async function ensureVolume(sessionId) {
  try {
    await hostDocker(["volume", "create", volumeName(sessionId)]);
  } catch {
    // Already exists — fine, this is the common case after the first call.
  }
  // Docker Desktop mounts a fresh volume as root-owned regardless of chmod;
  // fix ownership once via a throwaway root container before real work runs.
  await hostDocker([
    "run", "--rm", "--user", "0", "--network", "none",
    "-v", `${volumeName(sessionId)}:/sandbox`,
    SANDBOX_IMAGE, "bash", "-c", "mkdir -p /sandbox/repo && chown -R 10001:10001 /sandbox/repo",
  ]);
}

/**
 * Runs one script inside a fresh, capped, non-root container mounted to
 * this session's volume. This is the one function every GitHub action goes
 * through — capped resources, no network unless explicitly requested,
 * read-only root filesystem, every capability dropped except the four
 * needed to hand the mounted volume to the unprivileged sandbox user.
 */
async function runInSandbox(sessionId, script, { network = "none", secretEnv = {}, timeoutMs = 60000, inputText, allowNonZeroExit = false } = {}) {
  await ensureVolume(sessionId);

  const wrappedScript =
    `mkdir -p /sandbox/repo && chown -R 10001:10001 /sandbox/repo /home/sandboxuser && ` +
    `HOME=/home/sandboxuser su sandboxuser -c ${shQuote(script)}`;

  const args = [
    "run", "--rm", "--user", "root", "--network", network, "--read-only",
    "--cap-drop", "ALL",
    "--cap-add", "CHOWN", "--cap-add", "DAC_OVERRIDE", "--cap-add", "SETUID", "--cap-add", "SETGID",
    "--pids-limit", "256", "--memory", "1g", "--cpus", "1",
    "-v", `${volumeName(sessionId)}:/sandbox`,
    "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
    "--tmpfs", "/home/sandboxuser:rw,noexec,nosuid,size=64m",
  ];
  if (inputText !== undefined) args.push("-i");
  for (const secretKey of Object.keys(secretEnv)) args.push("-e", secretKey);
  args.push(SANDBOX_IMAGE, "bash", "-lc", wrappedScript);

  return hostDocker(args, { inputText, secretEnv, timeoutMs, allowNonZeroExit });
}

function safePath(filePath) {
  const cleaned = String(filePath || "").trim();
  if (!cleaned || cleaned.startsWith("/") || cleaned.includes("..")) {
    throw new Error(`Unsafe file path: "${filePath}"`);
  }
  return cleaned;
}

function safeBranch(branch) {
  if (!/^[A-Za-z0-9._/-]{1,200}$/.test(branch)) {
    throw new Error(`Invalid branch name: "${branch}"`);
  }
  return branch;
}

export async function cloneRepo(sessionId, repoUrl, token, branch) {
  const safeBranchName = safeBranch(branch);
  const script =
    `set -eu; ` +
    `find /sandbox/repo -mindepth 1 -delete 2>/dev/null || true; ` +
    `git clone -- ${shQuote(repoUrl)} /sandbox/repo; ` +
    // Continue on this session's branch if a previous query in this same
    // session already pushed one; a not-yet-pushed branch silently falls
    // through to the repo's default branch instead.
    `cd /sandbox/repo && (git fetch origin ${shQuote(safeBranchName)} && git checkout ${shQuote(safeBranchName)}) >/dev/null 2>&1 || true; ` +
    `true`;
  return runInSandbox(sessionId, script, { network: "bridge", secretEnv: { ...GIT_AUTH_ENV, GITHUB_PAT: token }, timeoutMs: 300000 });
}

// Groq's free-tier TPM budget (8000 tokens/minute on the smaller models) is
// the tightest constraint in the whole fallback chain — an unbounded file
// read or listing can blow straight past it in one tool call, which is
// exactly what happened before these caps existed (a single read pushed one
// request to 22,106 tokens). Every candidate in the chain shares the same
// conversation, so this has to stay small regardless of which one answers.
const MAX_FILE_LISTING_ENTRIES = 200;
const MAX_FILE_READ_CHARS = 4000;

export async function listFiles(sessionId) {
  const raw = await runInSandbox(sessionId, "cd /sandbox/repo && find . -path './.git' -prune -o -type f -print | sed 's#^./##' | sort");
  const lines = raw.split("\n").filter(Boolean);
  if (lines.length <= MAX_FILE_LISTING_ENTRIES) return raw;
  return lines.slice(0, MAX_FILE_LISTING_ENTRIES).join("\n") + `\n...[truncated — ${lines.length - MAX_FILE_LISTING_ENTRIES} more files not shown]`;
}

export async function readFile(sessionId, filePath) {
  const path = safePath(filePath);
  const raw = await runInSandbox(sessionId, `cd /sandbox/repo && test -f ${shQuote(path)} && cat -- ${shQuote(path)}`);
  if (raw.length <= MAX_FILE_READ_CHARS) return raw;
  // The truncation marker matters as much as the cap itself — the agent
  // needs to know it did NOT see the whole file, rather than silently
  // reasoning from a partial read as if it were complete.
  return raw.slice(0, MAX_FILE_READ_CHARS) + `\n...[truncated — file is ${raw.length} characters, only the first ${MAX_FILE_READ_CHARS} shown]`;
}

export async function writeFile(sessionId, filePath, content) {
  const path = safePath(filePath);
  const parentDir = posixPath.dirname(path);
  const script = `cd /sandbox/repo && mkdir -p -- ${shQuote(parentDir)}; cat > ${shQuote(path)}`;
  await runInSandbox(sessionId, script, { inputText: content });
  return `Wrote ${path}.`;
}

export async function pushBranch(sessionId, repoUrl, token, branch) {
  const safeBranchName = safeBranch(branch);
  const script =
    `set -eu; cd /sandbox/repo; ` +
    `git remote set-url origin ${shQuote(repoUrl)}; ` +
    `git config user.email agentforge@local.invalid; git config user.name AgentForge; ` +
    `git checkout -B ${shQuote(safeBranchName)}; git add --all; ` +
    `git diff --cached --quiet && { echo 'No changes to push.'; exit 0; }; ` +
    `git commit -m 'AgentForge requested change'; git push --set-upstream origin HEAD`;
  return runInSandbox(sessionId, script, { network: "bridge", secretEnv: { ...GIT_AUTH_ENV, GITHUB_PAT: token }, timeoutMs: 300000 });
}

function githubCoordinates(repoUrl) {
  const match = repoUrl.match(/github\.com[/:]([\w.-]+)\/([\w.-]+?)(?:\.git)?\/?$/i);
  if (!match) throw new Error(`Could not parse a GitHub owner/repo from "${repoUrl}"`);
  return { owner: match[1], repo: match[2] };
}

async function defaultBranch(owner, repo, token) {
  const res = await fetch(`https://api.github.com/repos/${owner}/${repo}`, {
    headers: { Accept: "application/vnd.github+json", Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new GithubUnavailable("GitHub could not read repository metadata. Check the token's repository access.");
  const data = await res.json();
  return data.default_branch || "main";
}

/**
 * Opens the pull request. If one is already open for this branch (a later
 * query in the same session pushing more commits onto the same branch),
 * GitHub responds 422 "already exists" — caught here and turned into a
 * pointer at the existing PR instead of an error, so a repeated query in
 * one session never fails just because it isn't the first PR-opening call.
 */
export async function createPullRequest(repoUrl, token, branch, title, body) {
  const safeBranchName = safeBranch(branch);
  const { owner, repo } = githubCoordinates(repoUrl);
  const headers = { Accept: "application/vnd.github+json", Authorization: `Bearer ${token}` };

  const res = await fetch(`https://api.github.com/repos/${owner}/${repo}/pulls`, {
    method: "POST",
    headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify({
      title: title?.trim() || "AgentForge change",
      head: safeBranchName,
      base: await defaultBranch(owner, repo, token),
      body: body?.trim() || "",
    }),
  });

  if (res.status === 422) {
    const text = await res.text();
    if (text.toLowerCase().includes("already exists")) {
      const existing = await fetch(
        `https://api.github.com/repos/${owner}/${repo}/pulls?head=${owner}:${safeBranchName}&state=open`,
        { headers },
      );
      const existingData = existing.ok ? await existing.json() : [];
      if (existingData.length > 0) {
        return { url: existingData[0].html_url, message: `Pushed new commits to the existing pull request: ${existingData[0].html_url}` };
      }
    }
    throw new GithubUnavailable(`GitHub could not create the pull request (422): ${text.slice(0, 300)}`);
  }

  if (!res.ok) {
    throw new GithubUnavailable(`GitHub could not create the pull request (${res.status}): ${(await res.text()).slice(0, 300)}`);
  }

  const data = await res.json();
  return { url: data.html_url, message: `Pull request created: ${data.html_url}` };
}

/**
 * Removes this session's volume entirely — safe because every action
 * re-clones from scratch, and anything that matters (the pushed branch)
 * already lives on GitHub's servers, not in this scratch volume.
 */
export async function removeSessionVolume(sessionId) {
  await hostDocker(["volume", "rm", "-f", volumeName(sessionId)], { allowNonZeroExit: true });
}

/**
 * Pure-read operations, on GitHub's own REST API — no Docker, no clone, no
 * volume, no session at all. A read never needs to write anything, so
 * there's no reason to pay for a sandboxed container or persistent storage
 * just to look at a file; these are simple authenticated HTTP calls.
 */
export async function fetchRepoTree(repoUrl, token, ref) {
  const { owner, repo } = githubCoordinates(repoUrl);
  const branch = ref || (await defaultBranch(owner, repo, token));
  const headers = { Accept: "application/vnd.github+json", Authorization: `Bearer ${token}` };
  const res = await fetch(`https://api.github.com/repos/${owner}/${repo}/git/trees/${encodeURIComponent(branch)}?recursive=1`, { headers });
  if (!res.ok) throw new GithubUnavailable(`GitHub could not read the repository tree (${res.status}).`);
  const data = await res.json();
  return (data.tree || []).filter((item) => item.type === "blob").map((item) => item.path);
}

export async function fetchFileContent(repoUrl, token, filePath, ref) {
  const path = safePath(filePath);
  const { owner, repo } = githubCoordinates(repoUrl);
  const headers = { Accept: "application/vnd.github+json", Authorization: `Bearer ${token}` };
  const query = ref ? `?ref=${encodeURIComponent(ref)}` : "";
  const res = await fetch(`https://api.github.com/repos/${owner}/${repo}/contents/${path.split("/").map(encodeURIComponent).join("/")}${query}`, { headers });
  if (!res.ok) throw new GithubUnavailable(`GitHub could not read "${path}" (${res.status}).`);
  const data = await res.json();
  if (Array.isArray(data)) throw new GithubUnavailable(`"${path}" is a directory, not a file.`);
  if (data.encoding !== "base64") throw new GithubUnavailable(`Unexpected encoding for "${path}".`);
  return Buffer.from(data.content, "base64").toString("utf-8");
}
