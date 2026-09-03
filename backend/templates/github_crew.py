"""Custom Python agent loop for GitHub repository tasks – no CrewAI required.

The flow is intentionally deterministic:
  1. Clone the repo and list all files (no LLM).
  2. Read the 5 most relevant files, capped at ~400 chars each (no LLM).
  3. Call the LLM once with the context and ask for a JSON diff.
  4. Apply the file writes, push the branch, and create the PR (no LLM).

The total prompt is kept well under 4,000 tokens to stay within Groq's
free-tier 8,000 TPM limit.
"""

from __future__ import annotations

import json
import re

import litellm

from backend.config import Settings
from backend.tools.github_tool import GithubTool, SandboxUnavailable


def run_github_workflow(
    user_request: str,
    repo_url: str,
    token: str,
    session_id: str,
    settings: Settings,
) -> str:
    """Run the full GitHub workflow and return a human-readable result string."""
    branch = f"agentforge/{session_id[:8]}"
    github = GithubTool(
        repo_url=repo_url,
        token=token,
        session_id=session_id,
        image=settings.sandbox_image,
    )

    # Phase 1: Explore (no LLM calls)
    github._run("clone")
    all_files_raw = github._run("list_files")
    all_files = [f.strip() for f in all_files_raw.splitlines() if f.strip()]

    # Read only 5 files most likely to be relevant to the request.
    # Prefer root-level files and common code/doc/config extensions.
    priority_ext = {".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".md", ".toml", ".yaml", ".yml", ".txt", ".html", ".css"}
    sorted_files = sorted(
        all_files,
        key=lambda f: (len(f.split("/")), 0 if any(f.endswith(e) for e in priority_ext) else 1),
    )
    files_to_read = sorted_files[:5]
    file_contents: dict[str, str] = {}
    for fpath in files_to_read:
        try:
            # Cap each file at 400 chars to keep total prompt small
            raw = github._run("read_file", file_path=fpath)
            file_contents[fpath] = raw[:400] + ("...[truncated]" if len(raw) > 400 else "")
        except (SandboxUnavailable, ValueError):
            pass

    # Phase 2: Plan (single LLM call, tight context)
    file_list_block = "\n".join(all_files[:50])  # cap the listing at 50 entries
    context_block = _build_context_block(file_contents)

    system_prompt = (
        "You are an expert software engineer. Given a repo file listing and some file samples, "
        "produce ONLY a valid JSON object — no markdown, no extra text — in this exact format:\n"
        '{"summary":"<one sentence>","files":[{"path":"<relative/path>","content":"<full new content>"}],'
        '"pr_title":"<title>","pr_body":"<body>"}\n\n'
        "Rules: use safe relative paths only, provide COMPLETE file content (not diffs), "
        "never include credentials."
    )

    user_message = (
        f"Request: {user_request}\n\n"
        f"Files in repo:\n{file_list_block}\n\n"
        f"Sample file contents:\n{context_block}"
    )

    response = litellm.completion(
        model=f"groq/{settings.groq_model}",
        api_key=settings.groq_api_key,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=0.1,
        max_tokens=2048,
    )

    raw = response.choices[0].message.content or ""
    plan = _parse_json_plan(raw)

    if not plan or not plan.get("files"):
        return f"The agent could not determine what changes to make.\n\nAgent response:\n{raw}"

    # Phase 3: Apply (no LLM calls)
    written: list[str] = []
    for file_entry in plan["files"]:
        fpath = file_entry.get("path", "").strip()
        content = file_entry.get("content", "")
        if not fpath:
            continue
        github._run("write_file", file_path=fpath, content=content)
        written.append(fpath)

    if not written:
        return "The agent produced a plan but made no file changes."

    push_result = github._run("push_branch", branch_name=branch)
    if "No changes to push" in push_result:
        return "No changes were needed – the repository already satisfies the request."

    pr_title = plan.get("pr_title") or "AgentForge requested change"
    pr_body = plan.get("pr_body") or f"Changes requested: {user_request}"
    pr_result = github._run("create_pull_request", branch_name=branch, title=pr_title, body=pr_body)

    return (
        f"{plan.get('summary', 'Changes applied successfully.')}\n\n"
        f"Files changed: {', '.join(written)}\n\n"
        f"{pr_result}"
    )


def _build_context_block(file_contents: dict[str, str]) -> str:
    parts: list[str] = []
    for path, content in file_contents.items():
        parts.append(f"--- {path} ---\n{content}")
    return "\n\n".join(parts)


def _parse_json_plan(raw: str) -> dict | None:
    """Extract a JSON object from the LLM response, tolerating markdown fences."""
    stripped = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped.strip())
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return None
